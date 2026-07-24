"""Fetch Deribit perp funding history (hourly interest_1h) 2025-10-01..2026-07-24."""
import time
import requests
import pandas as pd

INSTS = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL",
         "SOL": "SOL_USDC-PERPETUAL", "XRP": "XRP_USDC-PERPETUAL"}
URL = "https://www.deribit.com/api/v2/public/get_funding_rate_history"
T0 = int(pd.Timestamp("2025-10-01", tz="UTC").timestamp() * 1000)
T1 = int(pd.Timestamp("2026-07-24", tz="UTC").timestamp() * 1000)
STEP = 7 * 86400 * 1000  # weekly chunks (168 hourly records)

rows = []
for asset, inst in INSTS.items():
    t = T0
    while t < T1:
        for attempt in range(5):
            try:
                r = requests.get(URL, params=dict(instrument_name=inst, start_timestamp=t,
                                                  end_timestamp=min(t + STEP, T1)), timeout=30)
                res = r.json().get("result", [])
                break
            except Exception as e:
                print("retry", inst, t, e)
                time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"failed {inst} {t}")
        for x in res:
            rows.append(dict(asset=asset, timestamp=x["timestamp"], interest_1h=x["interest_1h"],
                             interest_8h=x["interest_8h"], index_price=x["index_price"]))
        t += STEP
        time.sleep(0.15)
    print(asset, "done", sum(1 for x in rows if x["asset"] == asset))

df = pd.DataFrame(rows).drop_duplicates(["asset", "timestamp"]).sort_values(["asset", "timestamp"])
df.to_parquet("/home/user/D1/results/improve/hedge-lab/funding_hourly.parquet", index=False)
print(df.groupby("asset").agg(n=("timestamp", "size"),
                              mean_1h_bp=("interest_1h", lambda s: s.mean() * 1e4),
                              ann_pct=("interest_1h", lambda s: s.mean() * 24 * 365 * 100)))
