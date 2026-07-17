"""Selective book_snapshot_25 downloader: feed it a CSV of slug,date rows.

Usage: python3 scripts/dl_books.py <list.csv> [workers]
"""
import sys, asyncio, datetime as dt
import pandas as pd
from common import DATA, TLX_KEY
from telonex.download import download_async

BOOKS = DATA / "pm_books"; BOOKS.mkdir(exist_ok=True)


async def main():
    lst = pd.read_csv(sys.argv[1])
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    sem = asyncio.Semaphore(workers)
    jobs = list(lst.drop_duplicates(["slug", "date"]).itertuples())

    async def one(row):
        day = str(row.date)
        out = BOOKS / f"polymarket_book_snapshot_25_{day}_{row.slug}_Yes.parquet"
        if out.exists() or (BOOKS / f"{row.slug}_{day}.empty").exists():
            return
        async with sem:
            try:
                nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
                files = await download_async(api_key=TLX_KEY, exchange="polymarket",
                                             channel="book_snapshot_25", slug=row.slug, outcome="Yes",
                                             from_date=day, to_date=nxt, download_dir=str(BOOKS))
                if not files:
                    (BOOKS / f"{row.slug}_{day}.empty").touch()
            except Exception as e:
                print("ERR", row.slug, day, e, flush=True)

    await asyncio.gather(*[one(r) for r in jobs])
    print("DONE", len(jobs), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
