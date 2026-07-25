"""Family 1: MAKER-LEGGED VERTICALS — adjacent-strike bucket vs options RND mass,
BOTH legs resting maker orders (fee 0), tape fill truth per leg.

  long-between  (PM underprices the bucket): YES bid on K_lo at bid_lo+1c
                + NO bid on K_hi == resting YES ask at ask_hi-1c.
                Payoff 1+between, fair 1+fv_mass, cost lim_lo + (1-lim_hi_ask).
  short-between (PM overprices): YES ask on K_lo at ask_lo-1c + YES bid on K_hi.

Accounting per signal (task spec): if BOTH legs fill within the window H, hold the
vertical to settlement (delta-neutral-ish by construction; residual hedged with
net digital delta, LINEAR). If only ONE leg fills by window end, UNWIND it as a
taker at the first clean panel bar at/after t0+H (pay fee + spread), hedging the
round trip linearly; if no clean quote exists, hold hedged to settlement (rare).
Non-crossing guards: bid+1c < ask and ask-1c > bid on the respective leg.

Usage: python nf_verticals.py train|test
"""
import sys

import numpy as np
import pandas as pd

from nf_common import load_all, Tape, FastTape, Spot1m, fee, clustered, OUT

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
H_HOURS = 4
FROZEN = dict(thr=0.03, tte_lo=0, tte_hi=8, side="long")  # set after train tuning


def pair_signals(m, thr, tte_lo, tte_hi):
    m = m[(m.tte_d >= tte_lo) & (m.tte_d < tte_hi)]
    m = m[(m.bid > 0.01) & (m.ask < 0.99) & (m.spread >= 0.02) & (m.spread <= 0.10)]
    m = m.sort_values(["asset", "T_pm", "ts", "K"]).reset_index(drop=True)
    lo = m
    hi = m.shift(-1)
    same = (lo.asset == hi.asset) & (lo.T_pm == hi.T_pm) & (lo.ts == hi.ts)
    fv_mass = lo.fv - hi.fv
    lim_lo = lo.bid + 0.01                 # YES bid on lo (long); < ask by spread>=2c
    lim_hi_ask = hi.ask - 0.01             # YES ask on hi (long); > bid by spread>=2c
    edge_l = (1 + fv_mass) - (lim_lo + (1 - lim_hi_ask))
    lim_lo_ask = lo.ask - 0.01             # YES ask on lo (short)
    lim_hi = hi.bid + 0.01                 # YES bid on hi (short)
    edge_s = (1 - fv_mass) - ((1 - lim_lo_ask) + lim_hi)
    base = pd.DataFrame(dict(asset=lo.asset, event=lo.event, ts=lo.ts,
                             slug_lo=lo.slug, slug_hi=hi.slug, tte_d=lo.tte_d,
                             fv_mass=fv_mass, lab_lo=lo.label, lab_hi=hi.label,
                             d_lo=lo.delta, d_hi=hi.delta,
                             retlin_T=lo.retlin_T, S_bin=lo.S_bin, S_T=lo.S_T,
                             lim_lo=lim_lo, lim_hi_ask=lim_hi_ask,
                             lim_lo_ask=lim_lo_ask, lim_hi=lim_hi))
    sl = base[same & (edge_l > thr)].copy()
    sl["side"], sl["edge"] = "long", edge_l[same & (edge_l > thr)]
    ss = base[same & (edge_s > thr)].copy()
    ss["side"], ss["edge"] = "short", edge_s[same & (edge_s > thr)]
    s = pd.concat([sl, ss], ignore_index=True)
    if len(s) == 0:
        return s
    s["pair"] = s.slug_lo + "|" + s.slug_hi + "|" + s.side
    s = s.sort_values("ts")
    keep, last = [], {}
    for r in s.itertuples():
        if r.pair not in last or r.ts - last[r.pair] >= 86400:
            keep.append(r.Index)
            last[r.pair] = r.ts
    return s.loc[keep]


def unwind_quote(bys, slug, t_after):
    """First clean (non-crossed, both-sided) panel bar at/after t_after."""
    g = bys.get(slug)
    if g is None:
        return None
    w = g[(g.ts >= t_after) & (g.bid <= g.ask) & g.bid.notna() & g.ask.notna()
          & (g.bid > 0) & (g.ask < 1)]
    return None if len(w) == 0 else w.iloc[0]


