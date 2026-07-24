"""Diagnostics on train: (a) S2 hedged EV by tte band, incumbent vs seasonal FV;
(b) reliability by fv bucket for clock vs seasonal, extrap zone and short tte."""
import sys, pathlib
import numpy as np
import pandas as pd

OUT = pathlib.Path("/home/user/D1/results/improve/fv-upgrade")
sys.path.insert(0, str(OUT))
import run_signals as rs
from fv2 import calib_by

allm = rs.build_variants(tdfs=())
m = allm[~allm.is_test]

rows = []
for name, fvcol, sigcol in [("incumbent", "fv", "sigma"), ("seasonal", "fv_sea", "sig_sea")]:
    d2 = rs.s2_maker(m, fvcol, sigcol)
    d2 = d2.merge(m[["slug", "ts", "tte_d", "extrap"]], on=["slug", "ts"], how="left")
    for bname, msk in [("tte<1d", d2.tte_d < 1), ("1-3d", (d2.tte_d >= 1) & (d2.tte_d < 3)),
                       ("3-8d", d2.tte_d >= 3), ("extrap", d2.extrap == 1)]:
        g = d2[msk]
        if len(g) < 5:
            continue
        st = rs.clustered(g)
        st.update(variant=name, band=bname)
        rows.append(st)
t = pd.DataFrame(rows)[["variant", "band", "n", "n_events", "hit", "ev", "ev_ev", "t"]]
print("=== S2 maker by tte band (train) ===")
print(t.to_string(index=False))
t.to_csv(OUT / "train_s2_by_band.csv", index=False)

# reliability by fv bucket, extrap zone
tr = m.copy()
tr["bucket"] = pd.cut(tr["fv"], [0, .02, .05, .1, .2, .4, .6, .8, .9, .95, .98, 1.0])
parts = []
for scope, g in [("extrap", tr[tr.extrap == 1]), ("tte<1d", tr[tr.tte_d < 1]),
                 ("all", tr)]:
    for name, col in [("clock", "fv"), ("seasonal", "fv_sea")]:
        gg = g.copy()
        gg["bucket"] = pd.cut(gg[col], [0, .02, .05, .1, .2, .4, .6, .8, .9, .95, .98, 1.0])
        cb = calib_by(gg, col, "bucket")
        cb["variant"] = name
        cb["scope"] = scope
        parts.append(cb)
rel = pd.concat(parts, ignore_index=True)
rel.to_csv(OUT / "train_reliability.csv", index=False)
for scope in ["extrap", "tte<1d"]:
    print(f"=== reliability {scope} (train): mean_fv vs mean_label ===")
    r = rel[rel.scope == scope]
    piv = r.pivot_table(index="bucket", columns="variant",
                       values=["mean_fv", "mean_label", "n"], observed=True)
    print(piv.round(4).to_string())
