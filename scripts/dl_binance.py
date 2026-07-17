"""Binance official kline archive (the exact Polymarket resolution source).

Monthly 1m + 1h zips from data.binance.vision for BTC/ETH/SOL/XRP USDT,
plus daily files for the current partial month. Stored as per-symbol parquet.
"""
import datetime as dt, io, zipfile, urllib.request, time
import pandas as pd
from common import DATA

OUT = DATA / "binance"; OUT.mkdir(exist_ok=True)
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "qvol", "n_trades", "taker_base", "taker_quote", "ignore"]


def fetch(url):
    for a in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception:
            if a == 4:
                return None
            time.sleep(2 ** a)


def get_zip_df(url):
    raw = fetch(url)
    if raw is None:
        return None
    z = zipfile.ZipFile(io.BytesIO(raw))
    with z.open(z.namelist()[0]) as f:
        head = f.read(200).decode()
    skip = 1 if head.split(",")[0] in ("open_time",) else 0
    with z.open(z.namelist()[0]) as f:
        df = pd.read_csv(f, header=None, names=COLS, skiprows=skip)
    return df


def main():
    months = pd.period_range("2025-09", "2026-06", freq="M").strftime("%Y-%m").tolist()
    for sym in SYMS:
        for interval in ["1m", "1h"]:
            out = OUT / f"{sym}_{interval}.parquet"
            if out.exists():
                continue
            parts = []
            for m in months:
                url = f"https://data.binance.vision/data/spot/monthly/klines/{sym}/{interval}/{sym}-{interval}-{m}.zip"
                df = get_zip_df(url)
                if df is not None:
                    parts.append(df)
                    print(sym, interval, m, len(df), flush=True)
            # current month: daily files
            d = dt.date(2026, 7, 1)
            while d <= dt.date(2026, 7, 17):
                url = f"https://data.binance.vision/data/spot/daily/klines/{sym}/{interval}/{sym}-{interval}-{d.isoformat()}.zip"
                df = get_zip_df(url)
                if df is not None:
                    parts.append(df)
                d += dt.timedelta(days=1)
            alldf = pd.concat(parts, ignore_index=True)
            # normalize: open_time in us for 2025+ archives (they switched to microseconds in 2025)
            t = alldf["open_time"].astype("int64")
            alldf["open_time_us"] = t.where(t > 10**14, t * 1000)
            alldf = alldf.drop_duplicates("open_time_us").sort_values("open_time_us")
            alldf[["open_time_us", "open", "high", "low", "close", "volume", "n_trades"]].to_parquet(out, index=False)
            print("SAVED", out, len(alldf), flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
