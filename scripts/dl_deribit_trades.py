"""Pull full Deribit option trade tape (with iv, mark, index) via history host.

Per (currency, utc_day) parquet, idempotent. Currencies: BTC, ETH (inverse
options) and USDC (linear options incl SOL_USDC, XRP_USDC; filtered to
SOL/XRP underlyings to keep files small).
Usage: python3 scripts/dl_deribit_trades.py [start_date] [end_date] [workers]
"""
import sys, datetime as dt, json
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from common import DATA, ROOT, rpc

OUT = DATA / "deribit_opt_trades"; OUT.mkdir(exist_ok=True)
LOG = ROOT / "logs"; LOG.mkdir(exist_ok=True)
H = "history.deribit.com"


def pull_day(cur, day):
    f = OUT / f"{cur}_{day}.parquet"
    if f.exists():
        return 0
    s = int(dt.datetime.fromisoformat(day + "T00:00:00+00:00").timestamp() * 1000)
    e = s + 86400000 - 1
    rows, end = [], e
    while True:
        res = rpc(H, "public/get_last_trades_by_currency_and_time",
                  {"currency": cur, "kind": "option", "start_timestamp": s,
                   "end_timestamp": end, "count": 10000, "sorting": "desc"})
        tr = res["trades"]
        rows.extend(tr)
        if not res.get("has_more") or not tr:
            break
        end = min(t["timestamp"] for t in tr) - 1
        if end <= s:
            break
    if rows:
        df = pd.DataFrame(rows)
        df = df.drop_duplicates(subset=["trade_id"]).sort_values("timestamp")
        if cur == "USDC":
            df = df[df["instrument_name"].str.startswith(("SOL_", "XRP_"))]
        df.to_parquet(f, index=False)
    else:
        f.touch()  # empty marker
    return len(rows)


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-10-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-07-17"
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist()
    days = days[::-1]  # newest first
    jobs = [(c, d) for d in days for c in ["BTC", "ETH", "USDC"]]
    t0 = dt.datetime.now()
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(pull_day, c, d): (c, d) for c, d in jobs}
        for fut in __import__("concurrent.futures", fromlist=["as_completed"]).as_completed(futs):
            c, d = futs[fut]
            done += 1
            try:
                n = fut.result()
                if done % 50 == 0:
                    print(f"[{dt.datetime.now()-t0}] {done}/{len(jobs)} last={c} {d} n={n}", flush=True)
            except Exception as e:
                with open(LOG / "deribit_errors.log", "a") as fh:
                    fh.write(f"{c} {d} {e}\n")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
