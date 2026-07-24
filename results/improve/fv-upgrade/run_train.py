"""Train-side evaluation of FV variants: baseline repro, seasonal vol-time,
RV blend grid, Student-t grid. Saves per-variant fv columns for signal re-runs."""
import sys, pathlib, time
import numpy as np
import pandas as pd

OUT = pathlib.Path("/home/user/D1/results/improve/fv-upgrade")
sys.path.insert(0, str(OUT))
sys.path.insert(0, "/home/user/D1/scripts")
import fv2
from fv2 import (ROOT, DATA, SPLIT_S, YR, hourly_profile, build_voltime, ewma_rv,
                 load_smiles, attach_smiles, recompute_fv, calib_table, calib_by, tte_band)

ASSETS = ["BTC", "ETH", "SOL", "XRP"]


def load_asset(a):
    p = pd.read_parquet(DATA / f"panel_{a}.parquet")
    p = p[p.result_id >= 0].copy()
    p["asset"] = a
    return p


def main():
    profiles, voltimes, panels = {}, {}, {}
    for a in ASSETS:
        prof = hourly_profile(a, train_only=True, by_week=True)
        profiles[a] = prof
        p = load_asset(a)
        panels[a] = p
        voltimes[a] = build_voltime(prof, p.ts.min(), p.T_pm.max(), by_week=True)
    np.save(OUT / "profiles_hourweek.npy", np.vstack([profiles[a] for a in ASSETS]))

    # profile summary
    prof_df = pd.DataFrame({a: profiles[a] for a in ASSETS})
    prof_df.to_csv(OUT / "vol_profile_hourweek.csv", index_label="hour_of_week")
    hod = prof_df.groupby(np.arange(168) % 24).mean()
    print("=== hour-of-day variance weight (mean across week, per asset) ===")
    print(hod.round(2).to_string())
    dow = prof_df.groupby(np.arange(168) // 24).mean()
    print("=== day-of-week variance weight ===")
    print(dow.round(2).to_string())

    results = {}
    frames = []
    for a in ASSETS:
        t0 = time.time()
        p = panels[a]
        sm = load_smiles(a)
        m = attach_smiles(p, sm)
        # baseline reproduction check
        fv_base, sig_base = recompute_fv(m)
        m["fv_base"] = fv_base
        m["sig_base"] = sig_base
        ok = np.isfinite(fv_base) & np.isfinite(m["fv"].values)
        diff = np.abs(fv_base[ok] - m["fv"].values[ok])
        print(f"{a}: repro rows {ok.sum()}/{len(m)} max|dfv|={np.nanmax(diff):.4g} "
              f"mean|dfv|={np.nanmean(diff):.2e}  ({time.time()-t0:.0f}s)")
        # seasonal
        fv_sea, sig_sea = recompute_fv(m, voltime=voltimes[a])
        m["fv_sea"] = fv_sea
        m["sig_sea"] = sig_sea
        frames.append(m)
        results[a] = m

    allm = pd.concat(frames, ignore_index=True)
    allm["is_test"] = allm["T_pm"] >= SPLIT_S
    allm["band"] = tte_band(allm["tte_d"])
    tr = allm[~allm.is_test].copy()
    print(f"train rows {len(tr)}, test rows {allm.is_test.sum()}")

    rows = []
    for name, col in [("incumbent(panel)", "fv"), ("repro_clock", "fv_base"),
                      ("seasonal_voltime", "fv_sea")]:
        r = calib_table(tr, col); r["variant"] = name; r["scope"] = "all"; rows.append(r)
        rx = calib_table(tr[tr.extrap == 1], col); rx["variant"] = name; rx["scope"] = "extrap"; rows.append(rx)
        r1 = calib_table(tr[tr.tte_d < 1], col); r1["variant"] = name; r1["scope"] = "tte<1d"; rows.append(r1)
    t1 = pd.DataFrame(rows)[["variant", "scope", "n", "brier", "logloss"]]
    print("=== baseline vs seasonal (train) ===")
    print(t1.to_string(index=False))
    t1.to_csv(OUT / "train_seasonal_summary.csv", index=False)

    print("=== by tte band (train): brier ===")
    tb = []
    for name, col in [("clock", "fv_base"), ("seasonal", "fv_sea")]:
        b = calib_by(tr, col, "band"); b["variant"] = name; tb.append(b)
    tb = pd.concat(tb)
    print(tb.pivot_table(index="band", columns="variant", values="brier", observed=True).round(5).to_string())
    tb.to_csv(OUT / "train_seasonal_by_band.csv", index=False)

    allm.to_parquet(OUT / "panel_fv_variants.parquet", index=False)
    print("saved panel_fv_variants.parquet")


if __name__ == "__main__":
    main()
