"""Rebuild incumbent trade lists (train + OOS) with all fields needed for hedge lab.

S1 taker-YES: gap_yes>0.05, 3<=tte_d<8, base filter (0.02<ask<0.98, spread<=0.05), dedup 24h,
entry at next-bar ask. S2 maker-YES: fv-(bid+0.01)>0.025, tte 0-8, selection on base filter,
fill = any of next 16 bars (full panel) with ask<=limit. Matches scripts/final_eval.py.
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/user/D1/scripts")
from battery import prep
from evaluate import fee, dedup_trades

OUT = "/home/user/D1/results/improve/hedge-lab"

KEEP = ["asset", "slug", "K", "T_pm", "ts", "sigma", "tte_d", "S_bin", "S_T", "delta",
        "ret_T", "label", "event", "is_test"]


def taker_trades(base):
    sel = base[(base.gap_yes > 0.05) & (base.tte_d >= 3) & (base.tte_d < 8)]
    sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
    d = sel.dropna(subset=["n_ask", "S_T"]).copy()
    d["entry"] = d.n_ask
    d["entry_ts"] = d.n_ts
    d["ev_raw"] = d.label - d.entry - fee(d.entry)
    d["ev_hedged"] = d.ev_raw - d.delta * d.ret_T
    return d[KEEP + ["entry", "entry_ts", "ev_raw", "ev_hedged"]].reset_index(drop=True)


def maker_trades(allp, base, horizon_bars=16):
    bys = {s: g.reset_index(drop=True) for s, g in allp.groupby("slug")}
    sel = base[(base.fv - (base.bid + 0.01)) > 0.025]
    sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
    rows = []
    for r in sel.itertuples():
        g = bys.get(r.slug)
        if g is None:
            continue
        fut = g[g.ts > r.ts].head(horizon_bars)
        if len(fut) == 0:
            continue
        lim = r.bid + 0.01
        hit = fut[fut.ask <= lim]
        if len(hit) == 0:
            continue
        f0 = hit.iloc[0]
        ev_raw = r.label - lim
        rows.append(dict(asset=r.asset, slug=r.slug, K=r.K, T_pm=r.T_pm, ts=r.ts,
                         sigma=r.sigma, tte_d=r.tte_d, S_bin=r.S_bin, S_T=r.S_T,
                         delta=r.delta, ret_T=r.ret_T, label=r.label, event=r.event,
                         is_test=r.is_test, entry=lim, entry_ts=f0.ts, ev_raw=ev_raw,
                         ev_hedged=ev_raw - r.delta * r.ret_T))
    return pd.DataFrame(rows)


def main():
    panels = [prep(a) for a in ["BTC", "ETH", "SOL", "XRP"]]
    allp = pd.concat(panels, ignore_index=True)
    base = allp[(allp.ask > 0.02) & (allp.ask < 0.98) & (allp.spread <= 0.05)]
    for name, fn in [("taker", lambda s: taker_trades(base[base.is_test == s])),
                     ("maker", lambda s: maker_trades(allp[allp.is_test == s],
                                                      base[base.is_test == s]))]:
        for smp, flag in [("train", False), ("oos", True)]:
            d = fn(flag)
            d.to_csv(f"{OUT}/trades_{name}_{smp}.csv", index=False)
            g = d.groupby(["asset", "event"]).ev_hedged.mean()
            print(f"{name} {smp}: n={len(d)} ev_ev={g.mean():.4f} "
                  f"t={g.mean()/(g.std()/np.sqrt(len(g))):.2f}")


if __name__ == "__main__":
    main()
