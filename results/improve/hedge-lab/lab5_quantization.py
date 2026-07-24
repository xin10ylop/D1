"""Part 5: Deribit hedge granularity for small accounts. TRAIN trades.

Contract granularity (instruments_* on disk): BTC-PERP $10, ETH-PERP $1,
SOL_USDC-PERP 0.1 SOL (~0.1*S $), XRP_USDC-PERP 10 XRP (~10*S $).
For premium clip C: shares = C/entry, exact hedge$ = shares*delta,
quantized = g*round(./g). Extra P&L = (quant-exact)*ret_T (per trade $).
"""
import numpy as np
import pandas as pd

LAB = "/home/user/D1/results/improve/hedge-lab"


def gran(asset, S):
    if asset in ("BTC", "ETH"):
        return {"BTC": 10.0, "ETH": 1.0}[asset]
    return {"SOL": 0.1, "XRP": 10.0}[asset] * S


rows = []
for name in ["taker", "maker"]:
    d = pd.read_csv(f"{LAB}/trades_{name}_train_funding.csv")
    d["g"] = [gran(r.asset, r.S_bin) for r in d.itertuples()]
    for clip in [10, 25, 50, 100, 500]:
        sh = clip / d.entry
        exact = sh * d.delta
        quant = d.g * np.round(exact / d.g)
        err = quant - exact
        extra = -err * d.ret_T                    # extra $ P&L from mis-sized short
        ev_err_ps = extra / sh                    # per share
        rows.append(dict(
            strat=name, clip_usd=clip,
            hedge_usd_mean=round(exact.mean(), 1),
            abs_err_usd=round(err.abs().mean(), 2),
            rel_err_pct=round(100 * (err.abs() / exact.clip(lower=1e-9)).mean(), 1),
            zero_hedge_pct=round(100 * (quant == 0).mean(), 1),
            ev_shift_cps=round(100 * ev_err_ps.mean(), 3),
            sd_add_cps=round(100 * ev_err_ps.std(), 3)))
        print(rows[-1], flush=True)

res = pd.DataFrame(rows)
res.to_csv(f"{LAB}/quantization_summary.csv", index=False)
print("\n", res.to_string())

# per-asset at $50 clip
d = pd.read_csv(f"{LAB}/trades_taker_train_funding.csv")
d["g"] = [gran(r.asset, r.S_bin) for r in d.itertuples()]
sh = 50 / d.entry
exact = sh * d.delta
quant = d.g * np.round(exact / d.g)
err = (quant - exact).abs()
t = d.assign(hedge=exact, err=err, rel=100 * err / exact).groupby("asset")[
    ["hedge", "err", "rel", "g"]].mean().round(2)
print("\ntaker, $50 clip, by asset:\n", t.to_string())
