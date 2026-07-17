"""Build the master (asset, market, 15-min) panel: PM BBO vs options-implied FV.

Usage: python3 scripts/build_panel.py BTC [date_from] [date_to]
Writes data/panel_{ASSET}.parquet and data/smiles_{ASSET}.parquet (cached).
"""
import sys, math, glob, bisect, datetime as dt
import numpy as np
import pandas as pd
from scipy.stats import norm
from common import DATA
import fv as fvlib

GRID_S = 900


def load_bars(slug):
    fs = sorted(glob.glob(str(DATA / f"pm_bars/{slug}_*.parquet")))
    if not fs:
        return None
    b = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    return b


def build(asset, d_from=None, d_to=None):
    uni = pd.read_parquet(DATA / "pm_universe.parquet")
    uni = uni[uni.asset == asset].copy()
    uni["end_dt"] = pd.to_datetime(uni["end_dt"], utc=True)
    if d_from:
        uni = uni[uni["end_dt"] >= pd.Timestamp(d_from, tz="UTC")]
    if d_to:
        uni = uni[uni["end_dt"] <= pd.Timestamp(d_to, tz="UTC")]

    smile_f = DATA / f"smiles_{asset}.parquet"
    if smile_f.exists():
        smiles = pd.read_parquet(smile_f)
    else:
        tr = fvlib.load_trades(asset)
        smiles = fvlib.fit_smiles(tr)
        smiles.to_parquet(smile_f, index=False)
        # index series cache
        idx = fvlib.index_series(tr)
        idx.rename("index").to_frame().to_parquet(DATA / f"index1m_{asset}.parquet")
    idx = pd.read_parquet(DATA / f"index1m_{asset}.parquet")["index"]

    # binance 1m closes
    symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}[asset]
    kl = pd.read_parquet(DATA / f"binance/{symbol}_1m.parquet")
    kl_t = pd.to_datetime(kl["open_time_us"] + 60_000_000, unit="us", utc=True)  # close time
    bin1m = pd.Series(kl["close"].values, index=kl_t)

    carry = fvlib.carry_curve(asset)

    # smile lookup: grid_ts -> {exp_ts: row}
    smiles = smiles[(smiles["n"] >= 8) & (smiles["nk"] >= 4)]
    by_grid = {}
    for r in smiles.itertuples():
        by_grid.setdefault(int(r.grid_ts), {})[int(r.exp_ts)] = r

    def smile_w(row, x, tau_row):
        sig = float(fvlib.smile_sigma(
            {"a": row.a, "b": row.b, "c": row.c, "xlo": row.xlo, "xhi": row.xhi}, x))
        return sig * sig * tau_row, sig

    rows = []
    for mk in uni.itertuples():
        bars = load_bars(mk.slug)
        if bars is None or len(bars) == 0:
            continue
        bars = bars.set_index(pd.to_datetime(bars["ts"], utc=True)).sort_index()
        T_pm = mk.end_dt.timestamp() + 60  # close of the 12:00 candle
        full = pd.date_range(bars.index[0].ceil("15min"),
                             min(bars.index[-1], mk.end_dt), freq="15min", tz="UTC")
        if len(full) == 0:
            continue
        b15 = bars[~bars.index.duplicated(keep="last")].reindex(
            bars.index.union(full)).sort_index().ffill().reindex(full)
        for t, brow in b15.iterrows():
            ts = t.timestamp()
            tau_pm = (T_pm - ts) / (365 * 86400)
            if tau_pm <= 0 or not np.isfinite(brow.get("bid", np.nan)):
                continue
            g = by_grid.get(int(ts // GRID_S * GRID_S))
            if not g:
                continue
            S = idx.asof(t) if t >= idx.index[0] else np.nan
            Sb = bin1m.asof(t) if t >= bin1m.index[0] else np.nan
            if not (np.isfinite(S) and np.isfinite(Sb)):
                continue
            K_eff = mk.strike * S / Sb  # strike on Deribit underlier
            exps = sorted(g.keys())
            later = [e for e in exps if e >= T_pm]
            earlier = [e for e in exps if e < T_pm]
            crate = fvlib.carry_at(carry, ts * 1000, tau_pm) if carry is not None else 0.0
            F_pm = S * math.exp(crate * tau_pm)
            xq = math.log(K_eff / F_pm)
            extrap = 0
            try:
                if earlier and later:
                    e1, e2 = earlier[-1], later[0]
                    r1, r2 = g[e1], g[e2]
                    t1, t2 = (e1 - ts) / (365 * 86400), (e2 - ts) / (365 * 86400)
                    w1, s1 = smile_w(r1, xq, t1)
                    w2, s2 = smile_w(r2, xq, t2)
                    w = w1 + (w2 - w1) * (tau_pm - t1) / (t2 - t1)
                    wl = smile_w(r1, xq + 0.01, t1)[0] + (smile_w(r2, xq + 0.01, t2)[0] - smile_w(r1, xq + 0.01, t1)[0]) * (tau_pm - t1) / (t2 - t1)
                    sig = math.sqrt(max(w, 1e-8) / tau_pm)
                    sig_p = math.sqrt(max(wl, 1e-8) / tau_pm)
                    slope = (sig_p - sig) / 0.01
                elif later:
                    e2 = later[0]
                    r2 = g[e2]
                    t2 = (e2 - ts) / (365 * 86400)
                    _, sig = smile_w(r2, xq, t2)
                    _, sp = smile_w(r2, xq + 0.01, t2)
                    slope = (sp - sig) / 0.01
                    extrap = 1
                else:
                    continue
                fv = fvlib.digital_above(F_pm, K_eff, tau_pm, sig, slope)
            except Exception:
                continue
            rows.append((mk.slug, mk.strike, T_pm, ts, tau_pm * 365, brow.bid, brow.bid_sz,
                         brow.ask, brow.ask_sz, fv, sig, xq, S, Sb, extrap,
                         int(mk.result_id) if str(mk.result_id).isdigit() else -1))
    cols = ["slug", "K", "T_pm", "ts", "tte_d", "bid", "bid_sz", "ask", "ask_sz",
            "fv", "sigma", "x", "S_idx", "S_bin", "extrap", "result_id"]
    out = pd.DataFrame(rows, columns=cols)
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
