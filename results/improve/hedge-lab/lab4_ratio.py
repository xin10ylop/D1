"""Part 4: hedge ratio grid h in {0,0.5,0.75,1.0,1.25} x digital delta. TRAIN ONLY.

ev(h) = ev_raw - h*delta*ret_T + h*fund_pnl - 2*3.5bp*h*delta.
Metrics: clustered mean/t, per-trade sd, mean - lambda*var (lambda 1,2),
bankroll-sim max drawdown (chronological, 100 shares per trade, $ P&L).
"""
import numpy as np
import pandas as pd

LAB = "/home/user/D1/results/improve/hedge-lab"
FEE_BP = 3.5e-4
SHARES = 100

rows = []
for name in ["taker", "maker"]:
    d = pd.read_csv(f"{LAB}/trades_{name}_train_funding.csv").sort_values("entry_ts")
    for h in [0.0, 0.5, 0.75, 1.0, 1.25]:
        ev = d.ev_raw - h * d.delta * d.ret_T + h * d.fund_pnl - 2 * FEE_BP * h * d.delta
        g = ev.groupby([d.asset, d.event]).mean()
        se = g.std() / np.sqrt(len(g))
        cum = (SHARES * ev).cumsum()
        dd = (cum.cummax() - cum).max()
        var = ev.var()
        rows.append(dict(strat=name, h=h, n=len(d),
                         ev=round(g.mean(), 5), t=round(g.mean() / se, 2),
                         sd=round(ev.std(), 4),
                         mv_l1=round(ev.mean() - 1.0 * var, 5),
                         mv_l2=round(ev.mean() - 2.0 * var, 5),
                         maxDD_usd=round(dd, 0),
                         total_usd=round(cum.iloc[-1], 0)))
        print(rows[-1], flush=True)

res = pd.DataFrame(rows)
res.to_csv(f"{LAB}/ratio_summary.csv", index=False)
print("\n", res.to_string())
