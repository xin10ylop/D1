"""Part 3: hedge with the Deribit dated future nearest-after the PM expiry vs perp.

TRAIN ONLY (instrument choice is a policy decision; test look reserved).
For each trade pick the dated future (bars1h_) with expiry >= T_pm that has bars at
entry; short delta at entry close, cover at last bar close before T_pm.
No funding on dated. Fees 3.5bp/side both (task convention). Spread scenarios added
separately (dated futures are thin: tick 2.5 vs 0.5 BTC; low volume).
"""
import glob
import os
import re
import numpy as np
import pandas as pd

LAB = "/home/user/D1/results/improve/hedge-lab"
FUT = "/home/user/D1/data/deribit_fut"
FEE_BP = 3.5e-4

MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def parse_exp(name):
    m = re.match(r"(\d{1,2})([A-Z]{3})(\d{2})$", name)
    if not m:
        return None
    d, mon, y = int(m.group(1)), MON[m.group(2)], 2000 + int(m.group(3))
    return pd.Timestamp(y, mon, d, 8, tz="UTC").timestamp()  # 08:00 UTC expiry


# index dated futures per asset
FUTS = {}
PERPF = {}
for f in glob.glob(f"{FUT}/bars1h_*.parquet"):
    base = os.path.basename(f)[7:-8]
    asset = base.split("-")[0].replace("_USDC", "")
    if "PERPETUAL" in base:
        PERPF[asset] = f.replace("bars1h_", "bars1m_")   # use 1-min perp bars
        continue
    exp = parse_exp(base.split("-")[1])
    if exp:
        FUTS.setdefault(asset, []).append((exp, f))
for a in FUTS:
    FUTS[a].sort()

BARS = {}


def bars(f):
    if f not in BARS:
        b = pd.read_parquet(f).sort_values("ts_ms")
        b = b[np.isfinite(b.close) & (b.close > 0)]
        BARS[f] = (b.ts_ms.values / 1000.0, b.close.values)
    return BARS[f]


def leg_prices(f, t0, t1, tol=7200):
    t, c = bars(f)
    i0 = np.searchsorted(t, t0, side="right") - 1
    if i0 < 0 or t0 - t[i0] > tol:
        return None
    i1 = np.searchsorted(t, t1, side="right") - 1
    if i1 <= i0 or t1 - t[i1] > tol:
        return None
    return c[i0], c[i1]


def fut_hedge(r):
    """Return (fut_pnl, perp_fill_pnl, instrument, basis_entry, tte_fut_h) or None."""
    pp = leg_prices(PERPF[r.asset], r.entry_ts, r.T_pm, tol=900)
    if pp is None:
        return None
    perp_pnl = -r.delta * np.log(pp[1] / pp[0])
    for exp, f in FUTS.get(r.asset, []):
        if exp < r.T_pm:
            continue
        fp = leg_prices(f, r.entry_ts, r.T_pm)
        if fp is None:
            continue
        F0, F1 = fp
        pnl = -r.delta * np.log(F1 / F0)
        return (pnl, perp_pnl, os.path.basename(f)[7:-8],
                np.log(F0 / pp[0]), (exp - r.T_pm) / 3600)
    return None


def clus(d, col):
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / np.sqrt(len(g))
    return g.mean(), g.mean() / se if se > 0 else np.nan


rows = []
for name in ["taker", "maker"]:
    d = pd.read_csv(f"{LAB}/trades_{name}_train_funding.csv")
    res = [fut_hedge(r) for r in d.itertuples()]
    ok = np.array([x is not None for x in res])
    d = d[ok].copy()
    d["fut_pnl"] = [x[0] for x in res if x]
    d["perp_fill_pnl"] = [x[1] for x in res if x]
    d["fut_inst"] = [x[2] for x in res if x]
    d["basis_bp"] = [x[3] * 1e4 for x in res if x]
    d["fut_tte_h"] = [x[4] for x in res if x]
    fee_leg = 2 * FEE_BP * d.delta
    # three hedges on the SAME matched subset:
    # perp from signal-bar spot (incumbent convention), perp at fill, dated fut at fill
    d["ev_perp_sig"] = d.ev_raw - d.delta * d.ret_T + d.fund_pnl - fee_leg
    d["ev_perp"] = d.ev_raw + d.perp_fill_pnl + d.fund_pnl - fee_leg
    d["ev_fut"] = d.ev_raw + d.fut_pnl - fee_leg          # no funding
    for hs_bp in [2.5, 5.0]:                              # extra half-spread scenarios, dated only
        d[f"ev_fut_hs{hs_bp}"] = d.ev_fut - 2 * hs_bp * 1e-4 * d.delta
    d.to_csv(f"{LAB}/trades_{name}_train_datedfut.csv", index=False)
    evs, tsg = clus(d, "ev_perp_sig")
    evp, tp = clus(d, "ev_perp")
    evf, tf = clus(d, "ev_fut")
    ev25, _ = clus(d, "ev_fut_hs2.5")
    ev50, _ = clus(d, "ev_fut_hs5.0")
    rows.append(dict(
        strat=name, n=len(d), match_rate=round(ok.mean(), 3),
        ev_perp_sig=round(evs, 5), t_sig=round(tsg, 2),
        ev_perp=round(evp, 5), t_perp=round(tp, 2), sd_perp=round(d.ev_perp.std(), 4),
        ev_fut=round(evf, 5), t_fut=round(tf, 2), sd_fut=round(d.ev_fut.std(), 4),
        ev_fut_hs2p5bp=round(ev25, 5), ev_fut_hs5bp=round(ev50, 5),
        diff_fut_minus_perp=round((d.ev_fut - d.ev_perp).mean(), 5),
        diff_t=round(clus(d.assign(dd=d.ev_fut - d.ev_perp), "dd")[1], 2),
        timing_cost=round((d.ev_perp - d.ev_perp_sig).mean(), 5),
        basis_bp_mean=round(d.basis_bp.mean(), 1),
        fut_tte_h_mean=round(d.fut_tte_h.mean(), 1),
        corr=round(np.corrcoef(d.ev_fut, d.ev_perp)[0, 1], 4)))
    print(rows[-1], flush=True)
    print("instruments used sample:", d.fut_inst.value_counts().head(8).to_dict())

res = pd.DataFrame(rows)
res.to_csv(f"{LAB}/datedfut_summary.csv", index=False)
print(res.to_string())
