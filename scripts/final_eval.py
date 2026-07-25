"""One-shot final evaluation: tune on train (expiry < 2026-05-16), freeze, evaluate OOS.

Produces results/final_strategies.csv + per-strategy OOS trade lists in results/trades/.
Families:
  F1 taker YES hold   : buy YES at next-bar ask when fv - ask > thr
  F2 taker NO hold    : buy NO at next-bar (1-bid) when bid - fv > thr
  F3 maker YES hold   : rest at bid+0.01 when fv - (bid+0.01) > thr; conservative fill
  F4 maker NO hold    : rest NO at (1-ask)+0.01 when (1-fv) - (1-ask+0.01) ... symmetric
Selection on train: hedged-EV clustered t >= 2 and n_events >= 15; pick per family the
threshold/tte-band with max hedged EV subject to n >= 40. Freeze. Evaluate identically OOS.
"""
import sys, json, pathlib
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/user/D1/scripts")
from battery import prep, clustered_stats, eval_side, SPLIT
from evaluate import fee, dedup_trades

RES = pathlib.Path("/home/user/D1/results"); (RES / "trades").mkdir(parents=True, exist_ok=True)

TTE_BANDS = [(0, 8), (0, 1), (1, 3), (3, 8)]
THRS = [0.01, 0.02, 0.03, 0.05]


def taker_family(m, side):
    gapc = "gap_yes" if side == "yes" else "gap_no"
    entryc = "n_ask" if side == "yes" else "n_bid"
    out = []
    for thr in THRS:
        for lo, hi in TTE_BANDS:
            sel = m[(m[gapc] > thr) & (m.tte_d >= lo) & (m.tte_d < hi)]
            sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
            d = eval_side(sel, side, entryc)
            st = clustered_stats(d)
            if st:
                out.append((dict(side=side, thr=thr, tte=(lo, hi)), st, d))
    return out


def maker_family(allp, m, side, horizon_bars=16):
    out = []
    bys = {s: g.reset_index(drop=True) for s, g in allp.groupby("slug")}
    for thr in [0.015, 0.025, 0.04]:
        if side == "yes":
            sel = m[(m.fv - (m.bid + 0.01)) > thr]
        else:
            sel = m[((1 - m.ask + 0.01) < (1 - m.fv) - thr)]
        sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
        trades = []
        for r in sel.itertuples():
            g = bys.get(r.slug)
            if g is None:
                continue
            fut = g[(g.ts > r.ts)].head(horizon_bars)
            if len(fut) == 0:
                continue
            if side == "yes":
                lim = r.bid + 0.01
                if lim >= r.ask:  # AUDIT FIX: crossing limit = taker, not a free maker fill
                    continue
                if ((fut.ask <= lim) & (fut.bid <= fut.ask)).any():
                    ev_raw = r.label - lim
                    ev_h = ev_raw - r.delta * r.ret_T
                    trades.append(dict(asset=r.asset, event=r.event, slug=r.slug, ts=r.ts,
                                       entry=lim, ev_raw=ev_raw, ev_hedged=ev_h, label=r.label))
            else:
                lim_no = (1 - r.ask) + 0.01
                if (fut.bid >= 1 - lim_no).any():
                    ev_raw = (1 - r.label) - lim_no
                    ev_h = ev_raw + r.delta * r.ret_T
                    trades.append(dict(asset=r.asset, event=r.event, slug=r.slug, ts=r.ts,
                                       entry=lim_no, ev_raw=ev_raw, ev_hedged=ev_h, label=r.label))
        d = pd.DataFrame(trades)
        st = clustered_stats(d) if len(d) else {}
        if st:
            st["fill_rate"] = round(len(d) / max(len(sel), 1), 3)
            out.append((dict(side=side, thr=thr, tte=(0, 8)), st, d))
    return out


def main():
    panels = [prep(a) for a in ["BTC", "ETH", "SOL", "XRP"]]
    allp = pd.concat(panels, ignore_index=True)
    # AUDIT FIX: exclude crossed books (negative spread = phantom one-sided quotes)
    base = allp[(allp.ask > 0.02) & (allp.ask < 0.98) & (allp.spread >= 0.0) & (allp.spread <= 0.05)]
    train, test = base[~base.is_test], base[base.is_test]
    alltr, allte = allp[~allp.is_test], allp[allp.is_test]
    print(f"train: {train.slug.nunique()} mkts {train.exp_dt.min().date()}->{train.exp_dt.max().date()}")
    print(f"test : {test.slug.nunique()} mkts {test.exp_dt.min().date()}->{test.exp_dt.max().date()}")

    families = {
        "taker_yes": lambda m, a: taker_family(m, "yes"),
        "taker_no": lambda m, a: taker_family(m, "no"),
        "maker_yes": lambda m, a: maker_family(a, m, "yes"),
        "maker_no": lambda m, a: maker_family(a, m, "no"),
    }
    rows = []
    for fam, fn in families.items():
        cands = fn(train, alltr)
        good = [(cfg, st, d) for cfg, st, d in cands
                if st.get("n", 0) >= 40 and st.get("n_events", 0) >= 15 and st.get("t", 0) >= 2]
        if not good:
            rows.append(dict(family=fam, status="no_config_passes_train",
                             best_train=str(max([ (round(st.get('t',-9),2), cfg) for cfg,st,_ in cands], default=None))))
            continue
        cfg, st_tr, _ = max(good, key=lambda x: x[1]["ev_ev"])
        cands_te = fn(test, allte)
        match = [x for x in cands_te if x[0] == cfg]
        st_te, d_te = (match[0][1], match[0][2]) if match else ({}, pd.DataFrame())
        if len(d_te):
            d_te.to_csv(RES / "trades" / f"{fam}_oos.csv", index=False)
        rows.append(dict(family=fam, status="evaluated", cfg=json.dumps(cfg, default=str),
                         train_n=st_tr["n"], train_ev_h=round(st_tr["ev_ev"], 4), train_t=round(st_tr["t"], 2),
                         oos_n=st_te.get("n"), oos_events=st_te.get("n_events"),
                         oos_ev_hedged=round(st_te.get("ev_ev", np.nan), 4) if st_te else None,
                         oos_t=round(st_te.get("t", np.nan), 2) if st_te else None,
                         oos_hit=round(st_te.get("hit", np.nan), 3) if st_te else None))
        print(rows[-1], flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(RES / "final_strategies.csv", index=False)
    print(out.to_string())


if __name__ == "__main__":
    main()
