"""Execution lab: order placement / exit studies using pm_trades tick tape as fill truth.

New file (does not touch live-bot scripts). See results/improve/execution-lab/findings.md.

Fill truth conventions (YES token tape):
  - We rest a BID at limit L: filled if a 'sell' print (taker sold YES) at price <= L
    occurs in (t0, t0+H]. Fill price = L (we were the best bid at L by construction).
  - We rest an ASK at limit A: filled if a 'buy' print at price >= A occurs in window.
Signal on bar t (panel 15-min grid, quotes as-of ts) => order placed at ts, prints
strictly after ts count. Taker counterfactual uses next-bar quotes (n_ask), as incumbent.
"""
import glob
import pathlib
import re
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = pathlib.Path("/home/user/D1")
DATA = ROOT / "data"
SPLIT = pd.Timestamp("2026-05-16", tz="UTC")
FEE_RATE = 0.07


def fee(p):
    return FEE_RATE * p * (1 - p)


def prep_fast(asset):
    """Vectorized version of battery.prep (identical semantics)."""
    p = pd.read_parquet(DATA / f"panel_{asset}.parquet")
    p = p[p.result_id >= 0].copy()
    p["asset"] = asset
    p["mid"] = (p.bid + p.ask) / 2
    p["spread"] = p.ask - p.bid
    symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}[asset]
    kl = pd.read_parquet(DATA / f"binance/{symbol}_1m.parquet")
    close_map = dict(zip((kl["open_time_us"] // 1000000 + 60).astype(int), kl["close"]))
    p["S_T"] = p["T_pm"].astype(int).map(close_map)
    p = p.sort_values(["slug", "ts"]).reset_index(drop=True)
    g = p.groupby("slug")
    for c in ["bid", "ask"]:
        p["n_" + c] = g[c].shift(-1)
    p["n_ts"] = g["ts"].shift(-1)
    ok = (p["n_ts"] - p["ts"]) <= 1800
    for c in ["n_bid", "n_ask"]:
        p.loc[~ok, c] = np.nan
    p["exp_dt"] = pd.to_datetime(p["T_pm"], unit="s", utc=True)
    p["is_test"] = p["exp_dt"] >= SPLIT
    p["event"] = p["exp_dt"].dt.date
    p["gap_yes"] = p["fv"] - p["ask"]
    # vectorized digital $-delta
    tau = p.tte_d.values / 365.0
    v = p.sigma.values * np.sqrt(np.maximum(tau, 0))
    with np.errstate(divide="ignore", invalid="ignore"):
        d2 = np.log(p.S_bin.values / p.K.values) / v - v / 2
        delta = norm.pdf(d2) / v
    delta[~np.isfinite(delta)] = 0.0
    delta[(tau <= 0) | (p.sigma.values <= 0)] = 0.0
    p["delta"] = delta
    p["ret_T"] = np.log(p["S_T"] / p["S_bin"])
    return p


def dedup(df, cooldown_h=24):
    df = df.sort_values("ts")
    keep, last = [], {}
    for r in df.itertuples():
        if r.slug not in last or r.ts - last[r.slug] >= cooldown_h * 3600:
            keep.append(r.Index)
            last[r.slug] = r.ts
    return df.loc[keep]


def signals_s1(p, sample):
    """Incumbent taker-YES: gap>0.05, 3<=tte<8."""
    m = p[p.is_test] if sample == "test" else p[~p.is_test]
    m = m[(m.ask > 0.02) & (m.ask < 0.98) & (m.spread <= 0.05)]
    sel = m[(m.gap_yes > 0.05) & (m.tte_d >= 3) & (m.tte_d < 8)]
    return dedup(sel)


def signals_s2(p, sample):
    """Incumbent maker-YES: fv - (bid+0.01) > 0.025, tte<8."""
    m = p[p.is_test] if sample == "test" else p[~p.is_test]
    m = m[(m.ask > 0.02) & (m.ask < 0.98) & (m.spread <= 0.05)]
    sel = m[(m.fv - (m.bid + 0.01) > 0.025) & (m.tte_d < 8)]
    return dedup(sel)


# ---------------- tape store ----------------
class Tape:
    def __init__(self):
        fs = glob.glob(str(DATA / "pm_trades" / "*"))
        self.files = {}       # slug -> {date_str: path or None(empty)}
        pat = re.compile(r"polymarket_trades_(\d{4}-\d{2}-\d{2})_(.+)_Yes\.(parquet|empty)$")
        for f in fs:
            m = pat.search(f)
            if not m:
                continue
            date, slug, ext = m.groups()
            self.files.setdefault(slug, {})[date] = f if ext == "parquet" else None
        self._cache = {}

    def coverage(self, slug, t0_s, t1_s):
        """All UTC dates in [t0,t1] have a tape file (parquet or empty)?"""
        d = self.files.get(slug)
        if d is None:
            return False
        dates = pd.date_range(pd.Timestamp(t0_s, unit="s").floor("D"),
                              pd.Timestamp(t1_s, unit="s").floor("D"), freq="D")
        return all(x.strftime("%Y-%m-%d") in d for x in dates)

    def trades(self, slug):
        if slug in self._cache:
            return self._cache[slug]
        d = self.files.get(slug, {})
        parts = []
        for date, f in sorted(d.items()):
            if f is None:
                continue
            t = pd.read_parquet(f, columns=["timestamp_us", "price", "size", "side"])
            parts.append(t)
        if parts:
            t = pd.concat(parts, ignore_index=True)
            t["price"] = t["price"].astype(float)
            t["size"] = t["size"].astype(float)
            t = t.sort_values("timestamp_us").reset_index(drop=True)
        else:
            t = pd.DataFrame(columns=["timestamp_us", "price", "size", "side"])
        if len(self._cache) > 200:
            self._cache.pop(next(iter(self._cache)))
        self._cache[slug] = t
        return t

    def first_bid_fill(self, slug, t0_s, t1_s, limit, strict=False):
        """First 'sell' print at price <= limit (strict: < limit) in (t0, t1].

        strict=True is the queue-join case (our limit does not improve the best
        bid): require a trade-through below our level, which by price priority
        implies our order was cleared. Returns fill time (s) or None."""
        t = self.trades(slug)
        ok = (t.price < limit - 1e-9) if strict else (t.price <= limit + 1e-9)
        w = t[(t.timestamp_us > t0_s * 1e6) & (t.timestamp_us <= t1_s * 1e6)
              & (t.side == "sell") & ok]
        return None if len(w) == 0 else w.timestamp_us.iloc[0] / 1e6

    def first_ask_fill(self, slug, t0_s, t1_s, limit, strict=False):
        """First 'buy' print at price >= limit (strict: > limit) in (t0, t1]."""
        t = self.trades(slug)
        ok = (t.price > limit + 1e-9) if strict else (t.price >= limit - 1e-9)
        w = t[(t.timestamp_us > t0_s * 1e6) & (t.timestamp_us <= t1_s * 1e6)
              & (t.side == "buy") & ok]
        return None if len(w) == 0 else w.timestamp_us.iloc[0] / 1e6


def clustered(d, col="ev_hedged"):
    if len(d) == 0:
        return {}
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / max(np.sqrt(len(g)), 1)
    return {"n": len(d), "n_events": len(g), "ev_mean": d[col].mean(),
            "ev_ev": g.mean(), "se": se,
            "t": g.mean() / se if se > 0 else np.nan,
            "hit": d.label.mean() if "label" in d and d.label.notna().any() else np.nan}


def load_all(assets=("BTC", "ETH", "SOL", "XRP")):
    return pd.concat([prep_fast(a) for a in assets], ignore_index=True)
