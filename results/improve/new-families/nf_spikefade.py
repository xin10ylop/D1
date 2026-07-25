"""Family 3: SPIKE-FADE — after a >=1.5-sigma trailing-30-min Binance move, does the
PM quote OVERSHOOT the FV change (fade profitable) or LAG it (dead, graveyard)?

Overshoot O = (mid_t - mid_{t-2bars}) - (fv_t - fv_{t-2bars}); mids only from
non-crossed books (audit C1). Phase A: does signed O predict reversion of
(mid - fv) over the next hour? Phase B: maker fade of the cheapened side, tape
fill truth (2h window), hold to settlement, LINEAR hedge (signal-time primary,
fill-time reported). Usage: python nf_spikefade.py train|test
"""
import sys

import numpy as np
import pandas as pd

from nf_common import (load_all, Tape, FastTape, Spot1m, fee,
                       dedup, clustered, OUT, DATA, SYMBOL)

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
H_FILL = 2 * 3600
# Frozen after train tuning: only othr=0.05 is positive under BOTH hedge
# conventions (signal-time +7.4c t=3.53; fill-time +2.7c t=1.28), n=254/187ev.
FROZEN = dict(othr=0.05, fside="both")


def spike_z(asset):
    kl = pd.read_parquet(DATA / f"binance/{SYMBOL[asset]}_1m.parquet")
    ts = (kl.open_time_us // 1_000_000).astype("int64").values
    r30 = pd.Series(np.log(kl.close.values)).diff(30)
    sig = r30.ewm(halflife=10 * 1440, min_periods=1440).std()
    return pd.DataFrame({"ts": ts + 60, "z30": (r30 / sig).values})  # close time


def build(allp):
    parts = []
    for a, g in allp.groupby("asset"):
        zf = spike_z(a)
        g = g.copy()
        g["z30"] = g.ts.map(dict(zip(zf.ts, zf.z30)))
        parts.append(g)
    p = pd.concat(parts, ignore_index=True)
    p = p.sort_values(["slug", "ts"]).reset_index(drop=True)
    # clean mid: crossed books give phantom mids (audit C1)
    p["cmid"] = np.where(p.spread >= 0, p["mid"], np.nan)
    g = p.groupby("slug")
    p["mid_pre"] = g["cmid"].shift(2)
    p["fv_pre"] = g["fv"].shift(2)
    p["ts_pre"] = g["ts"].shift(2)
    ok = (p.ts - p.ts_pre) <= 2100
    p.loc[~ok, ["mid_pre", "fv_pre"]] = np.nan
    p["dmid"] = p.cmid - p.mid_pre
    p["dfv"] = p.fv - p.fv_pre
    p["overshoot"] = p.dmid - p.dfv
    p["mid_f"] = g["cmid"].shift(-4)
    p["fv_f"] = g["fv"].shift(-4)
    p["ts_f"] = g["ts"].shift(-4)
    okf = (p.ts_f - p.ts) <= 4500
    p.loc[~okf, ["mid_f", "fv_f"]] = np.nan
    p["fwd_dmidfv"] = (p.mid_f - p.fv_f) - (p.cmid - p.fv)
    return p


def main():
    allp = load_all()
    p = build(allp)
    m = p[p.is_test] if sample == "test" else p[~p.is_test]
    m = m[(m.ask > 0.05) & (m.ask < 0.95) & (m.spread >= 0) & (m.spread <= 0.05)
          & m.overshoot.notna() & (m.tte_d < 8)]
    spike = m[m.z30.abs() >= 1.5].copy()
    spike["dir"] = np.sign(spike.z30)

    if sample == "train":
        t = spike.groupby("dir")[["dmid", "dfv", "overshoot"]].agg(["mean", "count"])
        print("response by spike dir (train):\n", t.round(4))
        t2 = spike.assign(ob=pd.cut(spike.overshoot * spike.dir,
                                    [-1, -0.05, -0.03, -0.01, 0.01, 0.03, 1])) \
            .groupby("ob", observed=True).agg(n=("fwd_dmidfv", "size"),
                                              fwd=("fwd_dmidfv", "mean"),
                                              fwd_sd=("fwd_dmidfv", "std"))
        t2["fwd_t"] = t2.fwd / (t2.fwd_sd / np.sqrt(t2.n.clip(lower=1)))
        print("signed overshoot -> next-1h d(mid-fv):\n", t2.round(4))
        t2.to_csv(OUT / "spikefade_reversion_train.csv")

    tape, spot = Tape(), Spot1m()
    ft = FastTape(tape)
    rows = []
    grid = [0.02, 0.03, 0.05] if sample == "train" else [FROZEN["othr"]]
    for othr in grid:
        dn = spike[(spike.dir < 0) & (spike.overshoot < -othr)
                   & (spike.fv - (spike.bid + 0.01) > 0) & (spike.spread > 0.01)]
        up = spike[(spike.dir > 0) & (spike.overshoot > othr)
                   & ((spike.ask - 0.01) - spike.fv > 0) & (spike.spread > 0.01)]
        sel = pd.concat([dn.assign(fside="yes"), up.assign(fside="no")])
        sel = dedup(sel, cooldown_h=24)
        trades = []
        for r in sel.sort_values(["slug", "ts"]).itertuples():
            t1 = r.ts + H_FILL
            if not tape.coverage(r.slug, r.ts, t1):
                continue
            if r.fside == "yes":
                lim = r.bid + 0.01
                tf = ft.bid_fill_t(r.slug, r.ts, t1, lim)
                if tf is None:
                    continue
                ev_raw = r.label - lim
                ev_h = ev_raw - r.delta * r.retlin_T
                S_f = spot.at(r.asset, tf)
                ev_hf = ev_raw - r.delta * (r.S_T / S_f - 1) if np.isfinite(S_f) else np.nan
            else:
                lim = r.ask - 0.01
                tf = ft.ask_fill_t(r.slug, r.ts, t1, lim)
                if tf is None:
                    continue
                ev_raw = (1 - r.label) - (1 - lim)
                ev_h = ev_raw + r.delta * r.retlin_T
                S_f = spot.at(r.asset, tf)
                ev_hf = ev_raw + r.delta * (r.S_T / S_f - 1) if np.isfinite(S_f) else np.nan
            trades.append(dict(asset=r.asset, event=r.event, slug=r.slug, ts=r.ts,
                               fside=r.fside, overshoot=r.overshoot, z=r.z30,
                               label=r.label, ev_raw=ev_raw, ev_hedged=ev_h,
                               ev_h_fill=ev_hf, wait_min=(tf - r.ts) / 60))
        d = pd.DataFrame(trades)
        if len(d) < 5:
            continue
        groups = [("both", d)] + [(fs, ds) for fs, ds in d.groupby("fside")]
        for fs, ds in groups:
            nsig = len(sel) if fs == "both" else len(sel[sel.fside == fs])
            st = clustered(ds)
            stf = clustered(ds.dropna(subset=["ev_h_fill"]), "ev_h_fill")
            rows.append(dict(othr=othr, fside=fs, sample=sample, n_sig=nsig,
                             fill_rate=len(ds) / max(nsig, 1),
                             ev_raw=clustered(ds, "ev_raw").get("ev_ev"),
                             ev_h_fill=stf.get("ev_ev"), t_fill=stf.get("t"), **st))
        d.to_csv(OUT / f"spikefade_trades_{sample}_o{othr}.csv", index=False)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"spikefade_grid_{sample}.csv", index=False)
    pd.set_option("display.width", 250)
    print(res.round(4).to_string())


if __name__ == "__main__":
    main()
