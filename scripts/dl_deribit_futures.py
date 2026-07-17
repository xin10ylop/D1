"""Deribit futures (perp + dated) hourly bars, perp 1-min bars, DVOL, instruments.

Covers BTC, ETH (native) and SOL/XRP (USDC-settled). Idempotent per file.
"""
import datetime as dt, json, time
import pandas as pd
from common import DATA, rpc

OUT = DATA / "deribit_fut"; OUT.mkdir(exist_ok=True)
H = "history.deribit.com"
W = "www.deribit.com"
START = dt.datetime(2025, 9, 25, tzinfo=dt.timezone.utc)
END = dt.datetime(2026, 7, 18, tzinfo=dt.timezone.utc)


def ms(d): return int(d.timestamp() * 1000)


def chart(inst, res, s, e, host=H):
    # paginate in <=4000-bar windows
    step = {"60": 3600000, "1": 60000, "1D": 86400000}[res] * 4000
    rows = {"ticks": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
    cur = s
    while cur < e:
        ee = min(cur + step, e)
        r = rpc(host, "public/get_tradingview_chart_data",
                {"instrument_name": inst, "start_timestamp": cur, "end_timestamp": ee, "resolution": res})
        if r.get("status") == "ok":
            for k in rows:
                rows[k].extend(r[k])
        cur = ee
    if not rows["ticks"]:
        return None
    df = pd.DataFrame(rows).rename(columns={"ticks": "ts_ms"}).drop_duplicates("ts_ms").sort_values("ts_ms")
    return df


def main():
    # instrument metadata (options + futures, live + expired)
    for cur in ["BTC", "ETH", "USDC"]:
        for kind in ["option", "future"]:
            for expired in ["true", "false"]:
                f = OUT / f"instruments_{cur}_{kind}_{expired}.parquet"
                if f.exists():
                    continue
                r = rpc(H if expired == "true" else W, "public/get_instruments",
                        {"currency": cur, "kind": kind, "expired": expired, "count": 10000})
                if r:
                    pd.DataFrame(r).to_parquet(f, index=False)
        print("instruments", cur, "done", flush=True)

    # futures universe: perps + dated within window
    futs = []
    for cur in ["BTC", "ETH", "USDC"]:
        for expired in ["true", "false"]:
            f = OUT / f"instruments_{cur}_future_{expired}.parquet"
            if f.exists():
                futs.append(pd.read_parquet(f))
    fu = pd.concat(futs, ignore_index=True).drop_duplicates("instrument_name")
    keep = fu[fu["instrument_name"].str.startswith(("BTC-", "ETH-", "SOL_USDC-", "XRP_USDC-"))]
    keep = keep[(keep["expiration_timestamp"].fillna(9e15) > ms(START)) | keep["instrument_name"].str.contains("PERPETUAL")]

    for row in keep.itertuples():
        inst = row.instrument_name
        f = OUT / f"bars1h_{inst}.parquet"
        if f.exists():
            continue
        s = max(ms(START), int(getattr(row, "creation_timestamp", ms(START)) or ms(START)))
        e = min(ms(END), int(row.expiration_timestamp)) if "PERPETUAL" not in inst else ms(END)
        if e <= s:
            continue
        try:
            df = chart(inst, "60", s, e)
            if df is not None:
                df.to_parquet(f, index=False)
                print("bars1h", inst, len(df), flush=True)
        except Exception as ex:
            print("ERR", inst, ex, flush=True)

    # perp 1-min bars (for fine alignment)
    for inst in ["BTC-PERPETUAL", "ETH-PERPETUAL", "SOL_USDC-PERPETUAL", "XRP_USDC-PERPETUAL"]:
        f = OUT / f"bars1m_{inst}.parquet"
        if f.exists():
            continue
        try:
            df = chart(inst, "1", ms(START), ms(END))
            if df is not None:
                df.to_parquet(f, index=False)
                print("bars1m", inst, len(df), flush=True)
        except Exception as ex:
            print("ERR 1m", inst, ex, flush=True)

    # DVOL hourly
    for cur in ["BTC", "ETH"]:
        f = OUT / f"dvol_{cur}.parquet"
        if f.exists():
            continue
        rows = []
        s = ms(START)
        while s < ms(END):
            e = min(s + 3600000 * 900, ms(END))
            r = rpc(W, "public/get_volatility_index_data",
                    {"currency": cur, "start_timestamp": s, "end_timestamp": e, "resolution": "3600"})
            rows.extend(r.get("data", []))
            s = e
        df = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close"]).drop_duplicates("ts_ms")
        df.to_parquet(f, index=False)
        print("dvol", cur, len(df), flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