def run(tape, ft, spot, sigs, bys):
    rows = []
    H = H_HOURS * 3600
    for r in sigs.sort_values(["slug_lo", "slug_hi", "ts"]).itertuples():
        t1 = r.ts + H
        if not (tape.coverage(r.slug_lo, r.ts, t1) and tape.coverage(r.slug_hi, r.ts, t1)):
            continue
        if r.side == "long":
            legs = [(r.slug_lo, "bid", r.lim_lo, +1, r.lab_lo, r.d_lo),
                    (r.slug_hi, "ask", r.lim_hi_ask, -1, r.lab_hi, r.d_hi)]
        else:
            legs = [(r.slug_lo, "ask", r.lim_lo_ask, -1, r.lab_lo, r.d_lo),
                    (r.slug_hi, "bid", r.lim_hi, +1, r.lab_hi, r.d_hi)]
        fills = []
        for slug, typ, lim, sgn, lab, dlt in legs:
            tf = (ft.bid_fill_t(slug, r.ts, t1, lim) if typ == "bid"
                  else ft.ask_fill_t(slug, r.ts, t1, lim))
            fills.append(tf)
        nf = sum(tf is not None for tf in fills)
        pnl, dnet, unwound = 0.0, 0.0, 0
        if nf == 2:
            for (slug, typ, lim, sgn, lab, dlt), tf in zip(legs, fills):
                if sgn > 0:              # long YES at lim
                    pnl += lab - lim
                    dnet += dlt
                else:                    # long NO at 1-lim (sold YES at lim)
                    pnl += (1 - lab) - (1 - lim)
                    dnet -= dlt
            pnl -= dnet * r.retlin_T     # LINEAR hedge of the residual net delta
        elif nf == 1:
            i = 0 if fills[0] is not None else 1
            slug, typ, lim, sgn, lab, dlt = legs[i]
            q = unwind_quote(bys, slug, t1)
            if q is None:                # cannot unwind: hold hedged to settlement
                if sgn > 0:
                    pnl = lab - lim - dlt * r.retlin_T
                else:
                    pnl = (1 - lab) - (1 - lim) + dlt * r.retlin_T
            else:
                unwound = 1
                S_u = spot.at(r.asset, q.ts)
                rl = (S_u / r.S_bin - 1) if np.isfinite(S_u) else 0.0
                if sgn > 0:              # sell YES at bid, taker fee
                    pnl = q.bid - lim - fee(q.bid) - dlt * rl
                else:                    # bought NO at 1-lim; sell NO at 1-ask
                    pnl = (1 - q.ask) - (1 - lim) - fee(1 - q.ask) + dlt * rl
        rows.append(dict(asset=r.asset, event=r.event, ts=r.ts, side=r.side,
                         edge=r.edge, tte_d=r.tte_d, nfill=nf, unwound=unwound,
                         both=int(nf == 2), ev_hedged=pnl,
                         label=r.lab_lo - r.lab_hi))
    return pd.DataFrame(rows)


def main():
    allp = load_all()
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    bys = {s: g.sort_values("ts").reset_index(drop=True)[
        ["ts", "bid", "ask"]] for s, g in m.groupby("slug")}
    tape, spot = Tape(), Spot1m()
    ft = FastTape(tape)
    rows = []
    thrs = (0.02, 0.03, 0.05) if sample == "train" else (FROZEN["thr"],)
    for thr in thrs:
        sigs = pair_signals(m, thr, 0, 8)
        if len(sigs) == 0:
            continue
        d_all = run(tape, ft, spot, sigs, bys)
        if len(d_all) < 5:
            continue
        d_all.to_csv(OUT / f"vert_trades_{sample}_thr{thr}.csv", index=False)
        ttes = [(0, 8), (0, 3), (3, 8)] if sample == "train" else \
            [(FROZEN["tte_lo"], FROZEN["tte_hi"])]
        for tlo, thi in ttes:
            d = d_all[(d_all.tte_d >= tlo) & (d_all.tte_d < thi)]
            for side, ds in d.groupby("side"):
                if sample == "test" and side != FROZEN["side"]:
                    continue
                act = ds[ds.nfill > 0]           # money at risk
                if len(act) < 5:
                    continue
                st = clustered(act)
                stb = clustered(ds[ds.both == 1]) if ds.both.sum() >= 3 else {}
                rows.append(dict(thr=thr, tte=f"{tlo}-{thi}", side=side,
                                 sample=sample, n_sig=len(ds),
                                 both_rate=ds.both.mean(),
                                 one_leg=(ds.nfill == 1).mean(),
                                 ev_both=stb.get("ev_ev", np.nan),
                                 t_both=stb.get("t", np.nan),
                                 n_both=stb.get("n", 0), **st))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"vert_grid_{sample}.csv", index=False)
    pd.set_option("display.width", 250)
    print(res.round(4).to_string())


if __name__ == "__main__":
    main()
