"""Study 1: AGGRESSIVE MAKER (rest bid at ask-0.01) vs incumbent taker, on S1 signals.

Per signal, tape-truth fill within H in {30m, 2h, 4h}. Policies compared per-signal:
  taker      : incumbent, next-bar ask + fee
  am_nofb    : aggressive maker; unfilled -> no trade (EV 0)
  am_fb      : aggressive maker; unfilled at H -> cross at the bar after ts+H (taker+fee)
Adverse selection: taker-counterfactual EV of filled vs unfilled cohorts.
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/user/D1/scripts")
import exec_lab as ex

SCRATCH = "/tmp/claude-0/-home-user-D1/03063d19-81f4-5b78-bafd-dd1b243986b8/scratchpad"
OUT = "/home/user/D1/results/improve/execution-lab"


def run(sample="train", horizons=(1800, 7200, 14400)):
    allp = pd.read_parquet(SCRATCH + "/allp.parquet")
    sig = ex.signals_s1(allp, sample)
    tape = ex.Tape()
    bys = {s: g.sort_values("ts").reset_index(drop=True) for s, g in
           allp[allp.slug.isin(sig.slug.unique())].groupby("slug")}
    Hmax = max(horizons)
    rows = []
    for slug, grp in sig.groupby("slug"):
        for r in grp.itertuples():
            cov = tape.coverage(slug, r.ts, r.ts + Hmax)
            L = round(r.ask - 0.01, 4)
            strict = L <= r.bid + 1e-9   # not improving best bid -> need trade-through
            ft = tape.first_bid_fill(slug, r.ts, r.ts + Hmax, L, strict=strict) if cov else None
            # taker counterfactual
            ev_tk = np.nan
            if np.isfinite(r.n_ask) and np.isfinite(r.S_T):
                ev_tk = (r.label - r.n_ask - ex.fee(r.n_ask)) - r.delta * r.ret_T
            # fallback quotes at each horizon: bar after ts+H
            row = dict(asset=r.asset, event=r.event, slug=slug, ts=r.ts, ask=r.ask,
                       n_ask=r.n_ask, gap=r.gap_yes, label=r.label, delta=r.delta,
                       ret_T=r.ret_T, covd=cov, limit=L, fill_t=ft, ev_taker=ev_tk)
            g = bys.get(slug)
            for H in horizons:
                filled = ft is not None and ft <= r.ts + H
                row[f"fill_{H}"] = filled
                ev_mk = (r.label - L) - r.delta * r.ret_T if filled else np.nan
                row[f"ev_am_{H}"] = ev_mk
                # fallback: cross at first bar with ts > r.ts + H (within 30m of it)
                fb = np.nan
                if not filled and g is not None:
                    nb = g[g.ts > r.ts + H].head(1)
                    if len(nb) and nb.ts.iloc[0] - (r.ts + H) <= 1800:
                        a = nb.ask.iloc[0]
                        if np.isfinite(a) and 0 < a < 1:
                            fb = (r.label - a - ex.fee(a)) - r.delta * r.ret_T
                row[f"ev_fb_{H}"] = fb
            rows.append(row)
    d = pd.DataFrame(rows)
    d.to_csv(f"{OUT}/s1_aggressive_maker_{sample}.csv", index=False)

    dc = d[d.covd & d.ev_taker.notna()].copy()
    res = []
    res.append(dict(policy="taker(incumbent)", H="-", fill_rate=1.0,
                    **ex.clustered(dc.assign(ev_hedged=dc.ev_taker))))
    for H in horizons:
        f = dc[f"fill_{H}"]
        # unfilled->0
        ev0 = dc[f"ev_am_{H}"].fillna(0.0)
        res.append(dict(policy="am_unfilled=0", H=H, fill_rate=f.mean(),
                        **ex.clustered(dc.assign(ev_hedged=ev0))))
        # per-fill EV
        dcf = dc[f]
        res.append(dict(policy="am_per_fill", H=H, fill_rate=f.mean(),
                        **ex.clustered(dcf.assign(ev_hedged=dcf[f"ev_am_{H}"]))))
        # with taker fallback
        evfb = dc[f"ev_am_{H}"].where(f, dc[f"ev_fb_{H}"])
        dfb = dc[evfb.notna()]
        res.append(dict(policy="am_takerfallback", H=H, fill_rate=f.mean(),
                        **ex.clustered(dfb.assign(ev_hedged=evfb[evfb.notna()]))))
        # adverse selection: taker-counterfactual EV by fill status
        res.append(dict(policy="cf_taker|filled", H=H, fill_rate=f.mean(),
                        **ex.clustered(dc[f].assign(ev_hedged=dc.loc[f, "ev_taker"]))))
        res.append(dict(policy="cf_taker|unfilled", H=H, fill_rate=f.mean(),
                        **ex.clustered(dc[~f].assign(ev_hedged=dc.loc[~f, "ev_taker"]))))
    out = pd.DataFrame(res)
    out.to_csv(f"{OUT}/s1_aggressive_maker_summary_{sample}.csv", index=False)
    print(f"== Study 1 sample={sample}  signals={len(d)} covered={len(dc)} "
          f"(coverage {d.covd.mean():.1%}) ==")
    pd.set_option("display.width", 220)
    print(out.round(4).to_string())
    return out


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "train")
