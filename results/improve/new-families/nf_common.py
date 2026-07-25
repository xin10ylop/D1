"""Corrected shared helpers for the new-families research (post code-audit).

Differences vs the earlier in-flight newfam_* run (whose CSVs are superseded):
  - prep(): AUDIT FIX C1 — crossed next-bar quotes (n_bid > n_ask) nulled; every
    signal filter downstream must ALSO require spread >= 0 at the signal bar.
  - AUDIT FIX M2 — hedge P&L is LINEAR: short perp of $delta notional earns
    -delta*(S_T/S_bin - 1) per YES share (+ for the NO side, which longs the perp).
    Log-hedge kept only as a reference column.
  - Fill-time hedge diagnostic for maker fills (S at fill minute from Binance 1m).
Train = expiry < 2026-05-16; tune on train only; ONE frozen test look per family.
"""
import glob
import pathlib
import re
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, "/home/user/D1/scripts")
from exec_lab import Tape, fee, FEE_RATE  # tape fill truth (print tape, audited)  # noqa

ROOT = pathlib.Path("/home/user/D1")
DATA = ROOT / "data"
OUT = ROOT / "results/improve/new-families"
OUT.mkdir(parents=True, exist_ok=True)
SPLIT = pd.Timestamp("2026-05-16", tz="UTC")
SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}


