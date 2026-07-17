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
