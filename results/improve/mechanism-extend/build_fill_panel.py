"""Build 15-min fill panel for old markets: fill VWAP per slot + options FV.

FV logic mirrors build_panel2 (total-variance interpolation between straddling
Deribit expiries, skew-corrected digital), except carry=0 (no dated-futures
bars for the extension era; sub-8d carry effect is <0.1% of spot).

Sources per grid slot ts (=slot start):
  - fills within [ts, ts+900) from data/pm_fills_old/
  - smile at grid_ts=ts from smiles_ext_{asset} + existing smiles_{asset}
  - S_idx from index1m_ext_{asset} + existing index1m_{asset} (ffill, <=10min stale)
  - S_bin from Binance 1m close (candle close at or before ts)
Output: results/improve/mechanism-extend/fill_panel_{asset}.parquet
"""
import sys, glob
import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA

ME = DATA.parent / "results/improve/mechanism-extend"
GRID = 900


def load_fills(slugs):
    parts = []
    want = set(slugs)
    for f in glob.glob(str(DATA / "pm_fills_old/polymarket_onchain_fills_*.parquet")):
        slug = f.split("/")[-1][len("polymarket_onchain_fills_YYYY-MM-DD_"):-len("_Yes.parquet")]
        if slug not in want:
            continue
        try:
            d = pd.read_parquet(f, columns=["block_timestamp_us", "price", "amount",
                                            "taker_side"])
        except Exception:
            continue
        if len(d) == 0:
            continue
        d["slug"] = slug
        parts.append(d)
    fills = pd.concat(parts, ignore_index=True)
    fills["price"] = pd.to_numeric(fills["price"], errors="coerce")
    fills["size"] = pd.to_numeric(fills["amount"], errors="coerce")
    fills = fills[(fills.price > 0) & (fills.price < 1) & (fills["size"] > 0)]
    fills["ts_s"] = fills["block_timestamp_us"] / 1e6
    return fills


