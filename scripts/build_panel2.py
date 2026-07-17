"""Vectorized master panel builder (replaces build_panel.py for full runs).

Groups by 15-min grid timestamp; within each grid point evaluates all live
markets against the smile table with numpy. ~100x faster than row loops.
Usage: python3 scripts/build_panel2.py BTC [d_from] [d_to]
"""
import sys, math, glob
import numpy as np
import pandas as pd
from scipy.stats import norm
from common import DATA
import fv as fvlib

GRID_S = 900


def load_all_bars(asset, slugs):
    fs = []
    for s in slugs:
        fs += sorted(glob.glob(str(DATA / f"pm_bars/{s}_*.parquet")))
    if not fs:
        return None
    parts = [pd.read_parquet(f) for f in fs]
    b = pd.concat(parts, ignore_index=True)
    b["ts"] = pd.to_datetime(b["ts"], utc=True)
    return b


def to_grid(bars, uni):
    """Per market: reindex minutes -> ffill -> take 15-min grid rows."""
    out = []
    end_map = dict(zip(uni.slug, uni.end_dt))
    for slug, g in bars.groupby("slug"):
        g = g.sort_values("ts").set_index("ts")
        g = g[~g.index.duplicated(keep="last")]
        end = end_map.get(slug)
        if end is None or len(g) == 0:
            continue
        grid = pd.date_range(g.index[0].ceil("15min"), min(g.index[-1] + pd.Timedelta("60min"), end),
                             freq="15min", tz="UTC")
        if len(grid) == 0:
            continue
        gg = g.reindex(g.index.union(grid)).sort_index().ffill().reindex(grid)
        gg["slug"] = slug
        out.append(gg.reset_index().rename(columns={"index": "ts"}))
    return pd.concat(out, ignore_index=True)


