"""Family 3: SPIKE-FADE — does PM overshoot FV after large 30-min Binance moves?

Distinct from the dead lag trade (underreaction). Here: after a >=1.5-sigma
30-min move ending at panel bar t, compare the PM mid change with the FV change
over the spike window. If Delta(mid) - Delta(fv) is large in the spike direction,
the cheapened side is faded with a MAKER order (bid+1c on YES after down-spikes;
YES ask-1c == NO bid after up-spikes), tape fill truth, hold to settlement, hedged.

Phase A (measurement): distribution of overshoot O = dmid - dfv conditional on
spike sign, and whether O predicts subsequent hedged EV.
Phase B (trade): entry rule tuned on train; one test look with frozen config.

Usage: python newfam_spikefade.py train|test
"""
import sys
import numpy as np
import pandas as pd
from newfam_common import (load_all, binance_1m, Tape, fee, dedup, clustered,
                           OUT, SPLIT)
from newfam_verticals import FastTape

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
H_FILL = 2 * 3600          # maker order lives 2h (fade should fill fast or not at all)
FROZEN = dict(othr=0.03, side="both")  # set after train tuning


def spike_flags(asset):
    """Per-minute z-score of trailing 30-min log return; sigma = 10-day EWM of
    30-min return std (computed causally)."""
    kl = binance_1m(asset)
    c = kl.close.values
    ts = kl.ts.values
    r30 = pd.Series(np.log(c)).diff(30)
    sig = r30.ewm(halflife=10 * 1440, min_periods=1440).std()
    z = (r30 / sig).values
    return pd.DataFrame({"ts": ts, "z30": z})


def attach_spikes(p, zf):
    """For each panel bar ts: z of the 30-min window ending at ts."""
    zmap = dict(zip(zf.ts, zf.z30))
    p = p.copy()
    p["z30"] = p.ts.map(zmap)
    return p


def build(allp):
    parts = []
    for a, g in allp.groupby("asset"):
        zf = spike_flags(a)
        parts.append(attach_spikes(g, zf))
    p = pd.concat(parts, ignore_index=True)
    p = p.sort_values(["slug", "ts"]).reset_index(drop=True)
    g = p.groupby("slug")
    # spike window = the two 15-min bars covering the trailing 30 min
    p["mid_pre"] = g["mid"].shift(2)
    p["fv_pre"] = g["fv"].shift(2)
    p["ts_pre"] = g["ts"].shift(2)
    ok = (p.ts - p.ts_pre) <= 2100  # 35 min: contiguous bars only
    p.loc[~ok, ["mid_pre", "fv_pre"]] = np.nan
    p["dmid"] = p.mid - p.mid_pre
    p["dfv"] = p.fv - p.fv_pre
    p["overshoot"] = p.dmid - p.dfv
    # forward 1h reversion measure (4 bars ahead)
    p["mid_f"] = g["mid"].shift(-4)
    p["fv_f"] = g["fv"].shift(-4)
    p["ts_f"] = g["ts"].shift(-4)
    okf = (p.ts_f - p.ts) <= 4500
    p.loc[~okf, ["mid_f", "fv_f"]] = np.nan
    p["fwd_dmidfv"] = (p.mid_f - p.fv_f) - (p.mid - p.fv)  # change in mispricing
    return p


def main():
    allp = load_all()
    p = build(allp)
    m = p[p.is_test] if sample == "test" else p[~p.is_test]
    m = m[(m.ask > 0.05) & (m.ask < 0.95) & (m.spread <= 0.05)
          & m.overshoot.notna() & (m.tte_d < 8)]

    spike = m[m.z30.abs() >= 1.5].copy()
    spike["dir"] = np.sign(spike.z30)

    if sample == "train":
        # Phase A: mean dmid vs dfv by spike direction (delta-weighted response)
        t = spike.groupby("dir")[["dmid", "dfv", "overshoot"]].agg(["mean", "count"])
        print("response by spike dir (train):\n", t.round(4))
        # does signed overshoot revert over the next hour? (mid-fv drifts back -> fade works)
        t2 = spike.assign(ob=pd.cut(spike.overshoot * spike.dir,
                                    [-1, -0.05, -0.03, -0.01, 0.01, 0.03, 1])) \
            .groupby("ob", observed=True).agg(n=("fwd_dmidfv", "size"),
                                              fwd_dmidfv=("fwd_dmidfv", "mean"),
                                              overshoot=("overshoot", "mean"))
        print("signed overshoot -> next-1h change in (mid-fv):\n", t2.round(4))
        t2.to_csv(OUT / "spikefade_reversion_train.csv")

    # Phase B: fade trades. Down-spike (dir=-1) & overshoot < -othr  -> YES cheap:
    # maker YES bid at bid+1c. Up-spike & overshoot > +othr -> NO cheap: maker
    # YES ask at ask-1c (buy NO). Also require the fade side has positive model
    # edge at our limit (fv vs limit) so we fade toward FV, not against it.
    tape = Tape()
    ft = FastTape(tape)
    rows = []
    grid = [0.02, 0.03, 0.05] if sample == "train" else [FROZEN["othr"]]
    for othr in grid:
        dn = spike[(spike.dir < 0) & (spike.overshoot < -othr)
                   & (spike.fv - (spike.bid + 0.01) > 0)]
        up = spike[(spike.dir > 0) & (spike.overshoot > othr)
                   & ((spike.ask - 0.01) - spike.fv > 0)]
        sel = pd.concat([dn.assign(fside="yes"), up.assign(fside="no")])
        sel = dedup(sel, cooldown_h=24)
        trades = []
        for r in sel.sort_values("slug").itertuples():
            t1 = r.ts + H_FILL
            if not tape.coverage(r.slug, r.ts, t1):
                continue
            if r.fside == "yes":
                lim = r.bid + 0.01
                fill = ft.bid_fill(r.slug, r.ts, t1, lim)
                if not fill:
                    continue
                ev_raw = r.label - lim
                ev_h = ev_raw - r.delta * r.ret_T
            else:
                lim = r.ask - 0.01           # our YES ask; NO cost = 1-lim
                fill = ft.ask_fill(r.slug, r.ts, t1, lim)
                if not fill:
                    continue
                ev_raw = (1 - r.label) - (1 - lim)
                ev_h = ev_raw + r.delta * r.ret_T
            trades.append(dict(asset=r.asset, event=r.event, ts=r.ts,
                               fside=r.fside, overshoot=r.overshoot, z=r.z30,
                               label=r.label, ev_raw=ev_raw, ev_hedged=ev_h))
        d = pd.DataFrame(trades)
        if len(d) < 5:
            continue
        for fs, ds in d.groupby("fside"):
            st = clustered(ds)
            rows.append(dict(othr=othr, fside=fs, sample=sample,
                             n_sig=len(sel[sel.fside == fs]),
                             fill_rate=len(ds) / max(len(sel[sel.fside == fs]), 1),
                             ev_raw=clustered(ds, "ev_raw").get("ev_ev"), **st))
        st = clustered(d)
        rows.append(dict(othr=othr, fside="both", sample=sample, n_sig=len(sel),
                         fill_rate=len(d) / max(len(sel), 1),
                         ev_raw=clustered(d, "ev_raw").get("ev_ev"), **st))
        d.to_csv(OUT / f"spikefade_trades_{sample}_o{othr}.csv", index=False)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"spikefade_grid_{sample}.csv", index=False)
    print(res.to_string())


if __name__ == "__main__":
    main()
