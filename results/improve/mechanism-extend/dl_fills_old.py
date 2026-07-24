"""Download onchain_fills (Yes outcome) for old daily strike markets -> data/pm_fills_old/.

Idempotent per (slug, day). Priority: recent-era first (usable with existing smiles),
then 2025 backward, then 2024.
Usage: python3 dl_fills_old.py [concurrency]
"""
import sys, asyncio, datetime as dt
import pandas as pd

sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA, ROOT, TLX_KEY
from telonex.download import download_async

OUT = DATA / "pm_fills_old"; OUT.mkdir(exist_ok=True)
LOG = ROOT / "logs"
UNI = ROOT / "results/improve/mechanism-extend/universe_old.parquet"
MAX_DAYS_BEFORE_EXP = 9


def jobs():
    u = pd.read_parquet(UNI)
    u["f0"] = pd.to_datetime(u["onchain_fills_from"], utc=True)
    u["f1"] = pd.to_datetime(u["onchain_fills_to"], utc=True)
    u["lo"] = pd.concat([u["f0"], u["end_dt"].dt.floor("D") - pd.Timedelta(days=MAX_DAYS_BEFORE_EXP)],
                        axis=1).max(axis=1)
    u["hi"] = pd.concat([u["f1"], u["end_dt"].dt.ceil("D")], axis=1).min(axis=1)
    # priority: era 1 = end >= 2025-09-25 (existing smiles), era 2 = 2025 BTC, 3 = 2025 ETH, 4 = 2024
    t0 = pd.Timestamp("2025-09-25", tz="UTC")
    t1 = pd.Timestamp("2025-01-01", tz="UTC")
    u["prio"] = 4
    u.loc[(u.end_dt >= t1) & (u.asset == "ETH"), "prio"] = 3
    u.loc[(u.end_dt >= t1) & (u.asset == "BTC"), "prio"] = 2
    u.loc[u.end_dt >= t0, "prio"] = 1
    u = u.sort_values(["prio", "end_dt"], ascending=[True, False])
    out = []
    for r in u.itertuples():
        d = r.lo.floor("D")
        while d <= r.hi:
            out.append((r.slug, d.date().isoformat()))
            d += pd.Timedelta(days=1)
    return out


async def one(slug, day, sem, stats):
    f = OUT / f"polymarket_onchain_fills_{day}_{slug}_Yes.parquet"
    marker = OUT / f"{slug}_{day}.empty"
    if f.exists() or marker.exists():
        return
    async with sem:
        try:
            nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
            files = await download_async(api_key=TLX_KEY, exchange="polymarket",
                                         channel="onchain_fills", slug=slug, outcome="Yes",
                                         from_date=day, to_date=nxt, download_dir=str(OUT))
            if not files:
                marker.touch()
            stats["ok"] += 1
        except Exception as e:
            stats["err"] += 1
            with open(LOG / "fills_old_errors.log", "a") as fh:
                fh.write(f"{slug} {day} {type(e).__name__}: {e}\n")


async def main():
    conc = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    js = jobs()
    print(f"{len(js)} market-days", flush=True)
    sem = asyncio.Semaphore(conc)
    stats = {"ok": 0, "err": 0}
    t0 = dt.datetime.now()

    async def progress():
        while True:
            await asyncio.sleep(60)
            print(f"[{dt.datetime.now()-t0}] {stats} files={len(list(OUT.glob('*.parquet')))}",
                  flush=True)

    ptask = asyncio.create_task(progress())
    B = 400
    for i in range(0, len(js), B):
        await asyncio.gather(*[one(s, d, sem, stats) for s, d in js[i:i + B]])
    ptask.cancel()
    print("DONE", stats, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
