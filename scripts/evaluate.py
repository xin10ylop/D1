"""Hypothesis evaluation harness.

Convention: every strategy evaluation appends a row to results/results_table.csv.
Buy-YES-at-ask EV: label - ask - fee(ask); Buy-NO (at 1-bid): (1-label) - (1-bid) - fee(1-bid).
fee(p) = FEE_RATE * p * (1-p)  [taker]; maker fee = 0.
"""
import pandas as pd
import numpy as np
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RES = ROOT / "results"; RES.mkdir(exist_ok=True)
TABLE = RES / "results_table.csv"
FEE_RATE = 0.07


def fee(p, taker=True):
    return FEE_RATE * p * (1 - p) if taker else 0.0


def log_result(hyp, name, sample, n, hit, ev, notes="", **kw):
    row = {"hypothesis": hyp, "strategy": name, "sample": sample, "n_trades": n,
           "hit_rate": round(hit, 4) if hit == hit else np.nan,
           "ev_per_share": round(ev, 5) if ev == ev else np.nan, "notes": notes}
    row.update(kw)
    df = pd.DataFrame([row])
    if TABLE.exists():
        old = pd.read_csv(TABLE)
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(TABLE, index=False)
    return row


def load_panel(asset, drop_extrap=False):
    p = pd.read_parquet(ROOT / f"data/panel_{asset}.parquet")
    p = p[p.result_id >= 0].copy()
    p["asset"] = asset
    p["mid"] = (p.bid + p.ask) / 2
    p["spread"] = p.ask - p.bid
    p["dt"] = pd.to_datetime(p.ts, unit="s", utc=True)
    p["date"] = p.dt.dt.date
    # settlement price: Binance 1m close of the candle opening at T_pm - 60
    symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}[asset]
    kl = pd.read_parquet(ROOT / f"data/binance/{symbol}_1m.parquet")
    close_map = dict(zip((kl["open_time_us"] // 1000000 + 60).astype(int), kl["close"]))
    p["S_T"] = p["T_pm"].astype(int).map(close_map)
    if drop_extrap:
        p = p[p.extrap == 0]
    return p


def ev_yes_taker(df):
    """Per-row EV of buying YES at ask, holding to resolution."""
    return df.label - df.ask - fee(df.ask)


def ev_no_taker(df):
    """Per-row EV of buying NO at (1 - bid_yes), holding to resolution."""
    pno = 1 - df.bid
    return (1 - df.label) - pno - fee(pno)


def digital_delta_dollar(row):
    """d(Digital)/d(lnS) = phi(d2)/(sig*sqrt(tau)); per-share $ delta vs log-return."""
    import math
    from scipy.stats import norm
    tau = row.tte_d / 365.0
    if tau <= 0 or row.sigma <= 0:
        return 0.0
    v = row.sigma * math.sqrt(tau)
    d2 = math.log(row.S_bin / row.K) / v - v / 2 if row.S_bin > 0 and row.K > 0 else 0.0
    return float(norm.pdf(d2) / v)


def ev_hedged(df, ev_raw, S_T):
    """Alpha-only EV: subtract digital delta x realized log-return of underlying.

    ev_raw: per-share raw EV series; S_T: settlement price series aligned to df."""
    deltas = df.apply(digital_delta_dollar, axis=1)
    beta_pnl = deltas * np.log(S_T / df.S_bin)
    sign = 1  # for YES buys; caller flips for NO
    return ev_raw - sign * beta_pnl


def dedup_trades(df, cols=("slug",), cooldown_h=24):
    """Keep at most one signal per market per cooldown window (avoids
    pseudo-replication from persistent gaps)."""
    df = df.sort_values("ts")
    keep = []
    last = {}
    for r in df.itertuples():
        key = tuple(getattr(r, c) for c in cols)
        if key not in last or r.ts - last[key] >= cooldown_h * 3600:
            keep.append(r.Index)
            last[key] = r.ts
    return df.loc[keep]
