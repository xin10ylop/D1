"""(2) Smile source: trade-IV vs mark-IV (inverted from mark_price, Black-76 coin-quoted)
for a validation month inside train (April 2026), BTC+ETH.
Both smile sets fitted with the same fit_smiles code on the same trade sample;
FV rebuilt with seasonal vol-time for both; compare Brier/logloss on panel rows."""
import sys, math, pathlib, time
import numpy as np
import pandas as pd
from scipy.stats import norm

OUT = pathlib.Path("/home/user/D1/results/improve/fv-upgrade")
sys.path.insert(0, str(OUT))
sys.path.insert(0, "/home/user/D1/scripts")
import fv as fvlib
import fv2
from fv2 import (DATA, SPLIT_S, YR, hourly_profile, build_voltime, attach_smiles,
                 core, digital, tte_band)

M0 = pd.Timestamp("2026-04-01", tz="UTC")
M1 = pd.Timestamp("2026-05-01", tz="UTC")


def mark_iv(tr):
    """Invert Black-76 (F=index, r=0, coin-quoted premium) from mark_price via Newton."""
    F = tr["index_price"].values
    K = tr["strike"].values
    tau = (tr["exp_ts"].values - tr["timestamp"].values / 1000.0) / YR
    is_call = (tr["cp"] == "C").values
    # coin premium -> USD premium / F  == normalized price with S=F
    pm = tr["mark_price"].values  # already premium/S in coin terms
    sig = np.where((tr["iv"].values > 1) & (tr["iv"].values < 500),
                   tr["iv"].values / 100.0, 0.6)
    ok = (tau > 1e-5) & (pm > 1e-6) & (F > 0) & (K > 0)
    sig = np.where(ok, sig, np.nan)
    k = np.log(F / K)
    for _ in range(50):
        v = sig * np.sqrt(tau)
        d1 = k / v + v / 2
        d2 = d1 - v
        c_model = norm.cdf(d1) - (K / F) * norm.cdf(d2)   # call premium / F
        model = np.where(is_call, c_model, c_model - 1 + K / F)  # put via parity
        vega = norm.pdf(d1) * np.sqrt(tau)                # d(price/F)/dsig
        step = (model - pm) / np.maximum(vega, 1e-8)
        step = np.clip(step, -0.5, 0.5)
        sig = np.clip(sig - step, 0.005, 5.0)
    v = sig * np.sqrt(tau)
    d1 = k / v + v / 2
    c_model = norm.cdf(d1) - (K / F) * norm.cdf(d1 - v)
    model = np.where(is_call, c_model, c_model - 1 + K / F)
    err = np.abs(model - pm)
    sig[~ok | (err > 1e-4)] = np.nan
    return sig


def main():
    t0s = int(M0.timestamp()) * 1000
    t1s = int(M1.timestamp()) * 1000
    res = {}
    for asset in ["BTC", "ETH"]:
        t0 = time.time()
        tr = fvlib.load_trades(asset)
        tr = tr[(tr["timestamp"] >= t0s - 86400_000) & (tr["timestamp"] < t1s)].copy()
        miv = mark_iv(tr)
        good = np.isfinite(miv)
        print(f"{asset}: {len(tr)} trades, mark-IV inverted {good.mean():.1%}, "
              f"median |markIV-tradeIV| = {np.nanmedian(np.abs(miv*100 - tr['iv'])):.2f} vol pts "
              f"({time.time()-t0:.0f}s)", flush=True)
        # trade-IV smiles (control, same sample)
        sm_trade = fvlib.fit_smiles(tr)
        # mark-IV smiles
        tr2 = tr[good].copy()
        tr2["iv"] = miv[good] * 100
        sm_mark = fvlib.fit_smiles(tr2)
        print(f"{asset}: smiles trade {len(sm_trade)}, mark {len(sm_mark)} "
              f"({time.time()-t0:.0f}s)", flush=True)
        res[asset] = (sm_trade, sm_mark)
        sm_mark.to_parquet(OUT / f"smiles_mark_{asset}_apr26.parquet", index=False)
        sm_trade.to_parquet(OUT / f"smiles_trade_{asset}_apr26.parquet", index=False)

    rows = []
    parts = []
    for asset in ["BTC", "ETH"]:
        sm_trade, sm_mark = res[asset]
        p = pd.read_parquet(DATA / f"panel_{asset}.parquet")
        p = p[(p.result_id >= 0) & (p.ts >= int(M0.timestamp())) &
              (p.T_pm < int(M1.timestamp()))].copy()
        prof = hourly_profile(asset, train_only=True, by_week=True)
        vt = build_voltime(prof, p.ts.min(), p.T_pm.max())
        fvs = {}
        for name, sm in [("trade", sm_trade), ("mark", sm_mark)]:
            sm = sm[(sm["n"] >= 8) & (sm["nk"] >= 4)]
            m = attach_smiles(p, sm)
            co = core(m, voltime=vt)
            fvs[name] = digital(co)
        d = pd.DataFrame({"fv_trade": fvs["trade"], "fv_mark": fvs["mark"],
                          "label": p["label"].values, "tte_d": p["tte_d"].values,
                          "extrap": p["extrap"].values, "asset": asset})
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    d = d.dropna(subset=["fv_trade", "fv_mark"])
    d["band"] = tte_band(d["tte_d"])
    print(f"joint rows {len(d)}")
    for scope, g in [("all", d), ("extrap", d[d.extrap == 1]), ("tte<1d", d[d.tte_d < 1])]:
        for name in ["trade", "mark"]:
            pp = g[f"fv_{name}"].clip(1e-4, 1 - 1e-4)
            y = g["label"]
            rows.append(dict(scope=scope, source=name, n=len(g),
                             brier=float(np.mean((pp - y) ** 2)),
                             logloss=float(-np.mean(y * np.log(pp) + (1 - y) * np.log(1 - pp)))))
    for b, g in d.groupby("band", observed=True):
        for name in ["trade", "mark"]:
            pp = g[f"fv_{name}"].clip(1e-4, 1 - 1e-4)
            y = g["label"]
            rows.append(dict(scope=f"band {b}", source=name, n=len(g),
                             brier=float(np.mean((pp - y) ** 2)),
                             logloss=float(-np.mean(y * np.log(pp) + (1 - y) * np.log(1 - pp)))))
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "train_markiv_apr26.csv", index=False)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
