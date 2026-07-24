"""ONE TEST LOOK. Frozen policy chosen on TRAIN only:
  - hedge instrument: BTC/ETH/SOL/XRP PERPETUAL (not dated future)
  - ratio h = 1.0 x digital delta; STATIC at entry fill time; no rebalance
  - hold to settlement; collect funding; perp taker fees 2 x 3.5bp x delta.
Applied to the incumbent OOS trade lists (taker primary; maker reported under both
hedge-timing conventions as a risk decomposition, same fixed trade list).
"""
import glob
import os
import numpy as np
import pandas as pd

LAB = "/home/user/D1/results/improve/hedge-lab"
FUT = "/home/user/D1/data/deribit_fut"
FEE_BP = 3.5e-4

fund = pd.read_parquet(f"{LAB}/funding_hourly.parquet")
CUMS = {}
for a, f in fund.groupby("asset"):
    f = f.sort_values("timestamp")
    t = f.timestamp.values / 1000.0
    CUMS[a] = (np.concatenate([[t[0] - 3600], t]),
               np.concatenate([[0.0], np.cumsum(f.interest_1h.values)]))

PERP = {}
for a, nm in [("BTC", "BTC-PERPETUAL"), ("ETH", "ETH-PERPETUAL"),
              ("SOL", "SOL_USDC-PERPETUAL"), ("XRP", "XRP_USDC-PERPETUAL")]:
    b = pd.read_parquet(f"{FUT}/bars1m_{nm}.parquet").sort_values("ts_ms")
    b = b[np.isfinite(b.close) & (b.close > 0)]
    PERP[a] = (b.ts_ms.values / 1000.0, b.close.values)


def px(asset, t0, tol=900):
    t, c = PERP[asset]
    i = np.searchsorted(t, t0, side="right") - 1
    return c[i] if i >= 0 and t0 - t[i] <= tol else np.nan


def clus(d, col):
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / np.sqrt(len(g))
    return g.mean(), g.mean() / se if se > 0 else np.nan, len(g)


rows = []
for name in ["taker", "maker"]:
    d = pd.read_csv(f"{LAB}/trades_{name}_oos_funding.csv")
    d["P0"] = [px(r.asset, r.entry_ts) for r in d.itertuples()]
    d["P1"] = [px(r.asset, r.T_pm) for r in d.itertuples()]
    ok = np.isfinite(d.P0) & np.isfinite(d.P1)
    print(name, "perp px coverage:", ok.mean().round(3))
    d = d[ok].copy()
    fees = 2 * FEE_BP * d.delta
    d["ev_policy_fill"] = d.ev_raw - d.delta * np.log(d.P1 / d.P0) + d.fund_pnl - fees
    d["ev_policy_sig"] = d.ev_raw - d.delta * d.ret_T + d.fund_pnl - fees
    d.to_csv(f"{LAB}/trades_{name}_oos_policy.csv", index=False)
    for conv in ["fill", "sig"]:
        ev, t, ne = clus(d, f"ev_policy_{conv}")
        rows.append(dict(strat=name, timing=conv, n=len(d), n_events=ne,
                         ev=round(ev, 5), t=round(t, 2),
                         sd=round(d[f"ev_policy_{conv}"].std(), 4),
                         raw_ev=round(clus(d, "ev_raw")[0], 5),
                         funding=round(d.fund_pnl.mean(), 5),
                         fees=round(fees.mean(), 5)))
        print(rows[-1], flush=True)

res = pd.DataFrame(rows)
res.to_csv(f"{LAB}/test_look_summary.csv", index=False)
print("\nFROZEN POLICY OOS (one look):\n", res.to_string())
