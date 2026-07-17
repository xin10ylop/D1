"""Sample downloader for HOURLY strike ladders -> 10-second bars.

BTC/ETH: sampled days. SOL/XRP: all hourly markets (few hundred).
Usage: python3 scripts/dl_pm_hourly.py [workers]
"""
import sys, re, asyncio, datetime as dt
import numpy as np
import pandas as pd
from common import ROOT, DATA, TLX_KEY
from telonex.download import download_async

BARS = DATA / "pm_bars_hourly"; BARS.mkdir(exist_ok=True)
RAW = DATA / "pm_raw_h"; RAW.mkdir(exist_ok=True)
LOG = ROOT / "logs"

SAMPLE_DAYS = ["2026-06-27", "2026-06-28", "2026-06-30", "2026-07-02", "2026-07-04",
               "2026-07-06", "2026-07-08", "2026-07-10", "2026-07-12", "2026-07-14",
               "2026-07-15", "2026-07-16"]


def universe():
    df = pd.read_parquet(DATA / "polymarket_markets.parquet")
    ql = df["question"].fillna("").str.lower()
    is_strike = ql.str.contains("above")
    asset = np.select(
        [ql.str.contains("bitcoin"), ql.str.contains("ethereum"), ql.str.contains("solana"), ql.str.contains("xrp")],
        ["BTC", "ETH", "SOL", "XRP"], "")
    df = df[is_strike & (asset != "")].copy()
    df["asset"] = asset[is_strike & (asset != "")]
    es = df["event_slug"].fillna("")
    hourly = es.str.match(r".*-\d{1,2}(am|pm)-et$")
    df = df[hourly & (df["quotes_from"].fillna("") != "")].copy()

    def strike(qq):
        m = re.search(r"\$?([\d,]+(?:\.\d+)?)", qq.replace("above ", "@").split("@")[-1])
        m = re.search(r"above \$?([\d,]+(?:\.\d+)?)", qq.lower())
        return float(m.group(1).replace(",", "")) if m else np.nan
    df["strike"] = df["question"].map(strike)
    df["end_dt"] = pd.to_datetime(pd.to_numeric(df["end_date_us"], errors="coerce"), unit="us", utc=True)
    sel = (df["asset"].isin(["SOL", "XRP"])) | (df["quotes_from"].isin(SAMPLE_DAYS)) | (df["quotes_to"].isin(SAMPLE_DAYS))
    df = df[sel]
    return df[["market_id", "slug", "asset", "strike", "end_dt", "quotes_from", "quotes_to",
               "status", "result_id"]]


def downsample(raw_path, slug):
    q = pd.read_parquet(raw_path, columns=["timestamp_us", "bid_price", "bid_size", "ask_price", "ask_size"])
    for c in ["bid_price", "bid_size", "ask_price", "ask_size"]:
        q[c] = pd.to_numeric(q[c], errors="coerce")
    q["ts"] = pd.to_datetime(q["timestamp_us"], unit="us", utc=True)
    q = q.set_index("ts").sort_index()
    q["mid"] = (q["bid_price"] + q["ask_price"]) / 2
    o = q.resample("10s").agg(
        bid=("bid_price", "last"), bid_sz=("bid_size", "last"),
        ask=("ask_price", "last"), ask_sz=("ask_size", "last"),
        mid_min=("mid", "min"), mid_max=("mid", "max"), n=("mid", "size"))
    o = o[o["n"] > 0]
    o["slug"] = slug
    return o.reset_index()


async def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    uni = universe()
    uni.to_parquet(DATA / "pm_universe_hourly.parquet", index=False)
    print("hourly sample universe:", len(uni), uni.groupby("asset").size().to_dict(), flush=True)
    sem = asyncio.Semaphore(workers)

    async def one(mk):
        d0 = dt.date.fromisoformat(mk.quotes_from)
        d1 = dt.date.fromisoformat(mk.quotes_to)
        d = d0
        while d <= d1:
            day = d.isoformat()
            d += dt.timedelta(days=1)
            out = BARS / f"{mk.slug}_{day}.parquet"
            if out.exists() or out.with_suffix(".empty").exists():
                continue
            async with sem:
                try:
                    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
                    files = await download_async(api_key=TLX_KEY, exchange="polymarket", channel="quotes",
                                                 slug=mk.slug, outcome="Yes", from_date=day, to_date=nxt,
                                                 download_dir=str(RAW))
                    if files:
                        downsample(files[0], mk.slug).to_parquet(out, index=False)
                        import os
                        os.remove(files[0])
                    else:
                        out.with_suffix(".empty").touch()
                except Exception as e:
                    with open(LOG / "pm_hourly_errors.log", "a") as f:
                        f.write(f"{mk.slug} {day} {e}\n")

    await asyncio.gather(*[one(mk) for mk in uni.itertuples()])
    print("DONE", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
