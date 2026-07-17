"""Hourly-ladder panel: 1-min grid, short-horizon FV.

FV sigma: blend of Deribit nearest-expiry ATM vol and EWMA realized vol from
Binance 1m, both scaled by an hour-of-day seasonality profile estimated from
the full 1m history. Labels recomputed from Binance 1h candle closes.
Usage: python3 scripts/build_panel_hourly.py BTC [blend_w]
"""
import sys, glob, math
import numpy as np
import pandas as pd
from scipy.stats import norm
from common import DATA

EPOCH = pd.Timestamp(0, tz="UTC")


def seasonality(sym):
    """Hour-of-UTC-day vol multipliers (mean |1m ret| based, normalized)."""
    kl = pd.read_parquet(DATA / f"binance/{sym}_1m.parquet")
    t = pd.to_datetime(kl["open_time_us"], unit="us", utc=True)
    r = np.log(kl["close"] / kl["close"].shift(1))
    h = t.dt.hour
    prof = r.abs().groupby(h).mean()
    return (prof / prof.mean()).to_dict()


def ewma_vol_1m(sym, lam=0.97):
    """Annualized EWMA sigma from 1m returns, per minute (shifted 1 bar: no lookahead)."""
    kl = pd.read_parquet(DATA / f"binance/{sym}_1m.parquet")
    t = pd.to_datetime(kl["open_time_us"] + 60_000_000, unit="us", utc=True)  # bar close time
    r = np.log(kl["close"] / kl["close"].shift(1))
    var = r.pow(2).ewm(alpha=1 - lam).mean().shift(1)
    sig_ann = np.sqrt(var * 365 * 1440)
    return pd.Series(sig_ann.values, index=t), pd.Series(kl["close"].values, index=t)


def build(asset, blend_w=0.5):
    sym = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}[asset]
    uni = pd.read_parquet(DATA / "pm_universe_hourly.parquet")
    uni = uni[uni.asset == asset].copy()
    uni["end_dt"] = pd.to_datetime(uni["end_dt"], utc=True)
    uni = uni[uni.strike.notna() & (uni.strike > 0)]

    prof = seasonality(sym)
    rv, close1m = ewma_vol_1m(sym)

    # labels from 1h candles: candle ENDS at T (open at T-1h)
    kl1h = pd.read_parquet(DATA / f"binance/{sym}_1h.parquet")
    close_at_end = dict(zip((kl1h["open_time_us"] // 1000000 + 3600).astype(int), kl1h["close"]))

    # Deribit nearest-expiry ATM vol from smiles
    sm = pd.read_parquet(DATA / f"smiles_{asset}.parquet")
    sm = sm[(sm.n >= 8)].sort_values(["grid_ts", "exp_ts"])
    atm = sm.groupby("grid_ts").first().reset_index()  # nearest expiry each grid ts
    atm_v = pd.Series(atm["a"].values, index=pd.to_datetime(atm["grid_ts"], unit="s", utc=True))

    rows = []
    for mk in uni.itertuples():
        fs = sorted(glob.glob(str(DATA / f"pm_bars_hourly/{mk.slug}_*.parquet")))
        if not fs:
            continue
        b = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
        b["ts"] = pd.to_datetime(b["ts"], utc=True)
        b = b.sort_values("ts").set_index("ts")
        b = b[~b.index.duplicated(keep="last")]
        T = int((mk.end_dt - EPOCH) // pd.Timedelta(seconds=1))
        S_T = close_at_end.get(T)
        label = int(S_T > mk.strike) if S_T is not None else -1
        grid = pd.date_range(b.index[0].ceil("1min"), mk.end_dt, freq="1min", tz="UTC")
        if len(grid) == 0:
            continue
        g = b.reindex(b.index.union(grid)).sort_index().ffill().reindex(grid)
        g = g.dropna(subset=["bid", "ask"])
        if len(g) == 0:
            continue
        ts_s = (g.index - EPOCH) // pd.Timedelta(seconds=1)
        tau = (T - ts_s.values) / (365 * 86400.0)
        ok = tau > 0
        g = g[ok]; tau = tau[ok]; ts_arr = ts_s.values[ok]
        S = close1m.reindex(g.index, method="ffill").values
        sig_rv = rv.reindex(g.index, method="ffill").values
        sig_iv = atm_v.reindex(g.index, method="ffill").values
        hours = g.index.hour.values
        mult = np.array([prof.get(h, 1.0) for h in hours])
        sig = (blend_w * np.nan_to_num(sig_iv, nan=np.nanmedian(sig_iv))
               + (1 - blend_w) * sig_rv) * mult
        v = sig * np.sqrt(tau)
        with np.errstate(divide="ignore", invalid="ignore"):
            d2 = np.log(S / mk.strike) / v - v / 2
        fv = norm.cdf(d2)
        rows.append(pd.DataFrame(dict(
            slug=mk.slug, K=mk.strike, T_pm=T, ts=ts_arr, tte_m=tau * 365 * 1440,
            bid=g.bid.values, bid_sz=g.bid_sz.values, ask=g.ask.values, ask_sz=g.ask_sz.values,
            fv=fv, sigma=sig, S_bin=S, label=label, result_id=mk.result_id)))
    out = pd.concat(rows, ignore_index=True)
    return out


if __name__ == "__main__":
    asset = sys.argv[1]
    w = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
    out = build(asset, w)
    f = DATA / f"panel_hourly_{asset}.parquet"
    out.to_parquet(f, index=False)
    ok = out[out.label >= 0]
    import numpy as np
    print("saved", f, len(out), "rows", out.slug.nunique(), "markets")
    print("brier fv:", round(np.mean((ok.fv - ok.label) ** 2), 4),
          "| brier mid:", round(np.mean(((ok.bid + ok.ask) / 2 - ok.label) ** 2), 4))
