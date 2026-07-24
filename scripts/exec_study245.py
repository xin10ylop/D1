"""Studies 2, 4, 5 on S2 maker-YES signals (fv - (bid+0.01) > 0.025, tte<8).

Study 2 LIMIT LADDER : rest at bid+0.01 vs mid-0.01 vs ask-0.01, H=4h, tape fills.
Study 4 REQUOTE      : rest 4h (incumbent) vs cancel@30m vs chase+1c@30m (gap recheck).
Study 5 STALENESS    : EV by fill latency bucket (<5m, 5-30m, 30m-2h, 2h-4h), rung bid+1c.
Also dumps per-signal fill detail (s2_fills_{sample}.parquet) for Study 3.
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/user/D1/scripts")
import exec_lab as ex

SCRATCH = "/tmp/claude-0/-home-user-D1/03063d19-81f4-5b78-bafd-dd1b243986b8/scratchpad"
OUT = "/home/user/D1/results/improve/execution-lab"
H = 14400  # 4h, matches incumbent maker horizon (16 x 15min bars)


def run(sample="train"):
    allp = pd.read_parquet(SCRATCH + "/allp.parquet")
    sig = ex.signals_s2(allp, sample)
    tape = ex.Tape()
    bys = {s: g.sort_values("ts").reset_index(drop=True) for s, g in
           allp[allp.slug.isin(sig.slug.unique())].groupby("slug")}
    rows = []
    for slug, grp in sig.groupby("slug"):
        g = bys.get(slug)
        for r in grp.itertuples():
            cov = tape.coverage(slug, r.ts, r.ts + H)
            rungs = {"bid+1c": round(r.bid + 0.01, 4),
                     "mid-1c": round((r.bid + r.ask) / 2 - 0.01, 4),
                     "ask-1c": round(r.ask - 0.01, 4)}
            row = dict(asset=r.asset, event=r.event, slug=slug, ts=r.ts, T_pm=r.T_pm,
                       bid=r.bid, ask=r.ask, fv=r.fv, gap=r.fv - rungs["bid+1c"],
                       label=r.label, delta=r.delta, ret_T=r.ret_T, S_bin=r.S_bin,
                       tte_d=r.tte_d, covd=cov)
            for name, L in rungs.items():
                ft = None
                if cov and 0 < L < 1:
                    strict = L <= r.bid + 1e-9
                    ft = tape.first_bid_fill(slug, r.ts, r.ts + H, L, strict=strict)
                row[f"L_{name}"] = L
                row[f"ft_{name}"] = ft
            # Study 4: chase policy on rung bid+1c. If unfilled in 30m, recheck gap
            # at the panel bar 30m later; chase to L0+0.01 if still > 0.025.
            L0 = rungs["bid+1c"]
            ft0 = row["ft_bid+1c"]
            ch_ft, ch_L, ch_state = None, np.nan, "na"
            if cov:
                if ft0 is not None and ft0 <= r.ts + 1800:
                    ch_ft, ch_L, ch_state = ft0, L0, "filled_early"
                else:
                    nb = g[(g.ts >= r.ts + 1800) & (g.ts <= r.ts + 3600)].head(1)
                    if len(nb):
                        b = nb.iloc[0]
                        if np.isfinite(b.fv) and b.fv - (L0 + 0.01) > 0.025:
                            L1 = round(L0 + 0.01, 4)
                            strict = np.isfinite(b.bid) and L1 <= b.bid + 1e-9
                            ft1 = tape.first_bid_fill(slug, r.ts + 1800, r.ts + H,
                                                      L1, strict=strict)
                            ch_ft, ch_L, ch_state = ft1, L1, "chased"
                        else:
                            ch_state = "cancelled_gap"
                    else:
                        ch_state = "cancelled_nobar"
            row.update(chase_ft=ch_ft, chase_L=ch_L, chase_state=ch_state)
            rows.append(row)
    d = pd.DataFrame(rows)
    d.to_parquet(f"{SCRATCH}/s2_fills_{sample}.parquet")
    d.to_csv(f"{OUT}/s2_fills_{sample}.csv", index=False)
    dc = d[d.covd].copy()

    def pol_stats(dd, L_col, ft_col, name, deadline=None):
        L, ft = dd[L_col], dd[ft_col]
        filled = ft.notna() if deadline is None else ft.notna() & (ft <= dd.ts + deadline)
        ev = (dd.label - L - dd.delta * dd.ret_T).where(filled)
        st_fill = ex.clustered(dd[filled].assign(ev_hedged=ev[filled]))
        st_sig = ex.clustered(dd.assign(ev_hedged=ev.fillna(0.0)))
        return dict(policy=name, fill_rate=filled.mean(), n_signals=len(dd),
                    ev_fill=st_fill.get("ev_ev"), t_fill=st_fill.get("t"),
                    n_fills=st_fill.get("n"), hit=st_fill.get("hit"),
                    ev_per_signal=st_sig.get("ev_ev"), t_sig=st_sig.get("t"))

    # Study 2 ladder
    lad = [pol_stats(dc, f"L_{k}", f"ft_{k}", f"ladder_{k}_4h") for k in
           ["bid+1c", "mid-1c", "ask-1c"]]
    # Study 4 requote
    req = [pol_stats(dc, "L_bid+1c", "ft_bid+1c", "rest_4h(incumbent)"),
           pol_stats(dc, "L_bid+1c", "ft_bid+1c", "cancel@30m", deadline=1800),
           pol_stats(dc, "chase_L", "chase_ft", "chase+1c@30m")]
    out = pd.DataFrame(lad + req)
    out.to_csv(f"{OUT}/s2_ladder_requote_{sample}.csv", index=False)
    print(f"== Studies 2+4 sample={sample} signals={len(d)} covered={len(dc)} "
          f"({d.covd.mean():.1%}) ==")
    pd.set_option("display.width", 220)
    print(out.round(4).to_string())
    print("chase states:", dc.chase_state.value_counts().to_dict())

    # Study 5 staleness (rung bid+1c fills)
    f = dc[dc["ft_bid+1c"].notna()].copy()
    f["lat_s"] = f["ft_bid+1c"] - f.ts
    bins = [0, 300, 1800, 7200, 14400]
    labs = ["<5m", "5-30m", "30m-2h", "2h-4h"]
    f["bucket"] = pd.cut(f.lat_s, bins, labels=labs)
    f["ev_hedged"] = f.label - f["L_bid+1c"] - f.delta * f.ret_T
    st = []
    for b in labs:
        s = ex.clustered(f[f.bucket == b])
        s["bucket"] = b
        st.append(s)
    stal = pd.DataFrame(st)
    stal.to_csv(f"{OUT}/s2_staleness_{sample}.csv", index=False)
    print(f"== Study 5 staleness sample={sample} ==")
    print(stal.round(4).to_string())


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "train")
