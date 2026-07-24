"""ONE test look for the execution lab — frozen configs only (tuned on train).

Frozen policy under test:
  P1 (S1 entry): aggressive maker — rest YES bid at ask-0.01; if unfilled after
     30m, cross at the next bar's ask (taker+fee). Baseline: incumbent taker.
  P2 (S2 entry): rest at bid+0.01, cancel at 2h (vs incumbent rest-4h baseline).
     Hedge placed at ORDER PLACEMENT (pre-hedge); unfilled orders charged the
     pre-hedge P&L over the resting window.
  P3 (exit):     early-exit ask at max(fv,bid)+0.01 once bid>=fv, vs hold —
     report EV/trade and EV per $-day (descriptive adopt for capital velocity).
  Staleness buckets on test fills: descriptive only.
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/user/D1/scripts")
import exec_lab as ex
import exec_study1
import exec_study3

SCRATCH = "/tmp/claude-0/-home-user-D1/03063d19-81f4-5b78-bafd-dd1b243986b8/scratchpad"
OUT = "/home/user/D1/results/improve/execution-lab"
DL = 7200   # frozen S2 cancel deadline
H = 14400   # baseline horizon


def s2_test(allp, tape):
    sig = ex.signals_s2(allp, "test")
    bys = {s: g.sort_values("ts").reset_index(drop=True) for s, g in
           allp[allp.slug.isin(sig.slug.unique())].groupby("slug")}
    rows = []
    for slug, grp in sig.groupby("slug"):
        g = bys.get(slug)
        for r in grp.itertuples():
            cov = tape.coverage(slug, r.ts, r.ts + H)
            L = round(r.bid + 0.01, 4)
            ft = None
            if cov and 0 < L < 1:
                ft = tape.first_bid_fill(slug, r.ts, r.ts + H, L,
                                         strict=L <= r.bid + 1e-9)
            # underlying at cancel (2h) for unfilled pre-hedge P&L
            i = min(max(g.ts.searchsorted(r.ts + DL) - 1, 0), len(g) - 1)
            S_c = g.S_bin.iloc[i]
            rows.append(dict(asset=r.asset, event=r.event, slug=slug, ts=r.ts,
                             T_pm=r.T_pm, bid=r.bid, ask=r.ask, fv=r.fv,
                             label=r.label, delta=r.delta, ret_T=r.ret_T,
                             S_bin=r.S_bin, tte_d=r.tte_d, covd=cov,
                             **{"L_bid+1c": L, "ft_bid+1c": ft}, S_cancel=S_c))
    d = pd.DataFrame(rows)
    d.to_parquet(f"{SCRATCH}/s2_fills_test.parquet")
    d.to_csv(f"{OUT}/s2_fills_test.csv", index=False)
    dc = d[d.covd].copy()

    def pol(name, deadline):
        ft = dc["ft_bid+1c"]
        filled = ft.notna() if deadline is None else ft.notna() & (ft <= dc.ts + deadline)
        ev = (dc.label - dc["L_bid+1c"] - dc.delta * dc.ret_T).where(filled)
        stf = ex.clustered(dc[filled].assign(ev_hedged=ev[filled]))
        sts = ex.clustered(dc.assign(ev_hedged=ev.fillna(0.0)))
        # pre-hedge accounting: unfilled carry short-delta P&L to cancel/horizon
        unpnl = -dc.delta * np.log(dc.S_cancel / dc.S_bin)
        evph = ev.where(filled, unpnl if deadline == DL else 0.0)
        stp = ex.clustered(dc.assign(ev_hedged=evph)) if deadline == DL else {}
        return dict(policy=name, fill_rate=filled.mean(), n_signals=len(dc),
                    n_fills=stf.get("n"), ev_fill=stf.get("ev_ev"),
                    t_fill=stf.get("t"), hit=stf.get("hit"),
                    ev_per_signal=sts.get("ev_ev"), t_sig=sts.get("t"),
                    ev_sig_prehedge=stp.get("ev_ev"), t_prehedge=stp.get("t"))

    out = pd.DataFrame([pol("rest_4h(baseline)", None), pol("cancel@2h(FROZEN)", DL)])
    out.to_csv(f"{OUT}/s2_frozen_test.csv", index=False)
    print(f"== S2 test  signals={len(d)} covered={len(dc)} ({d.covd.mean():.1%}) ==")
    print(out.round(4).to_string())

    # staleness (descriptive, 4h fills)
    f = dc[dc["ft_bid+1c"].notna()].copy()
    f["lat_s"] = f["ft_bid+1c"] - f.ts
    f["bucket"] = pd.cut(f.lat_s, [0, 300, 1800, 7200, 14400],
                         labels=["<5m", "5-30m", "30m-2h", "2h-4h"])
    f["ev_hedged"] = f.label - f["L_bid+1c"] - f.delta * f.ret_T
    st = []
    for b in ["<5m", "5-30m", "30m-2h", "2h-4h"]:
        s = ex.clustered(f[f.bucket == b])
        s["bucket"] = b
        st.append(s)
    stal = pd.DataFrame(st)
    stal.to_csv(f"{OUT}/s2_staleness_test.csv", index=False)
    print("== staleness test ==")
    print(stal.round(4).to_string())


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    allp = pd.read_parquet(SCRATCH + "/allp.parquet")
    tape = ex.Tape()
    # P1: frozen H=30m only (+ baseline taker) — exec_study1 prints/saves
    exec_study1.run("test", horizons=(1800,))
    # P2 + staleness
    s2_test(allp, tape)
    # P3: early exit vs hold on test books
    exec_study3.run("test")
