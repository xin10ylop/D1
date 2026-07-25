"""Family 4: EVENT-PORTFOLIO YES-BASKET.

Instead of one strike, buy the entire underpriced YES side of a ladder: at one
entry bar per (asset, expiry-event) per 24h, take ALL strikes with gap_yes > 2c
(taker at next-bar ask), weighted by gap, normalized to $1 total. Question: does
within-event diversification tighten the event-level distribution enough (vs the
single best strike) to justify larger per-event size?

Comparisons at the same entry bars:
  - best1: 100% in the max-gap strike (the incumbent-style pick)
  - eqw:   equal weight across qualifying strikes
  - gapw:  weight proportional to gap
Metrics per event-trade (portfolio EV per $1): mean, event-clustered t, SD across
events, plus n_strikes distribution. Tuned on train; frozen config one test look.

Usage: python newfam_basket.py train|test
"""
import sys
import numpy as np
import pandas as pd
from newfam_common import load_all, fee, clustered, OUT

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
FROZEN = dict(thr=0.02, tte_lo=3, tte_hi=8)  # set after train tuning


def event_trades(m, thr, tte_lo, tte_hi):
    """One basket per (asset,event) per 24h: first bar where >=1 strike qualifies."""
    m = m[(m.tte_d >= tte_lo) & (m.tte_d < tte_hi)
          & (m.ask > 0.02) & (m.ask < 0.98) & (m.spread <= 0.05)]
    m = m[m.gap_yes > thr].dropna(subset=["n_ask", "S_T"])
    out = []
    for (asset, event), g in m.groupby(["asset", "event"]):
        g = g.sort_values("ts")
        # entry bars: first qualifying bar, then next after 24h, etc.
        t_last = -1e18
        for ts, bar in g.groupby("ts"):
            if ts - t_last < 86400:
                continue
            t_last = ts
            px = bar.n_ask
            ev_r = bar.label - px - fee(px)
            ev_h = ev_r - bar.delta * bar.ret_T
            gw = bar.gap_yes / bar.gap_yes.sum()
            eq = np.repeat(1 / len(bar), len(bar))
            b1 = (bar.gap_yes == bar.gap_yes.max()).astype(float)
            b1 = b1 / b1.sum()
            out.append(dict(asset=asset, event=event, ts=ts, n_strikes=len(bar),
                            ev_gapw=float((gw * ev_h).sum()),
                            ev_eqw=float((eq * ev_h).sum()),
                            ev_best1=float((b1 * ev_h).sum()),
                            evr_gapw=float((gw * ev_r).sum()),
                            evr_best1=float((b1 * ev_r).sum()),
                            hit_gapw=float((gw * bar.label).sum())))
    return pd.DataFrame(out)


def stats(d, col):
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / max(np.sqrt(len(g)), 1)
    return dict(n_evt=len(g), ev=g.mean(), sd_evt=g.std(),
                t=g.mean() / se if se > 0 else np.nan)


def main():
    allp = load_all()
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    rows = []
    grid = ([(thr, a, b) for thr in (0.02, 0.03, 0.05) for a, b in ((0, 8), (3, 8))]
            if sample == "train" else
            [(FROZEN["thr"], FROZEN["tte_lo"], FROZEN["tte_hi"])])
    for thr, tlo, thi in grid:
        d = event_trades(m, thr, tlo, thi)
        if len(d) < 5:
            continue
        for w in ["gapw", "eqw", "best1"]:
            st = stats(d, f"ev_{w}")
            rows.append(dict(thr=thr, tte=f"{tlo}-{thi}", w=w, sample=sample,
                             n_trades=len(d),
                             mean_strikes=d.n_strikes.mean(), **st))
        d.to_csv(OUT / f"basket_trades_{sample}_thr{thr}_{tlo}{thi}.csv", index=False)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"basket_grid_{sample}.csv", index=False)
    print(res.to_string())


if __name__ == "__main__":
    main()
