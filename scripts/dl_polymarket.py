"""Bulk-download Polymarket daily-ladder quotes (YES) -> 1-min bars, trades (YES) raw.

Quotes raw files are deleted after downsampling to control disk. Idempotent:
skips (market, day) whose bars file already exists.
Usage: python3 scripts/dl_polymarket.py [n_workers]
"""
import sys, re, json, asyncio, datetime as dt, traceback
import pandas as pd
import numpy as np
from common import ROOT, DATA, TLX_KEY, ASSET_WORD
from telonex.download import download_async

BARS = DATA / "pm_bars"; BARS.mkdir(exist_ok=True)
TRADES = DATA / "pm_trades"; TRADES.mkdir(exist_ok=True)
RAW = DATA / "pm_raw"; RAW.mkdir(exist_ok=True)
LOG = ROOT / "logs"; LOG.mkdir(exist_ok=True)


def universe():
    df = pd.read_parquet(DATA / "polymarket_markets.parquet")
    ql = df["question"].fillna("").str.lower()
    is_strike = ql.str.contains("above")
    asset = np.select(
        [ql.str.contains("bitcoin"), ql.str.contains("ethereum"), ql.str.contains("solana"), ql.str.contains("xrp")],
        ["BTC", "ETH", "SOL", "XRP"], "")
    df = df[is_strike & (asset != "")].copy()
    df["asset"] = asset[is_strike & (asset != "")]
    # daily ladders only: event slug has no hourly suffix, no multistrike
    es = df["event_slug"].fillna("")
    daily = ~es.str.contains("multistrike") & ~es.str.match(r".*-\d{1,2}(am|pm)-et$")
    df = df[daily]
    df = df[df["quotes_from"].fillna("") != ""]
    # parse strike from question: "$62,000" etc.
    def strike(qq):
        m = re.search(r"\$([\d,]+(?:\.\d+)?)", qq)
        return float(m.group(1).replace(",", "")) if m else np.nan
    df["strike"] = df["question"].map(strike)
    df["end_dt"] = pd.to_datetime(pd.to_numeric(df["end_date_us"], errors="coerce"), unit="us", utc=True)
    df = df[df["strike"].notna() & df["end_dt"].notna()]
    return df[["market_id", "slug", "asset", "strike", "end_dt", "quotes_from", "quotes_to",
               "trades_from", "trades_to", "status", "result_id", "outcome_0"]]


def day_range(a, b):
    d0 = dt.date.fromisoformat(a); d1 = dt.date.fromisoformat(b)
    out = []
    while d0 <= d1:
        out.append(d0.isoformat()); d0 += dt.timedelta(days=1)
    return out


def downsample(raw_path, slug, day):
    q = pd.read_parquet(raw_path, columns=["timestamp_us", "bid_price", "bid_size", "ask_price", "ask_size"])
    for c in ["bid_price", "bid_size", "ask_price", "ask_size"]:
        q[c] = pd.to_numeric(q[c], errors="coerce")
    q["ts"] = pd.to_datetime(q["timestamp_us"], unit="us", utc=True)
    q = q.set_index("ts").sort_index()
    q["mid"] = (q["bid_price"] + q["ask_price"]) / 2
    o = q.resample("1min").agg(
        bid=("bid_price", "last"), bid_sz=("bid_size", "last"),
        ask=("ask_price", "last"), ask_sz=("ask_size", "last"),
        mid_min=("mid", "min"), mid_max=("mid", "max"), n=("mid", "size"))
    o = o[o["n"] > 0]
    o["slug"] = slug
    return o.reset_index()


async def do_market(row, sem, results):
    slug = row.slug
    days_q = day_range(row.quotes_from, row.quotes_to)
    for day in days_q:
        bars_file = BARS / f"{slug}_{day}.parquet"
        if bars_file.exists():
            continue
        async with sem:
            try:
                nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
                files = await download_async(api_key=TLX_KEY, exchange="polymarket", channel="quotes",
                                             slug=slug, outcome="Yes", from_date=day, to_date=nxt,
                                             download_dir=str(RAW))
                if files:
                    bars = downsample(files[0], slug, day)
                    bars.to_parquet(bars_file, index=False)
                    import os
                    os.remove(files[0])
                else:
                    bars_file.with_suffix(".empty").touch()
                results["q_ok"] += 1
            except Exception as e:
                results["q_err"] += 1
                with open(LOG / "pm_errors.log", "a") as f:
                    f.write(f"quotes {slug} {day} {e}\n")
    # trades
    if str(row.trades_from) not in ("", "None", "nan"):
        for day in day_range(row.trades_from, row.trades_to):
            tf = TRADES / f"polymarket_trades_{day}_{slug}_Yes.parquet"
            if tf.exists() or (TRADES / f"{slug}_{day}.empty").exists():
                continue
            async with sem:
                try:
                    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
                    files = await download_async(api_key=TLX_KEY, exchange="polymarket", channel="trades",
                                                 slug=slug, outcome="Yes", from_date=day, to_date=nxt,
                                                 download_dir=str(TRADES))
                    if not files:
                        (TRADES / f"{slug}_{day}.empty").touch()
                    results["t_ok"] += 1
                except Exception as e:
                    results["t_err"] += 1
                    with open(LOG / "pm_errors.log", "a") as f:
                        f.write(f"trades {slug} {day} {e}\n")


async def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    uni = universe()
    uni = uni.sort_values("end_dt", ascending=False)  # newest first
    uni.to_parquet(DATA / "pm_universe.parquet", index=False)
    print(f"universe: {len(uni)} markets", flush=True)
    sem = asyncio.Semaphore(workers)
    results = {"q_ok": 0, "q_err": 0, "t_ok": 0, "t_err": 0}

    async def runner(rows):
        for row in rows:
            await do_market(row, sem, results)

    rows = list(uni.itertuples())
    # market-level parallelism: split rows across tasks; sem limits inner concurrency
    n_tasks = workers * 2
    chunks = [rows[i::n_tasks] for i in range(n_tasks)]
    t0 = dt.datetime.now()

    async def progress():
        while True:
            await asyncio.sleep(60)
            done = len(list(BARS.glob("*.parquet")))
            print(f"[{dt.datetime.now()-t0}] bars files: {done} | {results}", flush=True)

    ptask = asyncio.create_task(progress())
    await asyncio.gather(*[runner(c) for c in chunks])
    ptask.cancel()
    print("DONE", results, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
