"""Deribit option trades for the extension window (BTC/ETH only), same dir/naming
as scripts/dl_deribit_trades.py (idempotent; skips existing files).
Usage: python3 dl_deribit_old.py start end workers
"""
import sys, datetime as dt
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/home/user/D1/scripts")
from common import ROOT
from dl_deribit_trades import pull_day, LOG

def main():
    start, end = sys.argv[1], sys.argv[2]
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d").tolist()[::-1]
    jobs = [(c, d) for d in days for c in ["BTC", "ETH"]]
    t0 = dt.datetime.now()
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(pull_day, c, d): (c, d) for c, d in jobs}
        for fut in as_completed(futs):
            c, d = futs[fut]
            done += 1
            try:
                n = fut.result()
                if done % 40 == 0:
                    print(f"[{dt.datetime.now()-t0}] {done}/{len(jobs)} last={c} {d} n={n}", flush=True)
            except Exception as e:
                with open(LOG / "deribit_old_errors.log", "a") as fh:
                    fh.write(f"{c} {d} {e}\n")
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
