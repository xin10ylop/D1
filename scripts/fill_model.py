"""Depth-aware fill models from book_snapshot_25 files.

taker_vwap: walk the ask (YES buy) or bid (NO buy via 1-bid) levels at the
snapshot nearest AFTER the signal time; returns (vwap, filled_shares) for a
requested $ notional. Missing book => no fill (conservative).
"""
import glob
import numpy as np
import pandas as pd
from common import DATA

_cache = {}


def _load_book(slug, date):
    key = (slug, date)
    if key in _cache:
        return _cache[key]
    f = DATA / f"pm_books/polymarket_book_snapshot_25_{date}_{slug}_Yes.parquet"
    if not f.exists():
        _cache[key] = None
        return None
    cols = ["timestamp_us"] + [f"{s}_{i}" for s in ("ask_price", "ask_size", "bid_price", "bid_size") for i in range(25)]
    try:
        b = pd.read_parquet(f)
        b = b[[c for c in cols if c in b.columns]]
        for c in b.columns:
            b[c] = pd.to_numeric(b[c], errors="coerce")
        b = b.sort_values("timestamp_us").reset_index(drop=True)
    except Exception:
        b = None
    _cache[key] = b
    if len(_cache) > 40:
        _cache.pop(next(iter(_cache)))
    return b


def taker_vwap(slug, ts, side, notional):
    """side='yes': buy YES at asks. side='no': buy NO = hit YES bids at (1-p).
    Returns (vwap_price_paid, shares, levels_used) or None."""
    date = pd.Timestamp(ts, unit="s", tz="UTC").strftime("%Y-%m-%d")
    b = _load_book(slug, date)
    if b is None:
        return None
    i = b["timestamp_us"].searchsorted(int(ts * 1e6))
    if i >= len(b):
        return None
    row = b.iloc[i]
    px_pref, sz_pref = ("ask_price", "ask_size") if side == "yes" else ("bid_price", "bid_size")
    remaining = notional
    cost = 0.0
    shares = 0.0
    lv = 0
    for k in range(25):
        p_, s_ = row.get(f"{px_pref}_{k}", np.nan), row.get(f"{sz_pref}_{k}", np.nan)
        if not (np.isfinite(p_) and np.isfinite(s_)) or s_ <= 0:
            break
        unit = p_ if side == "yes" else (1 - p_)
        if unit <= 0:
            break
        take = min(s_, remaining / unit)
        cost += take * unit
        shares += take
        remaining -= take * unit
        lv += 1
        if remaining <= 1e-9:
            break
    if shares <= 0:
        return None
    return (cost / shares, shares, lv)
