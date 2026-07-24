"""Family 1: MAKER-LEGGED VERTICALS (adjacent-strike bucket vs options RND mass).

Earlier vertical test (battery H27) paid taker fees on both legs -> dead. Here both
legs are MAKER (fee 0), fill truth from the YES-token tape as in exec-lab:
  long-between  = YES bid on K_lo at bid_lo+1c   + NO bid on K_hi (== YES ask at ask_hi-1c)
  short-between = NO bid on K_lo (YES ask at ask_lo-1c) + YES bid on K_hi at bid_hi+1c
Signal on bar t (panel quotes), orders live from ts for H hours; legs fill
independently. Per-signal accounting: filled legs held to settlement, hedged with
the digital delta of the filled leg(s) from the signal bar. Unfilled leg = nothing
(legging risk is real and is charged: a lone leg is just a directional digital).

Usage: python newfam_verticals.py train|test
"""
import sys
import numpy as np
import pandas as pd
from newfam_common import load_all, Tape, fee, clustered, OUT, SPLIT

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
H_HOURS = 4

FROZEN = dict(thr=0.03, tte_lo=0, tte_hi=8)  # set after train tuning


def pair_signals(m, thr, tte_lo, tte_hi):
    m = m[(m.tte_d >= tte_lo) & (m.tte_d < tte_hi)]
    m = m[(m.bid > 0.01) & (m.ask < 0.99) & (m.spread <= 0.10)]
    m = m.sort_values(["asset", "T_pm", "ts", "K"]).reset_index(drop=True)
    lo = m
    hi = m.shift(-1)
    same = (lo.asset == hi.asset) & (lo.T_pm == hi.T_pm) & (lo.ts == hi.ts)
    fv_mass = lo.fv - hi.fv
    lim_lo = lo.bid + 0.01
    lim_hi_ask = hi.ask - 0.01                       # our YES ask on hi == NO bid
    edge_l = (1 + fv_mass) - (lim_lo + (1 - lim_hi_ask))
    lim_lo_ask = lo.ask - 0.01
    lim_hi = hi.bid + 0.01
    edge_s = (1 - fv_mass) - ((1 - lim_lo_ask) + lim_hi)
    base = pd.DataFrame(dict(asset=lo.asset, event=lo.event, ts=lo.ts,
                             slug_lo=lo.slug, slug_hi=hi.slug,
                             fv_mass=fv_mass, lab_lo=lo.label, lab_hi=hi.label,
                             d_lo=lo.delta, d_hi=hi.delta, ret_T=lo.ret_T,
                             lim_lo=lim_lo, lim_hi_ask=lim_hi_ask,
                             lim_lo_ask=lim_lo_ask, lim_hi=lim_hi))
    sl = base[same & (edge_l > thr)].copy()
    sl["side"], sl["edge"] = "long", edge_l[same & (edge_l > thr)]
    ss = base[same & (edge_s > thr)].copy()
    ss["side"], ss["edge"] = "short", edge_s[same & (edge_s > thr)]
    s = pd.concat([sl, ss], ignore_index=False)
    if len(s) == 0:
        return s
    # dedup: one signal per pair per 24h per side
    s["pair"] = s.slug_lo + "|" + s.slug_hi + "|" + s.side
    s = s.sort_values("ts")
    keep, last = [], {}
    for r in s.itertuples():
        if r.pair not in last or r.ts - last[r.pair] >= 86400:
            keep.append(r.Index)
            last[r.pair] = r.ts
    return s.loc[keep]


def run(tape, sigs):
    """Per-signal P&L with independent tape fills of the two maker legs."""
    rows = []
    H = H_HOURS * 3600
    for r in sigs.itertuples():
        t1 = r.ts + H
        if not (tape.coverage(r.slug_lo, r.ts, t1) and tape.coverage(r.slug_hi, r.ts, t1)):
            continue
        if r.side == "long":
            f_lo = tape.first_bid_fill(r.slug_lo, r.ts, t1, r.lim_lo)
            f_hi = tape.first_ask_fill(r.slug_hi, r.ts, t1, r.lim_hi_ask)
            pnl, dnet = 0.0, 0.0
            if f_lo is not None:   # long YES lo at lim_lo
                pnl += r.lab_lo - r.lim_lo
                dnet += r.d_lo
            if f_hi is not None:   # long NO hi at 1-lim_hi_ask
                pnl += (1 - r.lab_hi) - (1 - r.lim_hi_ask)
                dnet -= r.d_hi
        else:
            f_lo = tape.first_ask_fill(r.slug_lo, r.ts, t1, r.lim_lo_ask)
            f_hi = tape.first_bid_fill(r.slug_hi, r.ts, t1, r.lim_hi)
            pnl, dnet = 0.0, 0.0
            if f_lo is not None:   # long NO lo
                pnl += (1 - r.lab_lo) - (1 - r.lim_lo_ask)
                dnet -= r.d_lo
            if f_hi is not None:   # long YES hi
                pnl += r.lab_hi - r.lim_hi
                dnet += r.d_hi
        nfill = int(f_lo is not None) + int(f_hi is not None)
        ev_h = pnl - dnet * r.ret_T
        rows.append(dict(asset=r.asset, event=r.event, ts=r.ts, side=r.side,
                         edge=r.edge, nfill=nfill, both=int(nfill == 2),
                         ev_raw=pnl, ev_hedged=ev_h))
    return pd.DataFrame(rows)


def main():
    allp = load_all()
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    tape = Tape()
    rows = []
    grid = ([(thr, a, b) for thr in (0.02, 0.03, 0.05) for a, b in ((0, 8), (0, 3), (3, 8))]
            if sample == "train" else [(FROZEN["thr"], FROZEN["tte_lo"], FROZEN["tte_hi"])])
    for thr, tlo, thi in grid:
        sigs = pair_signals(m, thr, tlo, thi)
        if len(sigs) == 0:
            continue
        d = run(tape, sigs)
        if len(d) < 5:
            continue
        for side, ds in d.groupby("side"):
            st = clustered(ds)
            st_b = clustered(ds[ds.both == 1]) if ds.both.sum() >= 3 else {}
            rows.append(dict(thr=thr, tte=f"{tlo}-{thi}", side=side, sample=sample,
                             n_sig=len(sigs[sigs.side == side]),
                             both_rate=ds.both.mean(), one_leg=(ds.nfill == 1).mean(),
                             ev_both=st_b.get("ev_ev", np.nan),
                             t_both=st_b.get("t", np.nan), n_both=st_b.get("n", 0),
                             **st))
        d.to_csv(OUT / f"vert_trades_{sample}_thr{thr}_{tlo}{thi}.csv", index=False)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"vert_grid_{sample}.csv", index=False)
    print(res.to_string())


if __name__ == "__main__":
    main()
