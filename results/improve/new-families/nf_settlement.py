"""Family 2: SETTLEMENT-ZONE SPECIALIST (expiry day, tte < 8h) using the adopted
seasonal vol-time FV (fv_sea from results/improve/fv-upgrade/panel_fv_variants.parquet).

YES side: buy high-prob YES at 90-97c (taker at next bar) when fv_sea > thr.
NO side (family-6 mirror): buy high-prob NO at 90-97c (i.e. YES bid 3-10c) when
1 - fv_sea > thr. Fee 0.07*p*(1-p) (~0.4-0.6c at p~0.93). LINEAR hedge (audit M2).
Judge on hedged EV AND the explicit loss tail (p1/p5 per-trade, loss rate).

Usage: python nf_settlement.py train|test
"""
import sys

import numpy as np
import pandas as pd

from nf_common import load_all, attach_fv_sea, fee, dedup, clustered, OUT

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
# frozen after train tuning (side, lo, hi, thr, fvcol):
# YES side dead on train (best +0.8c t=0.5) -> reject, no test look.
# NO side passes gate at thr=0.99: +1.85c ev_ev, t=2.24, n=131, 124 events.
FROZEN = [("no", 0.90, 0.97, 0.99, "fv_sea")]


def run_config(m, side, lo, hi, thr, fvcol):
    if side == "yes":
        sel = m[(m.ask >= lo) & (m.ask <= hi) & (m[fvcol] > thr)
                & m.n_ask.notna() & (m.n_ask <= hi + 0.02) & (m.ask_sz > 0)]
    else:
        pno = 1 - m.bid
        sel = m[(pno >= lo) & (pno <= hi) & ((1 - m[fvcol]) > thr)
                & m.n_bid.notna() & ((1 - m.n_bid) <= hi + 0.02) & (m.bid_sz > 0)]
    sel = dedup(sel, cooldown_h=24)
    d = sel.dropna(subset=["S_T"]).copy()
    if len(d) < 5:
        return None, None
    if side == "yes":
        px = d.n_ask
        d["ev_raw"] = d.label - px - fee(px)
        d["ev_hedged"] = d.ev_raw - d.delta * d.retlin_T
        d["win"] = d.label
    else:
        px = 1 - d.n_bid
        d["ev_raw"] = (1 - d.label) - px - fee(px)
        d["ev_hedged"] = d.ev_raw + d.delta * d.retlin_T
        d["win"] = 1 - d.label
    d["entry"] = px
    st = clustered(d)
    st_raw = clustered(d, "ev_raw")
    q = d.ev_raw.quantile([0.01, 0.05]).values
    row = dict(side=side, lo=lo, hi=hi, thr=thr, fvcol=fvcol, sample=sample,
               ev_raw=st_raw["ev_ev"], t_raw=st_raw["t"],
               p1_loss=q[0], p5_loss=q[1], worst=d.ev_raw.min(),
               loss_rate=(d.win == 0).mean(), mean_entry=px.mean(), **st)
    row["hit"] = d.win.mean()
    return row, d


def main():
    allp = attach_fv_sea(load_all())
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    # settlement zone + AUDIT C1: clean books only
    m = m[(m.tte_d * 24 < 8) & (m.tte_d > 0) & (m.spread >= 0)].copy()

    if sample == "train":
        m["b_sea"] = pd.cut(m.fv_sea, [0, .5, .9, .95, .98, .99, .995, 1.001])
        cal = m.groupby("b_sea", observed=True).agg(
            n=("label", "size"), label=("label", "mean"),
            fv_sea=("fv_sea", "mean"), fv_flat=("fv", "mean"),
            ask=("ask", "mean"), bid=("bid", "mean")).round(4)
        cal.to_csv(OUT / "settle_calibration_train.csv")
        print("calibration (train, tte<8h, clean books):\n", cal)

    grid = ([(s, 0.90, 0.97, t, "fv_sea") for s in ("yes", "no")
             for t in (0.985, 0.99, 0.995)]
            + [("yes", 0.85, 0.97, 0.99, "fv_sea"), ("no", 0.85, 0.97, 0.99, "fv_sea")]
            + [("yes", 0.90, 0.97, 0.99, "fv"), ("no", 0.90, 0.97, 0.99, "fv")])
    if sample == "test":
        grid = FROZEN
    rows, saved = [], False
    for side, lo, hi, thr, fvcol in grid:
        row, d = run_config(m, side, lo, hi, thr, fvcol)
        if row is None:
            continue
        rows.append(row)
        keep = (side, lo, hi, thr, fvcol) in (FROZEN if sample == "test"
                                              else [("yes", 0.90, 0.97, 0.99, "fv_sea"),
                                                    ("no", 0.90, 0.97, 0.99, "fv_sea")])
        if keep:
            cols = ["asset", "slug", "ts", "event", "entry", "fv", "fv_sea",
                    "tte_d", "label", "ev_raw", "ev_hedged"]
            d[cols].to_csv(OUT / f"settle_trades_{sample}_{side}.csv", index=False)
            saved = True
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"settle_grid_{sample}.csv", index=False)
    pd.set_option("display.width", 250)
    print(res.round(4).to_string())


if __name__ == "__main__":
    main()
