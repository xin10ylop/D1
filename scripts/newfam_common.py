"""Shared helpers for the new-families research agent (results/improve/new-families/).

Reuses exec_lab conventions: prep_fast panels, Tape fill truth, event-clustered t.
All tuning on train (expiry < 2026-05-16); ONE test look per family at the end.
"""
import sys
import pathlib
import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, "/home/user/D1/scripts")
from exec_lab import prep_fast, Tape, SPLIT, fee, dedup, clustered, load_all  # noqa

ROOT = pathlib.Path("/home/user/D1")
DATA = ROOT / "data"
OUT = ROOT / "results/improve/new-families"
OUT.mkdir(parents=True, exist_ok=True)

SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}


def binance_1m(asset):
    kl = pd.read_parquet(DATA / f"binance/{SYMBOL[asset]}_1m.parquet")
    kl["ts"] = (kl.open_time_us // 1_000_000).astype(int)
    kl = kl.sort_values("ts").reset_index(drop=True)
    return kl


def seasonal_profile(asset, end=SPLIT):
    """Share of daily realized variance per UTC minute-of-day, TRAIN data only.

    Returns w: np.array(1440), sum=1. Smoothed with a 31-minute rolling mean
    (circular) to kill single-minute noise while keeping the funding/US-open humps.
    """
    kl = binance_1m(asset)
    kl = kl[kl.ts < end.timestamp()]
    r = np.log(kl.close / kl.close.shift(1))
    mod = (kl.ts // 60) % 1440
    v = pd.DataFrame({"mod": mod, "r2": r * r}).dropna().groupby("mod").r2.mean()
    v = v.reindex(range(1440)).ffill().bfill()
    # circular smoothing
    vv = pd.concat([v.tail(60), v, v.head(60)]).rolling(31, center=True, min_periods=1).mean()
    v = vv.iloc[60:60 + 1440]
    w = v.values / v.values.sum()
    return w


def seasonal_frac_remaining(w, ts, T_pm):
    """Seasonal share of the final day's variance remaining between ts and T_pm,
    divided by the flat share (minutes/1440). Only valid for tte <= 24h."""
    cw = np.concatenate([[0.0], np.cumsum(w)])  # cum weight up to minute m

    def cum(t):  # cumulative weight at minute-of-day of t
        m = int((t // 60) % 1440)
        return cw[m]
    out = np.empty(len(ts))
    for i, (a, b) in enumerate(zip(ts, T_pm)):
        mins = (b - a) / 60.0
        if mins <= 0 or mins > 1440:
            out[i] = 1.0
            continue
        ca, cb = cum(a), cum(b)
        seas = cb - ca if cb >= ca else (1 - ca) + cb
        flat = mins / 1440.0
        out[i] = seas / flat if flat > 0 else 1.0
    return out


def digital_fv(S, K, sigma, tau):
    v = sigma * np.sqrt(np.maximum(tau, 1e-12))
    with np.errstate(divide="ignore", invalid="ignore"):
        d2 = np.log(S / K) / v - v / 2
    return norm.cdf(d2)
