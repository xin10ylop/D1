"""Study 3: MAKER EARLY-EXIT vs hold-to-settlement.

Position entered at t0/price P (S1 taker at next-bar ask, or S2 tape fill at bid+1c).
From the first panel bar after t0 where bid >= fv (edge gone), rest an ASK at
A = max(fv, bid) + 0.01, requoted every bar while the condition holds. Exit fill
truth: 'buy' print >= A (strict > if not improving the book ask) within that bar's
15-min window. Hedge runs from entry to exit (or to T if held).
Metrics: EV/trade, days held, EV per $-day (capital velocity), both policies.
Missing tape days => no exit fill (biases toward hold; coverage reported).
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/user/D1/scripts")
import exec_lab as ex

SCRATCH = "/tmp/claude-0/-home-user-D1/03063d19-81f4-5b78-bafd-dd1b243986b8/scratchpad"
OUT = "/home/user/D1/results/improve/execution-lab"


def simulate_exits(pos, allp, tape):
    """pos: DataFrame with asset,event,slug,t0,P,delta,label,T_pm,S_T."""
    bys = {s: g.sort_values("ts").reset_index(drop=True) for s, g in
           allp[allp.slug.isin(pos.slug.unique())].groupby("slug")}
    buys = {}
    rows = []
    for r in pos.itertuples():
        g = bys[r.slug]
        if r.slug not in buys:
            t = tape.trades(r.slug)
            b = t[t.side == "buy"]
            buys[r.slug] = (b.timestamp_us.values, b.price.values)
        bt, bp = buys[r.slug]
        gg = g[(g.ts > r.t0) & (g.ts < r.T_pm)]
        S0_i = g.ts.searchsorted(r.t0)
        S0 = g.S_bin.iloc[min(S0_i, len(g) - 1)]
        exit_px, exit_t, S_exit = np.nan, np.nan, np.nan
        tsv = gg.ts.values
        for j, b in enumerate(gg.itertuples()):
            if not (np.isfinite(b.bid) and np.isfinite(b.fv) and b.bid >= b.fv):
                continue
            A = round(max(b.fv, b.bid) + 0.01, 4)
            if A >= 1:
                continue
            strict = np.isfinite(b.ask) and A >= b.ask - 1e-9
            w_end = tsv[j + 1] if j + 1 < len(tsv) else min(b.ts + 900, r.T_pm)
            i0 = np.searchsorted(bt, b.ts * 1e6, side="right")
            i1 = np.searchsorted(bt, w_end * 1e6, side="right")
            seg = bp[i0:i1]
            okm = seg > A + 1e-9 if strict else seg >= A - 1e-9
            if okm.any():
                k = i0 + int(np.argmax(okm))
                exit_px, exit_t, S_exit = A, bt[k] / 1e6, b.S_bin
                break
        ev_hold = r.label - r.P - r.delta * np.log(r.S_T / S0)
        days_hold = (r.T_pm - r.t0) / 86400
        if np.isfinite(exit_px):
            ev_x = exit_px - r.P - r.delta * np.log(S_exit / S0)
            days_x = (exit_t - r.t0) / 86400
        else:
            ev_x, days_x = ev_hold, days_hold
        rows.append(dict(asset=r.asset, event=r.event, slug=r.slug, t0=r.t0, P=r.P,
                         label=r.label, exited=np.isfinite(exit_px), exit_px=exit_px,
                         days_hold=days_hold, days_exitpol=days_x,
                         ev_hold=ev_hold, ev_exitpol=ev_x))
    return pd.DataFrame(rows)


def summarize(e, name):
    out = []
    for pol in ["hold", "exitpol"]:
        st = ex.clustered(e.assign(ev_hedged=e[f"ev_{pol}"]))
        days = e[f"days_{pol}" if pol != "hold" else "days_hold"]
        cap_days = (e.P * days).sum()
        out.append(dict(book=name, policy=pol, n=st["n"], n_events=st["n_events"],
                        ev_ev=st["ev_ev"], t=st["t"], mean_days=days.mean(),
                        ev_per_dollar_day=e[f"ev_{pol}"].sum() / cap_days,
                        exit_rate=e.exited.mean()))
    return out


def run(sample="train"):
    allp = pd.read_parquet(SCRATCH + "/allp.parquet")
    tape = ex.Tape()
    res = []
    # Book A: S1 taker positions (entry next-bar ask + fee at ts+15m)
    s1 = ex.signals_s1(allp, sample).dropna(subset=["n_ask", "S_T"])
    posA = pd.DataFrame(dict(asset=s1.asset, event=s1.event, slug=s1.slug,
                             t0=s1.ts + 900, P=s1.n_ask + ex.fee(s1.n_ask),
                             delta=s1.delta, label=s1.label, T_pm=s1.T_pm, S_T=s1.S_T))
    eA = simulate_exits(posA, allp, tape)
    eA.to_csv(f"{OUT}/s3_earlyexit_s1_{sample}.csv", index=False)
    res += summarize(eA, "S1_taker")
    # Book B: S2 tape fills at bid+1c (from study 2 dump)
    d = pd.read_parquet(SCRATCH + f"/s2_fills_{sample}.parquet")
    f = d[d.covd & d["ft_bid+1c"].notna()].copy()
    f["S_T"] = f.S_bin * np.exp(f.ret_T)
    posB = pd.DataFrame(dict(asset=f.asset, event=f.event, slug=f.slug,
                             t0=f["ft_bid+1c"], P=f["L_bid+1c"], delta=f.delta,
                             label=f.label, T_pm=f.T_pm, S_T=f.S_T))
    eB = simulate_exits(posB, allp, tape)
    eB.to_csv(f"{OUT}/s3_earlyexit_s2_{sample}.csv", index=False)
    res += summarize(eB, "S2_maker_bid+1c")
    out = pd.DataFrame(res)
    out.to_csv(f"{OUT}/s3_earlyexit_summary_{sample}.csv", index=False)
    pd.set_option("display.width", 220)
    print(f"== Study 3 early exit sample={sample} ==")
    print(out.round(4).to_string())


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "train")
