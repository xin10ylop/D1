"""Family 2: SETTLEMENT-ZONE SPECIALIST (expiry day, tte<8h, extrap FV zone).

Seasonal-vol FV: rescale the final-day variance by the hour-of-day realized-variance
profile (train Binance 1m). Trade: buy high-prob YES at 90-97c when seasonal FV
is very high. Taker at next bar (fee tiny at p~0.93). Hedged + raw EV, and the
explicit loss distribution (P95/P99/worst). Depth check from pm_books separately.

Usage: python newfam_settlement.py train|test
"""
import sys
import numpy as np
import pandas as pd
from newfam_common import (load_all, seasonal_profile, seasonal_frac_remaining,
                           digital_fv, fee, dedup, clustered, OUT, SPLIT)

sample = sys.argv[1] if len(sys.argv) > 1 else "train"


def main():
    allp = load_all()
    profs = {a: seasonal_profile(a) for a in ["BTC", "ETH", "SOL", "XRP"]}
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    m = m[(m.extrap == 1) & (m.tte_d * 24 < 8) & (m.tte_d > 0)].copy()
    # seasonal tau adjustment
    parts = []
    for a, g in m.groupby("asset"):
        g = g.copy()
        adj = seasonal_frac_remaining(profs[a], g.ts.values, g.T_pm.values)
        g["tau_adj"] = adj
        tau = g.tte_d.values / 365.0
        g["fv_seas"] = digital_fv(g.S_bin.values, g.K.values, g.sigma.values,
                                  tau * adj)
        g["fv_flat"] = digital_fv(g.S_bin.values, g.K.values, g.sigma.values, tau)
        parts.append(g)
    m = pd.concat(parts, ignore_index=True)

    if sample == "train":
        # diagnostic: calibration of fv_seas vs fv_flat vs label in the zone
        m["b_seas"] = pd.cut(m.fv_seas, [0, .5, .9, .95, .98, .99, .995, 1.001])
        cal = m.groupby("b_seas", observed=True).agg(
            n=("label", "size"), label=("label", "mean"),
            fv_seas=("fv_seas", "mean"), fv_flat=("fv_flat", "mean"),
            ask=("ask", "mean")).round(4)
        cal.to_csv(OUT / "settle_calibration_train.csv")
        print("calibration (train, extrap zone):\n", cal)

    rows, best_trades = [], None
    grid = [(0.90, 0.97, 0.985), (0.90, 0.97, 0.99), (0.90, 0.97, 0.995),
            (0.85, 0.97, 0.99), (0.90, 0.99, 0.99),
            (0.90, 0.97, None)]  # None: flat-FV control at 0.99
    if sample == "test":
        grid = [FROZEN]
    for lo, hi, thr in grid:
        fvc = "fv_flat" if thr is None else "fv_seas"
        t = 0.99 if thr is None else thr
        sel = m[(m.ask >= lo) & (m.ask <= hi) & (m[fvc] > t)
                & (m.ask_sz > 0) & m.n_ask.notna() & (m.n_ask <= hi + 0.02)]
        sel = dedup(sel, cooldown_h=24)
        d = sel.dropna(subset=["n_ask", "S_T"]).copy()
        if len(d) < 5:
            continue
        px = d.n_ask
        d["ev_raw"] = d.label - px - fee(px)
        d["ev_hedged"] = d.ev_raw - d.delta * d.ret_T
        st = clustered(d)
        st_raw = clustered(d, "ev_raw")
        q = d.ev_raw.quantile([0.01, 0.05]).values
        rows.append(dict(lo=lo, hi=hi, thr=thr if thr else "flat0.99", fvcol=fvc,
                         sample=sample, ev_raw=st_raw["ev_ev"], t_raw=st_raw["t"],
                         p1_loss=q[0], p5_loss=q[1], worst=d.ev_raw.min(),
                         loss_rate=(d.label == 0).mean(), **st))
        if thr == 0.99 and lo == 0.90 and hi == 0.97:
            best_trades = d
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"settle_grid_{sample}.csv", index=False)
    print(res.to_string())
    if best_trades is not None:
        cols = ["asset", "slug", "ts", "event", "n_ask", "fv_seas", "fv_flat",
                "tte_d", "label", "ev_raw", "ev_hedged"]
        best_trades[cols].to_csv(OUT / f"settle_trades_{sample}.csv", index=False)


FROZEN = (0.90, 0.97, 0.99)  # set after train tuning

if __name__ == "__main__":
    main()
