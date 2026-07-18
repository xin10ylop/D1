"""Systematic strategy battery over the full panels.

Honest execution: signal computed on bar t, entry at bar t+1 quotes (next 15-min
grid point), taker fee on entry; hold-to-settlement unless stated. Hedged EV
subtracts digital-delta x realized underlying log-return (alpha isolation).
Train: expiry < SPLIT. Test: expiry >= SPLIT (== current fee regime).
"""
import sys
import numpy as np
import pandas as pd
from scipy.stats import norm
sys.path.insert(0, ".")
from evaluate import (load_panel, fee, dedup_trades, ev_yes_taker, ev_no_taker,
                      digital_delta_dollar, log_result, FEE_RATE)

SPLIT = pd.Timestamp("2026-05-16", tz="UTC")


def prep(asset):
    p = load_panel(asset)
    p = p.sort_values(["slug", "ts"]).reset_index(drop=True)
    g = p.groupby("slug")
    for c in ["bid", "ask", "bid_sz", "ask_sz", "mid"]:
        p["n_" + c] = g[c].shift(-1)
    p["n_ts"] = g["ts"].shift(-1)
    # only allow next-bar entry when the next bar is within 30 min
    ok = (p["n_ts"] - p["ts"]) <= 1800
    for c in ["n_bid", "n_ask", "n_bid_sz", "n_ask_sz", "n_mid"]:
        p.loc[~ok, c] = np.nan
    p["exp_dt"] = pd.to_datetime(p["T_pm"], unit="s", utc=True)
    p["is_test"] = p["exp_dt"] >= SPLIT
    p["event"] = p["exp_dt"].dt.date
    p["gap_yes"] = p["fv"] - p["ask"]          # buy YES edge (gross)
    p["gap_no"] = p["bid"] - p["fv"]           # buy NO edge (gross)
    p["delta"] = p.apply(digital_delta_dollar, axis=1)
    p["ret_T"] = np.log(p["S_T"] / p["S_bin"])
    return p


def eval_side(df, side, entry_col):
    """Returns per-trade frame with raw and hedged EV at next-bar entry."""
    d = df.dropna(subset=[entry_col, "S_T"]).copy()
    if side == "yes":
        px = d[entry_col]
        d["ev_raw"] = d.label - px - fee(px)
        d["ev_hedged"] = d.ev_raw - d.delta * d.ret_T
        d["entry"] = px
    else:
        px = 1 - d[entry_col]
        d["ev_raw"] = (1 - d.label) - px - fee(px)
        d["ev_hedged"] = d.ev_raw + d.delta * d.ret_T
        d["entry"] = px
    return d


def clustered_stats(d, ev_col="ev_hedged"):
    """Mean EV with event-clustered SE."""
    if len(d) == 0:
        return {}
    ev = d[ev_col]
    g = d.groupby(["asset", "event"])[ev_col].mean()
    se = g.std() / max(np.sqrt(len(g)), 1)
    return {"n": len(d), "n_events": len(g), "ev": ev.mean(), "ev_ev": g.mean(),
            "se": se, "t": g.mean() / se if se > 0 else np.nan,
            "hit": d.label.mean() if "label" in d else np.nan}


def battery_thresholds(panels, sample="train"):
    """A-family: threshold rules, both sides, taker at next bar."""
    rows = []
    allp = pd.concat(panels, ignore_index=True)
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    m = m[(m.ask > 0.02) & (m.ask < 0.98) & (m.spread <= 0.05)]
    for side, gapc, entryc in [("yes", "gap_yes", "n_ask"), ("no", "gap_no", "n_bid")]:
        for thr in [0.01, 0.02, 0.03, 0.05]:
            for tte_lo, tte_hi in [(0, 8), (0, 1), (1, 3), (3, 8)]:
                sel = m[(m[gapc] > thr) & (m.tte_d >= tte_lo) & (m.tte_d < tte_hi)]
                sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
                d = eval_side(sel, side, entryc)
                st = clustered_stats(d)
                if not st or st["n"] < 5:
                    continue
                rows.append(dict(family="threshold_taker", side=side, thr=thr,
                                 tte=f"{tte_lo}-{tte_hi}", sample=sample, **st))
    return pd.DataFrame(rows)


