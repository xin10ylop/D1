"""Fit smiles for the extension window (does NOT overwrite existing smiles files).

Saves data/smiles_ext_{asset}.parquet and data/index1m_ext_{asset}.parquet.
Usage: python3 fit_smiles_ext.py BTC 2025-01-01 2025-09-24
"""
import sys, glob
import pandas as pd

sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA
import fv as fvlib


def load_trades_window(asset, d0, d1):
    files = sorted(glob.glob(str(DATA / f"deribit_opt_trades/{asset}_*.parquet")))
    files = [f for f in files if d0 <= f.split("_")[-1][:10] <= d1]
    pre = asset + "-"
    parts = []
    for f in files:
        try:
            d = pd.read_parquet(f, columns=["timestamp", "instrument_name", "price", "iv",
                                            "mark_price", "index_price", "direction",
                                            "amount", "contracts"])
        except Exception:
            continue
        if len(d) == 0:
            continue
        d = d[d["instrument_name"].str.startswith(pre)]
        if len(d):
            parts.append(d)
    tr = pd.concat(parts, ignore_index=True)
    toks = tr["instrument_name"].str.slice(len(pre)).str.split("-", expand=True)
    tr["exp_ts"] = toks[0].map({t: fvlib.parse_expiry(t) for t in toks[0].unique()})
    tr["strike"] = pd.to_numeric(toks[1].str.replace("d", ".", regex=False), errors="coerce")
    tr["cp"] = toks[2]
    tr = tr[tr["strike"].notna() & tr["iv"].notna() & (tr["iv"] > 0.5) & (tr["iv"] < 500)]
    tr["ts_s"] = tr["timestamp"] / 1000.0
    return tr.sort_values("timestamp").reset_index(drop=True)


if __name__ == "__main__":
    asset, d0, d1 = sys.argv[1], sys.argv[2], sys.argv[3]
    suf = sys.argv[4] if len(sys.argv) > 4 else ""
    tr = load_trades_window(asset, d0, d1)
    print(asset, "trades:", len(tr), "expiries:", tr.exp_ts.nunique(), flush=True)
    idx = fvlib.index_series(tr)
    idx.rename("index").reset_index().rename(columns={"timestamp": "ts"}).to_parquet(
        DATA / f"index1m_ext{suf}_{asset}.parquet", index=False)
    sm = fvlib.fit_smiles(tr)
    sm.to_parquet(DATA / f"smiles_ext{suf}_{asset}.parquet", index=False)
    print("saved", len(sm), "smile rows", flush=True)
