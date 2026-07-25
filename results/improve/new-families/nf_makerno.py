"""Family 5: MAKER-NO REVISIT (audit M1 fixed the inverted sign; corrected bar-rule
was borderline +1.19c t=1.9 on train). Proper tune on train, tape fill truth.

Mechanics: rest a NO bid 1c above the NO best bid == rest a YES ask at (ask-1c).
Non-crossing requires spread > 0.01. Fill truth: first 'buy' print on the YES tape
at price >= our ask within a 4h window (exec-lab convention). Fee 0. Hold to
settlement. LINEAR hedge, NO side longs the perp: ev_h = ev_raw + delta*(S_T/S_bin-1).
Also reports the fill-time-hedge variant (hedge-lab flagged maker adverse selection).

Usage: python nf_makerno.py train|test
"""
import sys

import numpy as np
import pandas as pd

from nf_common import (load_all, Tape, FastTape, Spot1m, dedup, clustered, OUT)

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
H_FILL = 4 * 3600
FROZEN = dict(thr=0.02, tte_lo=0, tte_hi=8)  # set after train tuning


def signals(m, thr, tte_lo, tte_hi):
    m = m[(m.tte_d >= tte_lo) & (m.tte_d < tte_hi)]
    m = m[(m.ask > 0.02) & (m.ask < 0.98) & (m.spread > 0.01)]  # clean + non-crossing
    lim_no = (1 - m.ask) + 0.01
    sel = m[(1 - m.fv) - lim_no > thr]
    return dedup(sel, cooldown_h=24)


def run(tape, ft, spot, sel):
    rows = []
    for r in sel.sort_values(["slug", "ts"]).itertuples():
        t1 = r.ts + H_FILL
        if not tape.coverage(r.slug, r.ts, t1):
            continue
        limA = r.ask - 0.01                      # our resting YES ask
        ft_fill = ft.ask_fill_t(r.slug, r.ts, t1, limA)
        filled = ft_fill is not None
        row = dict(asset=r.asset, event=r.event, slug=r.slug, ts=r.ts,
                   tte_d=r.tte_d, filled=int(filled), label=r.label)
        if filled:
            lim_no = 1 - limA
            ev_raw = (1 - r.label) - lim_no
            ev_h = ev_raw + r.delta * r.retlin_T
            S_f = spot.at(r.asset, ft_fill)
            ev_hf = ev_raw + r.delta * (r.S_T / S_f - 1) if np.isfinite(S_f) else np.nan
            row.update(ev_raw=ev_raw, ev_hedged=ev_h, ev_h_fill=ev_hf,
                       entry=lim_no, wait_min=(ft_fill - r.ts) / 60)
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    allp = load_all()
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    tape, spot = Tape(), Spot1m()
    ft = FastTape(tape)
    grid = ([(thr, a, b) for thr in (0.01, 0.015, 0.02, 0.03)
             for a, b in ((0, 1), (1, 3), (3, 8), (0, 8))]
            if sample == "train" else
            [(FROZEN["thr"], FROZEN["tte_lo"], FROZEN["tte_hi"])])
    rows = []
    for thr, tlo, thi in grid:
        sel = signals(m, thr, tlo, thi)
        d = run(tape, ft, spot, sel)
        if len(d) == 0:
            continue
        f = d[d.filled == 1]
        if len(f) < 5:
            continue
        st = clustered(f)
        stf = clustered(f.dropna(subset=["ev_h_fill"]), "ev_h_fill")
        rows.append(dict(thr=thr, tte=f"{tlo}-{thi}", sample=sample,
                         n_sig=len(sel), n_cov=len(d),
                         fill_rate=len(f) / max(len(d), 1),
                         nowin=1 - f.label.mean(),
                         ev_raw=clustered(f, "ev_raw").get("ev_ev"),
                         ev_h_fill=stf.get("ev_ev"), t_fill=stf.get("t"),
                         wait_med=f.wait_min.median(), **st))
        if (thr, tlo, thi) == (FROZEN["thr"], FROZEN["tte_lo"], FROZEN["tte_hi"]) \
                or sample == "test":
            f.to_csv(OUT / f"makerno_trades_{sample}.csv", index=False)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"makerno_grid_{sample}.csv", index=False)
    pd.set_option("display.width", 250)
    print(res.round(4).to_string())


if __name__ == "__main__":
    main()
