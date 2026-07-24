"""Maker hedge-timing economics (TRAIN).

The incumbent maker ev_hedged assumes the short-perp hedge is on from the SIGNAL bar.
Lab3 shows hedging at FILL time destroys the edge (adverse selection: fills happen on
~0.5-0.6% down-moves). Pre-hedging at quote time is implementable, but the 28% of
quotes that never fill then carry a naked short until cancel (16 bars = 4h) and pay
round-trip perp fees. This script prices that.
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/user/D1/scripts")
from battery import prep
from evaluate import dedup_trades

LAB = "/home/user/D1/results/improve/hedge-lab"
FEE_BP = 3.5e-4
HORIZON = 16

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


panels = [prep(a) for a in ["BTC", "ETH", "SOL", "XRP"]]
allp = pd.concat(panels, ignore_index=True)
allp = allp[~allp.is_test]
base = allp[(allp.ask > 0.02) & (allp.ask < 0.98) & (allp.spread <= 0.05)]
bys = {s: g.reset_index(drop=True) for s, g in allp.groupby("slug")}

sel = base[(base.fv - (base.bid + 0.01)) > 0.025]
sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)

rows = []
for r in sel.itertuples():
    g = bys.get(r.slug)
    if g is None:
        continue
    fut = g[g.ts > r.ts].head(HORIZON)
    if len(fut) == 0:
        continue
    lim = r.bid + 0.01
    hit = fut[fut.ask <= lim]
    filled = len(hit) > 0
    if filled:
        # pre-hedged from signal bar to settlement (incumbent convention + costs)
        pnl = (r.label - lim) - r.delta * r.ret_T \
            + r.delta * fsum(r.asset, r.ts, r.T_pm) - 2 * FEE_BP * r.delta
        cancel_ts = np.nan
    else:
        last = fut.iloc[-1]
        cancel_ts = last.ts
        pnl = -r.delta * np.log(last.S_bin / r.S_bin) \
            + r.delta * fsum(r.asset, r.ts, cancel_ts) - 2 * FEE_BP * r.delta
    rows.append(dict(asset=r.asset, event=r.event, slug=r.slug, ts=r.ts,
                     filled=filled, delta=r.delta, pnl=pnl,
                     move_bp=1e4 * np.log((fut.iloc[-1].S_bin if not filled
                                           else hit.iloc[0].S_bin) / r.S_bin)))

d = pd.DataFrame(rows)


def clus(x, col="pnl"):
    g = x.groupby(["asset", "event"])[col].mean()
    se = g.std() / np.sqrt(len(g))
    return g.mean(), g.mean() / se if se > 0 else np.nan, len(g)


f_ev, f_t, f_ne = clus(d[d.filled])
u_ev, u_t, u_ne = clus(d[~d.filled])
a_ev, a_t, a_ne = clus(d)
n_f, n_u = int(d.filled.sum()), int((~d.filled).sum())
per_filled = (d.pnl.sum()) / max(n_f, 1)

out = pd.DataFrame([
    dict(leg="filled_prehedged", n=n_f, n_events=f_ne, ev=round(f_ev, 5), t=round(f_t, 2)),
    dict(leg="unfilled_naked_short", n=n_u, n_events=u_ne, ev=round(u_ev, 5), t=round(u_t, 2)),
    dict(leg="all_quotes_per_quote", n=n_f + n_u, n_events=a_ne, ev=round(a_ev, 5), t=round(a_t, 2)),
    dict(leg="all_in_per_FILLED_share", n=n_f, n_events=a_ne, ev=round(per_filled, 5), t=np.nan),
])
out.to_csv(f"{LAB}/maker_prehedge_summary.csv", index=False)
d.to_csv(f"{LAB}/maker_prehedge_trades_train.csv", index=False)
print(out.to_string())
print("\nfill rate:", round(n_f / (n_f + n_u), 3))
print("mean spot move signal->fill (filled, bp):", round(d[d.filled].move_bp.mean(), 1))
print("mean spot move signal->cancel (unfilled, bp):", round(d[~d.filled].move_bp.mean(), 1))
print("mean delta filled/unfilled:", round(d[d.filled].delta.mean(), 2),
      round(d[~d.filled].delta.mean(), 2))
