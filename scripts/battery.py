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


if __name__ == "__main__":
    assets = sys.argv[1:] if len(sys.argv) > 1 else ["BTC", "ETH", "SOL", "XRP"]
    panels = [prep(a) for a in assets]
    out = battery_thresholds(panels, "train")
    pd.set_option("display.width", 250)
    print(out.sort_values("t", ascending=False).head(30).to_string())
    out.to_csv("../results/battery_train.csv", index=False)
