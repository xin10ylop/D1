"""Part 2+3: rebalanced vs static hedge; perp vs dated-future hedge.

Path sim on 15-min panels from signal bar to settlement. Static ties out exactly with
incumbent ev_hedged (= ev_raw - delta0*ret_T). Rebalance variants recompute digital delta
on the path; perp fee 3.5bp/side on traded notional. Funding credited on held notional.
"""
import glob
import re
import numpy as np
import pandas as pd
from scipy.stats import norm

LAB = "/home/user/D1/results/improve/hedge-lab"
FEE_BP = 3.5e-4

fund = pd.read_parquet(f"{LAB}/funding_hourly.parquet")
CUMS = {}
for a, f in fund.groupby("asset"):
    f = f.sort_values("timestamp")
    t = f.timestamp.values / 1000.0
    CUMS[a] = (np.concatenate([[t[0] - 3600], t]),
               np.concatenate([[0.0], np.cumsum(f.interest_1h.values)]))


def fsum(asset, t0, t1):
    tt, c = CUMS[asset]
    return np.interp(t1, tt, c) - np.interp(t0, tt, c)


# ---- per-slug paths from raw panels
PATHS = {}
for a in ["BTC", "ETH", "SOL", "XRP"]:
    p = pd.read_parquet(f"/home/user/D1/data/panel_{a}.parquet",
                        columns=["slug", "ts", "sigma", "tte_d", "S_bin", "K"])
    p = p[np.isfinite(p.sigma) & (p.sigma > 0) & np.isfinite(p.S_bin)]
    for s, g in p.groupby("slug"):
        g = g.sort_values("ts")
        PATHS[s] = (g.ts.values.astype(float), g.S_bin.values, g.sigma.values,
                    g.tte_d.values, g.K.values[0])


def ddelta(S, K, sigma, tte_d):
    v = sigma * np.sqrt(np.maximum(tte_d, 1e-4) / 365.0)
    d2 = np.log(S / K) / v - v / 2
    return norm.pdf(d2) / v


def sim_trade(r, mode, cap_mult=None):
    """mode: 'static' | 'daily' | 'move2' ; returns hedge_pnl, fees, funding, n_rebal."""
    ts, S, sig, tte, K = PATHS[r.slug]
    i0 = np.searchsorted(ts, r.ts)
    if i0 >= len(ts) or ts[i0] != r.ts:
        i0 = max(np.searchsorted(ts, r.ts, side="right") - 1, 0)
    t_path = np.append(ts[i0:], r.T_pm)
    S_path = np.append(S[i0:], r.S_T)
    d_path = np.append(ddelta(S[i0:], K, sig[i0:], tte[i0:]), 0.0)
    n = len(t_path)
    d0 = r.delta
    held = np.full(n - 1, d0)
    if mode != "static":
        cur, s_ref, next_t = d0, S_path[0], t_path[0] + 86400
        for i in range(1, n - 1):
            trig = (t_path[i] >= next_t) if mode == "daily" else \
                   (abs(np.log(S_path[i] / s_ref)) > 0.02)
            if trig:
                cur = d_path[i]
                if cap_mult is not None:
                    cur = min(cur, cap_mult * d0)
                s_ref = S_path[i]
                if mode == "daily":
                    next_t = t_path[i] + 86400
            held[i:] = cur
    lr = np.log(S_path[1:] / S_path[:-1])
    hedge = -np.sum(held * lr)
    dd = np.abs(np.diff(np.concatenate([[0.0], held, [0.0]])))
    fees = FEE_BP * dd.sum()
    fnd = sum(h * fsum(r.asset, a, b) for h, a, b in zip(held, t_path[:-1], t_path[1:]) if h != 0)
    return hedge, fees, fnd, int((np.diff(held) != 0).sum())


def clus(d, col):
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / np.sqrt(len(g))
    return g.mean(), g.mean() / se if se > 0 else np.nan


MODES = [("static", None), ("daily", None), ("daily_cap2", 2.0), ("move2", None)]

rows = []
for name in ["taker", "maker"]:
    for smp in ["train", "oos"]:
        d = pd.read_csv(f"{LAB}/trades_{name}_{smp}.csv")
        for mname, cap in MODES:
            mode = "daily" if mname.startswith("daily") else mname
            out = [sim_trade(r, mode, cap) for r in d.itertuples()]
            h, fe, fn, nrb = map(np.array, zip(*out))
            d[f"pnl_{mname}"] = d.ev_raw + h + fn - fe
            ev, t = clus(d, f"pnl_{mname}")
            rows.append(dict(strat=name, sample=smp, mode=mname, n=len(d),
                             ev=round(ev, 5), t=round(t, 2),
                             sd_trade=round(d[f"pnl_{mname}"].std(), 4),
                             hedge_fees=round(fe.mean(), 5), funding=round(fn.mean(), 5),
                             n_rebal=round(nrb.mean(), 1),
                             static_check=round((d.ev_raw + h - d.ev_hedged).abs().max(), 6)
                             if mname == "static" else np.nan))
            print(rows[-1], flush=True)
        d.to_csv(f"{LAB}/trades_{name}_{smp}_rebal.csv", index=False)

res = pd.DataFrame(rows)
res.to_csv(f"{LAB}/rebal_summary.csv", index=False)
print("\n", res.to_string())
