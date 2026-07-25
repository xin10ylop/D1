"""Family 4: EVENT-PORTFOLIO YES-BASKET (maker legs).

At one entry bar per (asset, expiry-event) per 24h — the first bar where >= 1
strike qualifies — rest YES bids at bid+1c on EVERY strike in the ladder with
maker edge fv - (bid+0.01) > thr (the S2 signal, applied ladder-wide). Tape fill
truth per leg (4h window). Basket weights over FILLED legs: gap-weighted vs
equal vs best-1 (max-gap strike only, incumbent-style dedup proxy).

Question: does within-event diversification tighten the event-level P&L enough
(higher event t) to justify larger per-event size than single-strike?
Metrics per event-entry, per $1 of premium: hedged EV (LINEAR), event-clustered t,
across-event SD. Usage: python nf_basket.py train|test
"""
import sys

import numpy as np
import pandas as pd

from nf_common import load_all, Tape, FastTape, dedup, clustered, OUT

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
H_FILL = 4 * 3600
FROZEN = dict(thr=0.025, tte_lo=0, tte_hi=8)  # set after train tuning


def entry_bars(m, thr, tte_lo, tte_hi):
    """Qualifying strike-rows grouped into event-entry bars (24h cooldown/event)."""
    m = m[(m.tte_d >= tte_lo) & (m.tte_d < tte_hi)
          & (m.ask > 0.02) & (m.ask < 0.98) & (m.spread > 0.01)]  # clean + maker room
    m = m[m.fv - (m.bid + 0.01) > thr].dropna(subset=["S_T"])
    out = []
    for (asset, event), g in m.groupby(["asset", "event"]):
        t_last = -1e18
        for ts, bar in g.sort_values("ts").groupby("ts"):
            if ts - t_last < 86400:
                continue
            t_last = ts
            out.append(bar)
    return out


def run(tape, ft, bars):
    rows = []
    for bar in bars:
        legs = []
        for r in bar.itertuples():
            t1 = r.ts + H_FILL
            if not tape.coverage(r.slug, r.ts, t1):
                continue
            lim = r.bid + 0.01
            fill = ft.bid_fill_t(r.slug, r.ts, t1, lim)
            gap = r.fv - lim
            ev_raw = (r.label - lim) if fill is not None else np.nan
            ev_h = (ev_raw - r.delta * r.retlin_T) if fill is not None else np.nan
            legs.append(dict(slug=r.slug, gap=gap, filled=fill is not None,
                             lim=lim, label=r.label, ev_raw=ev_raw, ev_h=ev_h))
        if not legs:
            continue
        L = pd.DataFrame(legs)
        f = L[L.filled]
        r0 = bar.iloc[0]
        row = dict(asset=r0.asset, event=r0.event, ts=r0.ts, n_sig=len(L),
                   n_fill=len(f))
        # best1 = max-gap signalled strike (fill or nothing)
        b = L.loc[L.gap.idxmax()]
        row["ev_best1"] = b.ev_h if b.filled else np.nan
        row["best1_filled"] = bool(b.filled)
        if len(f):
            gw = f.gap / f.gap.sum()
            row["ev_gapw"] = float((gw * f.ev_h).sum())
            row["ev_eqw"] = float(f.ev_h.mean())
            row["ev_raw_gapw"] = float((gw * f.ev_raw).sum())
            row["hit_gapw"] = float((gw * f.label).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def stats(d, col):
    d = d.dropna(subset=[col])
    if len(d) == 0:
        return {}
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / max(np.sqrt(len(g)), 1)
    return dict(n_trades=len(d), n_evt=len(g), ev=g.mean(), sd_evt=g.std(),
                t=g.mean() / se if se > 0 else np.nan)


def main():
    allp = load_all()
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    tape = Tape()
    ft = FastTape(tape)
    grid = ([(thr, a, b) for thr in (0.025, 0.04) for a, b in ((0, 8), (3, 8))]
            if sample == "train" else
            [(FROZEN["thr"], FROZEN["tte_lo"], FROZEN["tte_hi"])])
    rows = []
    for thr, tlo, thi in grid:
        bars = entry_bars(m, thr, tlo, thi)
        d = run(tape, ft, bars)
        if len(d) < 5:
            continue
        d.to_csv(OUT / f"basket_trades_{sample}_thr{thr}_{tlo}{thi}.csv", index=False)
        for w in ["gapw", "eqw", "best1"]:
            st = stats(d, f"ev_{w}")
            rows.append(dict(thr=thr, tte=f"{tlo}-{thi}", w=w, sample=sample,
                             n_bars=len(d), mean_sig=d.n_sig.mean(),
                             mean_fill=d.n_fill.mean(), **st))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"basket_grid_{sample}.csv", index=False)
    pd.set_option("display.width", 250)
    print(res.round(4).to_string())


if __name__ == "__main__":
    main()