def build(asset, d_from=None, d_to=None):
    uni = pd.read_parquet(DATA / "pm_universe.parquet")
    uni = uni[uni.asset == asset].copy()
    uni["end_dt"] = pd.to_datetime(uni["end_dt"], utc=True)
    if d_from:
        uni = uni[uni["end_dt"] >= pd.Timestamp(d_from, tz="UTC")]
    if d_to:
        uni = uni[uni["end_dt"] <= pd.Timestamp(d_to, tz="UTC")]

    smiles = pd.read_parquet(DATA / f"smiles_{asset}.parquet")
    smiles = smiles[(smiles["n"] >= 8) & (smiles["nk"] >= 4)]
    idx = pd.read_parquet(DATA / f"index1m_{asset}.parquet")["index"]
    symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}[asset]
    kl = pd.read_parquet(DATA / f"binance/{symbol}_1m.parquet")
    bin1m = pd.Series(kl["close"].values,
                      index=pd.to_datetime(kl["open_time_us"] + 60_000_000, unit="us", utc=True))
    carry = fvlib.carry_curve(asset)
    if carry is not None:
        carry = carry.sort_values(["ts_ms", "tau"])

    bars = load_all_bars(asset, uni.slug.tolist())
    if bars is None:
        return pd.DataFrame()
    grid = to_grid(bars, uni)
    meta = uni.set_index("slug")[["strike", "end_dt", "result_id"]]
    grid = grid.join(meta, on="slug")
    epoch = pd.Timestamp(0, tz="UTC")
    grid["T_pm"] = (grid["end_dt"] - epoch) // pd.Timedelta(seconds=1) + 60
    grid["ts_s"] = (grid["ts"] - epoch) // pd.Timedelta(seconds=1)
    grid = grid[grid["ts_s"] < grid["T_pm"]]

    # attach index + binance prices (asof joins on the union index)
    grid = grid.sort_values("ts")
    grid["S_idx"] = idx.reindex(grid["ts"], method="ffill").values
    grid["S_bin"] = bin1m.reindex(grid["ts"], method="ffill").values
    grid = grid.dropna(subset=["S_idx", "S_bin", "bid", "ask"])
    grid = grid[(grid["S_idx"] > 0) & (grid["S_bin"] > 0) & (grid["strike"] > 0)]

    # smile lookup structures
    sm_by_grid = {}
    for gts, sg in smiles.groupby("grid_ts"):
        sm_by_grid[int(gts)] = sg.sort_values("exp_ts").reset_index(drop=True)

    # carry lookup per hour
    def carry_arrays(h):
        if carry is None:
            return None
        w = carry[(carry.ts_ms >= (h - 7200) * 1000) & (carry.ts_ms <= (h + 3600) * 1000)]
        if len(w) == 0:
            return None
        w = w.sort_values("tau")
        return w["tau"].values, w["carry"].values

    out_parts = []
    for gts, rows in grid.groupby(grid["ts_s"] // GRID_S * GRID_S):
        sg = sm_by_grid.get(int(gts))
        if sg is None:
            continue
        exp_arr = sg["exp_ts"].values.astype(float)
        A, B, C = sg["a"].values, sg["b"].values, sg["c"].values
        XLO, XHI = sg["xlo"].values, sg["xhi"].values
        ca = carry_arrays(int(gts // 3600 * 3600))
        n = len(rows)
        ts_s = rows["ts_s"].values.astype(float)
        T = rows["T_pm"].values.astype(float)
        tau = (T - ts_s) / (365 * 86400.0)
        S = rows["S_idx"].values
        Sb = rows["S_bin"].values
        K_eff = rows["strike"].values * S / Sb
        cr = np.interp(tau, ca[0], ca[1]) if ca is not None else np.zeros(n)
        F = S * np.exp(cr * tau)
        xq = np.log(K_eff / F)

        i_above = np.searchsorted(exp_arr, T, side="left")
        i_below = i_above - 1
        ok_above = i_above < len(exp_arr)
        ok_below = i_below >= 0

        def sig_at(idx_arr, x):
            a = A[idx_arr]; b = B[idx_arr]; c = C[idx_arr]
            xl = np.clip(x, XLO[idx_arr], XHI[idx_arr])
            return np.clip(a + b * xl + c * xl * xl, 0.01, 5.0)

        fvv = np.full(n, np.nan)
        sigv = np.full(n, np.nan)
        extrap = np.zeros(n, dtype=int)

        both = ok_above & ok_below
        onlyA = ok_above & ~ok_below
        for mask, mode in ((both, "interp"), (onlyA, "extrap")):
            if not mask.any():
                continue
            if mode == "interp":
                iA, iB = i_above[mask], i_below[mask]
                tA = (exp_arr[iA] - ts_s[mask]) / (365 * 86400.0)
                tB = (exp_arr[iB] - ts_s[mask]) / (365 * 86400.0)
                x = xq[mask]
                wB = sig_at(iB, x) ** 2 * tB
                wA = sig_at(iA, x) ** 2 * tA
                frac = (tau[mask] - tB) / np.maximum(tA - tB, 1e-9)
                w = wB + (wA - wB) * frac
                wB2 = sig_at(iB, x + 0.01) ** 2 * tB
                wA2 = sig_at(iA, x + 0.01) ** 2 * tA
                w2 = wB2 + (wA2 - wB2) * frac
            else:
                iA = i_above[mask]
                tA = (exp_arr[iA] - ts_s[mask]) / (365 * 86400.0)
                x = xq[mask]
                w = sig_at(iA, x) ** 2 * tau[mask]
                w2 = sig_at(iA, x + 0.01) ** 2 * tau[mask]
                extrap[mask] = 1
            sig = np.sqrt(np.maximum(w, 1e-10) / tau[mask])
            sig2 = np.sqrt(np.maximum(w2, 1e-10) / tau[mask])
            slope = (sig2 - sig) / 0.01
            v = sig * np.sqrt(tau[mask])
            d2 = np.log(F[mask] / K_eff[mask]) / v - v / 2
            dig = norm.cdf(d2) - norm.pdf(d2) * np.sqrt(tau[mask]) * slope
            fvv[mask] = np.clip(dig, 0.0, 1.0)
            sigv[mask] = sig

        part = rows[["slug", "strike", "T_pm", "ts_s", "bid", "bid_sz", "ask", "ask_sz",
                     "result_id"]].copy()
        part["fv"] = fvv
        part["sigma"] = sigv
        part["x"] = xq
        part["S_idx"] = S
        part["S_bin"] = Sb
        part["extrap"] = extrap
        part["tte_d"] = tau * 365
        out_parts.append(part[part["fv"].notna()])

    out = pd.concat(out_parts, ignore_index=True)
    out = out.rename(columns={"strike": "K", "ts_s": "ts"})
    out["result_id"] = pd.to_numeric(out["result_id"], errors="coerce").fillna(-1).astype(int)
    out["label"] = (out["result_id"] == 0).astype(int)
    return out


if __name__ == "__main__":
    asset = sys.argv[1]
    d_from = sys.argv[2] if len(sys.argv) > 2 else None
    d_to = sys.argv[3] if len(sys.argv) > 3 else None
    out = build(asset, d_from, d_to)
    f = DATA / f"panel_{asset}.parquet"
    out.to_parquet(f, index=False)
    print("saved", f, len(out), "rows,", out["slug"].nunique(), "markets", flush=True)
