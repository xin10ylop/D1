"""Binance 1m klines for the extension window -> data/binance/{SYM}_1m_old.parquet."""
import sys
import pandas as pd

sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA
from dl_binance import get_zip_df, OUT

def main():
    months = pd.period_range("2024-02", "2025-09", freq="M").strftime("%Y-%m").tolist()
    for sym in ["BTCUSDT", "ETHUSDT"]:
        out = OUT / f"{sym}_1m_old.parquet"
        if out.exists():
            continue
        parts = []
        for m in months:
            url = f"https://data.binance.vision/data/spot/monthly/klines/{sym}/1m/{sym}-1m-{m}.zip"
            df = get_zip_df(url)
            if df is not None:
                parts.append(df)
                print(sym, m, len(df), flush=True)
        alldf = pd.concat(parts, ignore_index=True)
        t = alldf["open_time"].astype("int64")
        alldf["open_time_us"] = t.where(t > 10**14, t * 1000)
        alldf = alldf.drop_duplicates("open_time_us").sort_values("open_time_us")
        alldf[["open_time_us", "open", "high", "low", "close", "volume", "n_trades"]].to_parquet(out, index=False)
        print("SAVED", out, len(alldf), flush=True)
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
