"""Part 1: funding P&L of the short-perp hedge for every incumbent trade (train + OOS).

Short perp RECEIVES funding when rate > 0. Per-share funding P&L =
delta_$ x sum(interest_1h over holding window [entry_ts, T_pm]).
"""
import numpy as np
import pandas as pd

LAB = "/home/user/D1/results/improve/hedge-lab"

fund = pd.read_parquet(f"{LAB}/funding_hourly.parquet")


def cum_interp(asset):
    f = fund[fund.asset == asset].sort_values("timestamp")
    t = f.timestamp.values / 1000.0
    c = np.concatenate([[0.0], np.cumsum(f.interest_1h.values)])
    tt = np.concatenate([[t[0] - 3600], t])
    return tt, c


CUMS = {a: cum_interp(a) for a in ["BTC", "ETH", "SOL", "XRP"]}


def funding_sum(asset, t0, t1):
    tt, c = CUMS[asset]
    return np.interp(t1, tt, c) - np.interp(t0, tt, c)


def add_funding(df):
    df = df.copy()
    df["fund_sum"] = [funding_sum(r.asset, r.entry_ts, r.T_pm) for r in df.itertuples()]
    df["fund_pnl"] = df.delta * df.fund_sum          # per share, $ (short receives +)
    df["hold_h"] = (df.T_pm - df.entry_ts) / 3600
    df["ev_hf"] = df.ev_hedged + df.fund_pnl         # hedged EV incl funding
    return df


def clus(d, col):
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / np.sqrt(len(g))
    return g.mean(), g.mean() / se if se > 0 else np.nan, len(g)


rows = []
store = {}
for name in ["taker", "maker"]:
    for smp in ["train", "oos"]:
        d = add_funding(pd.read_csv(f"{LAB}/trades_{name}_{smp}.csv"))
        store[(name, smp)] = d
        d.to_csv(f"{LAB}/trades_{name}_{smp}_funding.csv", index=False)
        ev, t, ne = clus(d, "ev_hedged")
        evf, tf, _ = clus(d, "ev_hf")
        fp, tfp, _ = clus(d, "fund_pnl")
        rows.append(dict(strat=name, sample=smp, n=len(d), n_events=ne,
                         ev_hedged=round(ev, 5), t=round(t, 2),
                         fund_pnl_ev=round(fp, 5), fund_t=round(tfp, 2),
                         ev_hedged_plus_fund=round(evf, 5), t_hf=round(tf, 2),
                         fund_pnl_mean=round(d.fund_pnl.mean(), 5),
                         mean_hold_h=round(d.hold_h.mean(), 1),
                         mean_delta=round(d.delta.mean(), 2)))

res = pd.DataFrame(rows)
res.to_csv(f"{LAB}/funding_pnl_summary.csv", index=False)
print(res.to_string())

# regime dependence: by month, by asset (train+oos pooled, per strat)
for name in ["taker", "maker"]:
    d = pd.concat([store[(name, "train")], store[(name, "oos")]])
    d["month"] = pd.to_datetime(d.ts, unit="s", utc=True).dt.to_period("M").astype(str)
    bym = d.groupby("month").agg(n=("fund_pnl", "size"), fund_pnl=("fund_pnl", "mean"),
                                 rate_1h_bp=("fund_sum", lambda s: np.nan),
                                 ev_hedged=("ev_hedged", "mean")).round(5)
    bya = d.groupby("asset").agg(n=("fund_pnl", "size"), fund_pnl=("fund_pnl", "mean"),
                                 delta=("delta", "mean"), hold_h=("hold_h", "mean")).round(4)
    print(f"\n{name} funding P&L by month:\n{bym.to_string()}")
    print(f"\n{name} funding P&L by asset:\n{bya.to_string()}")
    # correlation with underlying move (does funding hurt exactly when hedge pays?)
    print(name, "corr(fund_pnl, delta*ret_T):",
          round(np.corrcoef(d.fund_pnl, d.delta * d.ret_T)[0, 1], 3))