def prep(asset):
    """battery.prep semantics, vectorized, with the C1 crossed-quote fix and
    linear-return column. Matches scripts/battery.py:prep (fixed) exactly."""
    p = pd.read_parquet(DATA / f"panel_{asset}.parquet")
    p = p[p.result_id >= 0].copy()
    p["asset"] = asset
    p["mid"] = (p.bid + p.ask) / 2
    p["spread"] = p.ask - p.bid
    kl = pd.read_parquet(DATA / f"binance/{SYMBOL[asset]}_1m.parquet")
    close_map = dict(zip((kl["open_time_us"] // 1000000 + 60).astype(int), kl["close"]))
    p["S_T"] = p["T_pm"].astype(int).map(close_map)
    p = p.sort_values(["slug", "ts"]).reset_index(drop=True)
    g = p.groupby("slug")
    for c in ["bid", "ask", "bid_sz", "ask_sz"]:
        p["n_" + c] = g[c].shift(-1)
    p["n_ts"] = g["ts"].shift(-1)
    ncols = ["n_bid", "n_ask", "n_bid_sz", "n_ask_sz"]
    ok = (p["n_ts"] - p["ts"]) <= 1800
    for c in ncols:
        p.loc[~ok, c] = np.nan
    # AUDIT FIX C1: crossed next-bar book is a phantom (one-sided stale ask); no entry.
    crossed_next = p["n_bid"] > p["n_ask"]
    for c in ncols:
        p.loc[crossed_next, c] = np.nan
    p["exp_dt"] = pd.to_datetime(p["T_pm"], unit="s", utc=True)
    p["is_test"] = p["exp_dt"] >= SPLIT
    p["event"] = p["exp_dt"].dt.date
    p["gap_yes"] = p["fv"] - p["ask"]
    p["gap_no"] = p["bid"] - p["fv"]
    tau = p.tte_d.values / 365.0
    v = p.sigma.values * np.sqrt(np.maximum(tau, 0))
    with np.errstate(divide="ignore", invalid="ignore"):
        d2 = np.log(p.S_bin.values / p.K.values) / v - v / 2
        delta = norm.pdf(d2) / v
    delta[~np.isfinite(delta)] = 0.0
    delta[(tau <= 0) | (p.sigma.values <= 0)] = 0.0
    p["delta"] = delta
    p["ret_T"] = np.log(p["S_T"] / p["S_bin"])          # reference only
    p["retlin_T"] = p["S_T"] / p["S_bin"] - 1.0          # AUDIT FIX M2: tradable hedge
    return p


def load_all(assets=("BTC", "ETH", "SOL", "XRP")):
    return pd.concat([prep(a) for a in assets], ignore_index=True)


_FV_VARIANTS = None


def attach_fv_sea(p):
    """Merge fv_sea/sig_sea (seasonal vol-time FV, adopted by fv-upgrade) onto a
    prep() frame via (asset, slug, ts)."""
    global _FV_VARIANTS
    if _FV_VARIANTS is None:
        _FV_VARIANTS = pd.read_parquet(
            ROOT / "results/improve/fv-upgrade/panel_fv_variants.parquet",
            columns=["asset", "slug", "ts", "fv_sea", "sig_sea"])
    return p.merge(_FV_VARIANTS, on=["asset", "slug", "ts"], how="left")


def dedup(df, cooldown_h=24, key="slug"):
    df = df.sort_values("ts")
    keep, last = [], {}
    for r in df.itertuples():
        k = getattr(r, key)
        if k not in last or r.ts - last[k] >= cooldown_h * 3600:
            keep.append(r.Index)
            last[k] = r.ts
    return df.loc[keep]


def clustered(d, col="ev_hedged"):
    """Mean EV with (asset, expiry-date)-clustered SE."""
    if len(d) == 0:
        return {}
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / max(np.sqrt(len(g)), 1)
    return {"n": len(d), "n_events": len(g), "ev_mean": d[col].mean(),
            "ev_ev": g.mean(), "se": se,
            "t": g.mean() / se if se > 0 else np.nan,
            "hit": d.label.mean() if "label" in d and d.label.notna().any() else np.nan}


class FastTape:
    """Per-slug sorted print arrays; vectorized window fill checks."""

    def __init__(self, tape):
        self.tape = tape
        self.cache = {}

    def get(self, slug):
        if slug not in self.cache:
            if len(self.cache) > 1500:
                self.cache.pop(next(iter(self.cache)))
            t = self.tape.trades(slug)
            b = t[t.side == "buy"]
            s = t[t.side == "sell"]
            self.cache[slug] = dict(
                bt=b.timestamp_us.values / 1e6, bp=b.price.values,
                st=s.timestamp_us.values / 1e6, sp=s.price.values)
        return self.cache[slug]

    def bid_fill_t(self, slug, t0, t1, lim):
        """First time in (t0,t1] a 'sell' print occurs at price <= lim, else None."""
        a = self.get(slug)
        i0, i1 = np.searchsorted(a["st"], [t0 + 1e-9, t1 + 1e-9])
        w = np.nonzero(a["sp"][i0:i1] <= lim + 1e-9)[0]
        return None if len(w) == 0 else float(a["st"][i0 + w[0]])

    def ask_fill_t(self, slug, t0, t1, lim):
        """First time in (t0,t1] a 'buy' print occurs at price >= lim, else None."""
        a = self.get(slug)
        i0, i1 = np.searchsorted(a["bt"], [t0 + 1e-9, t1 + 1e-9])
        w = np.nonzero(a["bp"][i0:i1] >= lim - 1e-9)[0]
        return None if len(w) == 0 else float(a["bt"][i0 + w[0]])


class Spot1m:
    """Binance 1m close lookup for fill-time hedge diagnostics."""

    def __init__(self):
        self.maps = {}

    def at(self, asset, ts):
        if asset not in self.maps:
            kl = pd.read_parquet(DATA / f"binance/{SYMBOL[asset]}_1m.parquet")
            t = (kl.open_time_us // 1_000_000).astype("int64").values
            self.maps[asset] = (t, kl.close.values)
        t, c = self.maps[asset]
        i = np.searchsorted(t, ts, side="right") - 1
        if i < 0 or i >= len(t) or ts - t[i] > 300:
            return np.nan
        return float(c[i])


def fmt(st, keys=("n", "n_events", "ev_ev", "t", "hit")):
    return {k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in st.items() if k in keys}