def slot_agg(fills):
    fills = fills.copy()
    fills["ts"] = (fills["ts_s"] // GRID).astype(int) * GRID
    fills["pv"] = fills.price * fills["size"]
    is_buy = fills.taker_side.astype(str) == "buy"
    fills["pv_buy"] = fills.pv.where(is_buy, 0.0)
    fills["sz_buy"] = fills["size"].where(is_buy, 0.0)
    g = fills.sort_values("ts_s").groupby(["slug", "ts"])
    out = g.agg(pv=("pv", "sum"), sz=("size", "sum"), n=("price", "size"),
                last=("price", "last"), pv_buy=("pv_buy", "sum"),
                sz_buy=("sz_buy", "sum")).reset_index()
    out["vwap"] = out.pv / out.sz
    out["vwap_buy"] = np.where(out.sz_buy > 0, out.pv_buy / np.maximum(out.sz_buy, 1e-12), np.nan)
    return out.drop(columns=["pv", "pv_buy"])


def load_series(asset):
    sym = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}[asset]
    parts = []
    for suf in ["_old", ""]:
        f = DATA / f"binance/{sym}_1m{suf}.parquet"
        if f.exists():
            parts.append(pd.read_parquet(f, columns=["open_time_us", "close"]))
    kl = pd.concat(parts).drop_duplicates("open_time_us").sort_values("open_time_us")
    bin1m = pd.Series(kl["close"].values, index=(kl["open_time_us"].values // 1_000_000 + 60))
    # index
    def to_secs(x):
        return pd.DatetimeIndex(x).as_unit("ns").asi8 // 10**9

    iparts = []
    for suf in ["24", ""]:
        fe = DATA / f"index1m_ext{suf}_{asset}.parquet"
        if fe.exists():
            d = pd.read_parquet(fe)
            tcol = [c for c in d.columns if c != "index"][0]
            iparts.append(pd.Series(d["index"].values, index=to_secs(d[tcol])))
    fx = DATA / f"index1m_{asset}.parquet"
    if fx.exists():
        d = pd.read_parquet(fx)
        if "index" in d.columns:
            iparts.append(pd.Series(d["index"].values, index=to_secs(d.index)))
    idx = pd.concat(iparts).sort_index()
    idx = idx[~idx.index.duplicated(keep="last")]
    return bin1m, idx


def load_smiles(asset):
    parts = []
    for suf in ["24", ""]:
        fe = DATA / f"smiles_ext{suf}_{asset}.parquet"
        if fe.exists():
            parts.append(pd.read_parquet(fe))
    fx = DATA / f"smiles_{asset}.parquet"
    if fx.exists():
        parts.append(pd.read_parquet(fx))
    sm = pd.concat(parts, ignore_index=True)
    sm = sm[(sm["n"] >= 8) & (sm["nk"] >= 4)]
    sm = sm.drop_duplicates(["exp_ts", "grid_ts"], keep="first")
    return sm


def build(asset):
    u = pd.read_parquet(ME / "universe_verified.parquet")
    u = u[u.asset == asset]
    epoch = pd.Timestamp(0, tz="UTC")
    u = u.assign(T_open=(u["end_dt"] - epoch) // pd.Timedelta(seconds=1))
    u["T_pm"] = u["T_open"] + 60
    meta = u.set_index("slug")[["strike", "T_pm", "label", "S_T"]]

    fills = load_fills(u.slug.tolist())
    sl = slot_agg(fills)
    sl = sl.join(meta, on="slug")
    sl = sl[sl.ts < sl.T_pm]
    sl = sl[sl.ts >= sl.T_pm - 9.4 * 86400]

    bin1m, idx = load_series(asset)
    sm = load_smiles(asset)
    sm_by_grid = {int(g): sg.sort_values("exp_ts").reset_index(drop=True)
                  for g, sg in sm.groupby("grid_ts")}

    # asof lookups
    def asof(series, ts_arr, max_stale):
        pos = np.searchsorted(series.index.values, ts_arr, side="right") - 1
        ok = pos >= 0
        vals = np.full(len(ts_arr), np.nan)
        stale = np.full(len(ts_arr), np.inf)
        vals[ok] = series.values[pos[ok]]
        stale[ok] = ts_arr[ok] - series.index.values[pos[ok]]
        vals[stale > max_stale] = np.nan
        return vals

    ts_arr = sl.ts.values.astype("int64")
    sl["S_bin"] = asof(bin1m, ts_arr, 600)
    sl["S_idx"] = asof(idx, ts_arr, 600)
    sl = sl.dropna(subset=["S_bin", "S_idx"])

    out_parts = []
    for gts, rows in sl.groupby("ts"):
        sg = sm_by_grid.get(int(gts))
        if sg is None:
            continue
        exp_arr = sg["exp_ts"].values.astype(float)
        A, B, C = sg["a"].values, sg["b"].values, sg["c"].values
        XLO, XHI = sg["xlo"].values, sg["xhi"].values
        n = len(rows)
        ts_s = np.full(n, float(gts))
        T = rows["T_pm"].values.astype(float)
        tau = (T - ts_s) / (365 * 86400.0)
        S = rows["S_idx"].values
        Sb = rows["S_bin"].values
        K_eff = rows["strike"].values * S / Sb
        F = S  # carry = 0
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

        part = rows.copy()
        part["fv"] = fvv
        part["sigma"] = sigv
        part["x"] = xq
        part["extrap"] = extrap
        part["tte_d"] = tau * 365
        out_parts.append(part[part["fv"].notna()])

    out = pd.concat(out_parts, ignore_index=True)
    out["asset"] = asset
    out = out.rename(columns={"strike": "K"})
    return out


if __name__ == "__main__":
    for asset in sys.argv[1:]:
        out = build(asset)
        f = ME / f"fill_panel_{asset}.parquet"
        out.to_parquet(f, index=False)
        print("saved", f, len(out), "rows,", out.slug.nunique(), "markets", flush=True)