def battery_maker(panels, sample="train", horizon_bars=8):
    """Maker variant: rest a YES bid at (best_bid + 0.01) when gap vs FV is wide.

    Fill rule (conservative): filled only if a later bar within horizon has
    ask <= limit (book traded through the limit). Fee = 0. EV to settlement.
    """
    rows = []
    allp = pd.concat(panels, ignore_index=True)
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    m = m[(m.ask > 0.02) & (m.ask < 0.98)]
    bys = {s: g.sort_values("ts").reset_index(drop=True) for s, g in allp.groupby("slug")}
    for side in ["yes", "no"]:
        for thr in [0.01, 0.02, 0.03]:
            if side == "yes":
                sel = m[m.fv - (m.bid + 0.01) - 0.0 > thr]  # edge at our limit price
            else:
                sel = m[(1 - m.ask + 0.01) - (1 - m.fv) > thr]
            sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
            trades = []
            for r in sel.itertuples():
                gs = bys.get(r.slug)
                g = gs[gs.ts > r.ts].head(horizon_bars) if gs is not None else pd.DataFrame()
                if len(g) == 0:
                    continue
                if side == "yes":
                    lim = r.bid + 0.01
                    fill = g[g.ask <= lim]
                    if len(fill) == 0:
                        continue
                    f0 = fill.iloc[0]
                    ev_raw = r.label - lim
                    ev_h = ev_raw - r.delta * r.ret_T
                else:
                    lim_no = (1 - r.ask) + 0.01  # our NO bid; YES ask equivalent = 1-lim_no
                    fill = g[g.bid >= 1 - lim_no]
                    if len(fill) == 0:
                        continue
                    ev_raw = (1 - r.label) - lim_no
                    ev_h = ev_raw + r.delta * r.ret_T
                trades.append(dict(asset=r.asset, event=r.event, slug=r.slug,
                                   ev_raw=ev_raw, ev_hedged=ev_h, label=r.label))
            d = pd.DataFrame(trades)
            if len(d) < 5:
                continue
            st = clustered_stats(d)
            st["fill_rate"] = len(d) / max(len(sel), 1)
            rows.append(dict(family="maker_rest", side=side, thr=thr, sample=sample, **st))
    return pd.DataFrame(rows)


def battery_verticals(panels, sample="train"):
    """H27: adjacent-strike verticals when PM bucket mass mispriced vs options RND.

    Long-between: buy YES(K_lo) + buy NO(K_hi); cost-1 vs FV mass between.
    Short-between: buy NO(K_lo) + buy YES(K_hi) when PM overprices the bucket.
    Both legs taker at next-bar quotes; hold to settlement (labels give payoff).
    """
    rows = []
    allp = pd.concat(panels, ignore_index=True)
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    m = m[(m.ask > 0.02) & (m.ask < 0.98) & (m.spread <= 0.05)]
    trades = {"long": [], "short": []}
    for (asset, event, ts), g in m.groupby(["asset", "event", "ts"]):
        if len(g) < 2:
            continue
        g = g.sort_values("K").reset_index(drop=True)
        for i in range(len(g) - 1):
            lo, hi = g.iloc[i], g.iloc[i + 1]
            if not np.isfinite(lo.n_ask) or not np.isfinite(hi.n_bid):
                continue
            fv_mass = lo.fv - hi.fv
            # long-between: cost-1 = ask_lo + (1-bid_hi) - 1 = ask_lo - bid_hi
            cost_l = lo.n_ask - hi.n_bid
            fee_l = fee(lo.n_ask) + fee(1 - hi.n_bid)
            edge_l = fv_mass - cost_l - fee_l
            # short-between: buy NO(K_lo) at 1-bid_lo + YES(K_hi) at ask_hi; pays 1 - between
            cost_s = (1 - lo.n_bid) + hi.n_ask
            fee_s = fee(1 - lo.n_bid) + fee(hi.n_ask)
            edge_s = (1 - fv_mass) - cost_s - fee_s
            between = lo.label - hi.label  # 1 if K_lo < S_T <= K_hi
            if edge_l > 0.02:
                trades["long"].append(dict(asset=asset, event=event, slug=lo.slug, ts=ts,
                                           ev_raw=between - cost_l - fee_l,
                                           ev_hedged=between - cost_l - fee_l, label=between))
            if edge_s > 0.02:
                ev_s = (1 - between) - cost_s - fee_s
                trades["short"].append(dict(asset=asset, event=event, slug=lo.slug, ts=ts,
                                            ev_raw=ev_s, ev_hedged=ev_s, label=1 - between))
    for side, tl in trades.items():
        d = pd.DataFrame(tl)
        if len(d) < 5:
            continue
        d = dedup_trades(d, cols=("slug",), cooldown_h=24)
        st = clustered_stats(d)
        rows.append(dict(family="vertical_between", side=side, thr=0.02, sample=sample, **st))
    return pd.DataFrame(rows)


