"""Build universe of OLD (resolved < 2025-10-11) daily crypto strike markets with onchain fills."""
import re, sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA

OUT = DATA.parent / "results/improve/mechanism-extend"


def universe_old():
    df = pd.read_parquet(DATA / "polymarket_markets.parquet")
    ql = df["question"].fillna("").str.lower()
    is_strike = ql.str.contains("above")
    asset = np.select([ql.str.contains("bitcoin"), ql.str.contains("ethereum")],
                      ["BTC", "ETH"], "")
    m = df[is_strike & (asset != "")].copy()
    m["asset"] = asset[is_strike & (asset != "")]
    es = m["event_slug"].fillna("")
    daily = ~es.str.contains("multistrike") & ~es.str.match(r".*-\d{1,2}(am|pm)-et$")
    m = m[daily].copy()

    def strike(qq):
        mm = re.search(r"\$([\d,]+(?:\.\d+)?)", qq)
        return float(mm.group(1).replace(",", "")) if mm else np.nan

    m["strike"] = m["question"].map(strike)
    m["end_dt"] = pd.to_datetime(pd.to_numeric(m["end_date_us"], errors="coerce"),
                                 unit="us", utc=True)
    m = m[(m["status"] == "resolved") & m["strike"].notna()]
    m = m[m["onchain_fills_from"].fillna("") != ""]
    # sane end date (drop epoch-zero rows), before panel era start
    m = m[m["end_dt"] > pd.Timestamp("2024-01-01", tz="UTC")]
    m = m[m["end_dt"] < pd.Timestamp("2025-10-11", tz="UTC")]
    m["result_id"] = pd.to_numeric(m["result_id"], errors="coerce")
    m = m[m["result_id"].isin([0, 1]) & (m["outcome_0"] == "Yes")]
    m["label"] = (m["result_id"] == 0).astype(int)
    return m[["market_id", "slug", "event_slug", "question", "asset", "strike",
              "end_dt", "onchain_fills_from", "onchain_fills_to", "result_id",
              "label"]].reset_index(drop=True)


if __name__ == "__main__":
    u = universe_old()
    u.to_parquet(OUT / "universe_old.parquet", index=False)
    print("markets:", len(u))
    q = u.groupby([u["asset"], u["end_dt"].dt.tz_localize(None).dt.to_period("Q")]).size()
    print(q)
    # market-days to download (restrict to last 9 days before expiry)
    f0 = pd.to_datetime(u["onchain_fills_from"], utc=True)
    f1 = pd.to_datetime(u["onchain_fills_to"], utc=True)
    lo = pd.concat([f0, u["end_dt"].dt.floor("D") - pd.Timedelta(days=9)], axis=1).max(axis=1)
    nd = ((f1 - lo).dt.days + 1).clip(lower=0)
    print("market-days (capped at last 9d):", int(nd.sum()))
    print(u["end_dt"].min(), u["end_dt"].max())
