"""Grid search on TRAIN: (3) EWMA-RV blend (lambda + per-band w), (4) Student-t df.
Both on top of the seasonal vol-time base. Row-level Brier/logloss on train."""
import sys, pathlib, time
import numpy as np
import pandas as pd

OUT = pathlib.Path("/home/user/D1/results/improve/fv-upgrade")
sys.path.insert(0, str(OUT))
import fv2
from fv2 import (DATA, SPLIT_S, hourly_profile, build_voltime, ewma_rv, load_smiles,
                 attach_smiles, core, digital, calib_table, tte_band)

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
HLS = [120, 360, 1440, 4320]          # EWMA half-life in minutes
WS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]   # weight on IV
DFS = [3, 4, 6, 8, 12]                # Student-t df
BANDS = [(0, 1), (1, 3), (3, 8), (8, 100)]


def main():
    cores, rvs, metas = {}, {}, {}
    for a in ASSETS:
        p = pd.read_parquet(DATA / f"panel_{a}.parquet")
        p = p[p.result_id >= 0].copy()
        prof = hourly_profile(a, train_only=True, by_week=True)
        vt = build_voltime(prof, p.ts.min(), p.T_pm.max())
        m = attach_smiles(p, load_smiles(a))
        co = core(m, voltime=vt)
        cores[a] = co
        metas[a] = pd.DataFrame({"label": m["label"].values, "tte_d": m["tte_d"].values,
                                 "extrap": m["extrap"].values, "x": m["x"].values,
                                 "is_test": m["T_pm"].values >= SPLIT_S})
        rvs[a] = {hl: ewma_rv(a, hl) for hl in HLS}
        print(a, "prepped", flush=True)

    # ---------------- RV blend grid: per (hl, w) brier per tte band on train
    rows = []
    for hl in HLS:
        for wv in WS:
            fvs, labs, ttes, tests = [], [], [], []
            for a in ASSETS:
                co = cores[a]
                rvv = np.interp(co["ts"], rvs[a][hl].index.values.astype(float),
                                rvs[a][hl].values)
                sig = wv * co["sig"] + (1 - wv) * rvv
                slope = co["slope"] * wv
                fv = digital(co, sig_override=sig, slope_override=slope)
                fvs.append(fv); labs.append(metas[a]["label"].values)
                ttes.append(metas[a]["tte_d"].values); tests.append(metas[a]["is_test"].values)
            fv = np.concatenate(fvs); lab = np.concatenate(labs)
            tte = np.concatenate(ttes); ist = np.concatenate(tests)
            ok = np.isfinite(fv) & ~ist
            for lo, hi in BANDS:
                b = ok & (tte >= lo) & (tte < hi)
                p = np.clip(fv[b], 1e-4, 1 - 1e-4); y = lab[b]
                rows.append(dict(hl=hl, w=wv, band=f"{lo}-{hi}", n=int(b.sum()),
                                 brier=float(np.mean((p - y) ** 2)),
                                 logloss=float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))))
        print("hl", hl, "done", flush=True)
    bl = pd.DataFrame(rows)
    bl.to_csv(OUT / "train_blend_grid.csv", index=False)
    print("=== RV blend train brier by band (rows=w, cols=hl) ===")
    for band in bl.band.unique():
        piv = bl[bl.band == band].pivot(index="w", columns="hl", values="brier")
        print(f"-- band {band} (n={bl[bl.band==band].n.iloc[0]})")
        print((piv * 1e3).round(4).to_string())

    # ---------------- Student-t grid on seasonal base (no blend)
    rows = []
    fv0, lab0, tte0, x0, ext0, ist0 = [], [], [], [], [], []
    for a in ASSETS:
        co = cores[a]
        lab0.append(metas[a]["label"].values); tte0.append(metas[a]["tte_d"].values)
        x0.append(metas[a]["x"].values); ext0.append(metas[a]["extrap"].values)
        ist0.append(metas[a]["is_test"].values)
    lab = np.concatenate(lab0); tte = np.concatenate(tte0); xx = np.concatenate(x0)
    ist = np.concatenate(ist0)
    for df_ in [None] + DFS:
        fvs = [digital(cores[a], tdf=df_) for a in ASSETS]
        fv = np.concatenate(fvs)
        ok = np.isfinite(fv) & ~ist
        name = "normal" if df_ is None else f"t{df_}"
        p = np.clip(fv[ok], 1e-4, 1 - 1e-4); y = lab[ok]
        rows.append(dict(df=name, scope="all", n=int(ok.sum()),
                         brier=float(np.mean((p - y) ** 2)),
                         logloss=float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))))
        # wings: fv in extreme buckets or |x| large relative to vol-time
        for scope, msk in [("fv<2c", ok & (fv < 0.02)), ("fv>98c", ok & (fv > 0.98)),
                           ("2c-10c", ok & (fv >= 0.02) & (fv < 0.10)),
                           ("90-98c", ok & (fv >= 0.90) & (fv <= 0.98))]:
            p = np.clip(fv[msk], 1e-4, 1 - 1e-4); y = lab[msk]
            if msk.sum() == 0:
                continue
            rows.append(dict(df=name, scope=scope, n=int(msk.sum()),
                             brier=float(np.mean((p - y) ** 2)),
                             logloss=float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
                             mean_fv=float(fv[msk].mean()), mean_label=float(y.mean())))
    tt = pd.DataFrame(rows)
    tt.to_csv(OUT / "train_tails_grid.csv", index=False)
    print("=== Student-t train (seasonal base) ===")
    print(tt.to_string(index=False))


if __name__ == "__main__":
    main()