def battery_weekend_hours(panels, sample="train"):
    """H4/H28: does the gap structure differ by UTC hour-of-day / weekend?"""
    allp = pd.concat(panels, ignore_index=True)
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    m = m[(m.ask > 0.05) & (m.ask < 0.95)].copy()
    m["hour"] = m.dt.dt.hour
    m["wend"] = m.dt.dt.dayofweek >= 5
    t1 = m.groupby("wend")[["gap_yes", "gap_no", "spread"]].mean().round(4)
    t2 = m.groupby(m.hour // 4)[["gap_yes", "gap_no", "spread"]].mean().round(4)
    return t1, t2


def battery_calendar(panels, sample="train"):
    """H10/H13: same strike, adjacent expiries. PM conditional migration vs options.

    For expiry pair (T1<T2), same K: no-arb needs P(S_T2>K) >= P(S_T1>K) - P(down move)...
    We use the options surface directly: compare PM spread (p2-p1) to FV spread (fv2-fv1);
    trade when they disagree by > thr on executable quotes (buy cheap leg, sell rich leg
    via its NO). Both legs taker.
    """
    rows = []
    allp = pd.concat(panels, ignore_index=True)
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    m = m[(m.ask > 0.03) & (m.ask < 0.97)]
    for (asset, K, ts), g in m.groupby(["asset", "K", "ts"]):
        if g.T_pm.nunique() < 2:
            continue
        g = g.sort_values("T_pm")
        for i in range(len(g) - 1):
            a, b = g.iloc[i], g.iloc[i + 1]
            if b.T_pm - a.T_pm > 3 * 86400:
                continue
            pm_spr_ask = b.ask - a.bid   # cost of long T2 / short T1 (buy NO on T1)
            fv_spr = b.fv - a.fv
            edge_long2 = fv_spr - pm_spr_ask - fee(b.ask) - fee(1 - a.bid)
            pm_spr_bid = b.bid - a.ask
            edge_long1 = pm_spr_bid - fv_spr - fee(a.ask) - fee(1 - b.bid)
            if edge_long2 > 0.01:
                ev = (b.label - a.label) - pm_spr_ask - fee(b.ask) - fee(1 - a.bid)
                rows.append(dict(asset=asset, K=K, ts=ts, event=b.event, dir="long_far",
                                 edge=edge_long2, ev=ev))
            if edge_long1 > 0.01:
                ev = (a.label - b.label) - (a.ask - b.bid) - fee(a.ask) - fee(1 - b.bid)
                rows.append(dict(asset=asset, K=K, ts=ts, event=b.event, dir="long_near",
                                 edge=edge_long1, ev=ev))
    d = pd.DataFrame(rows)
    if len(d) == 0:
        return d, {}
    d = dedup_trades(d.assign(slug=d.asset + d.K.astype(str) + d.dir), cooldown_h=24)
    g = d.groupby(["asset", "event"])["ev"].mean()
    return d, {"n": len(d), "ev": d.ev.mean(), "n_events": len(g),
               "t": g.mean() / (g.std() / max(np.sqrt(len(g)), 1)) if len(g) > 2 else np.nan}


if __name__ == "__main__":
    assets = sys.argv[1:] if len(sys.argv) > 1 else ["BTC", "ETH", "SOL", "XRP"]
    panels = [prep(a) for a in assets]
    out = battery_thresholds(panels, "train")
    pd.set_option("display.width", 250)
    print(out.sort_values("t", ascending=False).head(30).to_string())
    out.to_csv("../results/battery_train.csv", index=False)
