"""Re-run incumbent S1 (taker-YES) and S2 (maker-YES) rules with variant FVs.

Variants: incumbent panel fv | seasonal vol-time | seasonal + Student-t(df).
Same rule constants as the frozen incumbent; delta uses the variant's own sigma.
Usage: python3 run_signals.py train|test
"""
import sys, math, pathlib
import numpy as np
import pandas as pd
from scipy.stats import norm

OUT = pathlib.Path("/home/user/D1/results/improve/fv-upgrade")
sys.path.insert(0, str(OUT))
sys.path.insert(0, "/home/user/D1/scripts")
import fv2
from fv2 import (DATA, SPLIT_S, YR, hourly_profile, build_voltime, load_smiles,
                 attach_smiles, core, digital)
from evaluate import fee, dedup_trades

ASSETS = ["BTC", "ETH", "SOL", "XRP"]
SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}


def build_variants(tdfs=(8,)):
    parts = []
    for a in ASSETS:
        p = pd.read_parquet(DATA / f"panel_{a}.parquet")
        p = p[p.result_id >= 0].copy()
        p["asset"] = a
        prof = hourly_profile(a, train_only=True, by_week=True)
        vt = build_voltime(prof, p.ts.min(), p.T_pm.max())
        m = attach_smiles(p, load_smiles(a))
        co = core(m, voltime=vt)
        m["fv_sea"] = digital(co)
        m["sig_sea"] = co["sig"]
        for df_ in tdfs:
            m[f"fv_sea_t{df_}"] = digital(co, tdf=df_)
        kl = pd.read_parquet(DATA / f"binance/{SYM[a]}_1m.parquet")
        close_map = dict(zip((kl["open_time_us"] // 1000000 + 60).astype(int), kl["close"]))
        m["S_T"] = m["T_pm"].astype(int).map(close_map)
        parts.append(m)
        print(a, "variants built", flush=True)
    allm = pd.concat(parts, ignore_index=True)
    allm["mid"] = (allm.bid + allm.ask) / 2
    allm["spread"] = allm.ask - allm.bid
    allm = allm.sort_values(["slug", "ts"]).reset_index(drop=True)
    g = allm.groupby("slug")
    for c in ["bid", "ask"]:
        allm["n_" + c] = g[c].shift(-1)
    allm["n_ts"] = g["ts"].shift(-1)
    ok = (allm["n_ts"] - allm["ts"]) <= 1800
    allm.loc[~ok, ["n_bid", "n_ask"]] = np.nan
    allm["exp_dt"] = pd.to_datetime(allm["T_pm"], unit="s", utc=True)
    allm["is_test"] = allm["exp_dt"] >= pd.Timestamp("2026-05-16", tz="UTC")
    allm["event"] = allm["exp_dt"].dt.date
    allm["ret_T"] = np.log(allm["S_T"] / allm["S_bin"])
    return allm


def delta_from(sig, tte_d, S_bin, K):
    tau = np.maximum(tte_d / 365.0, 1e-9)
    v = np.maximum(sig, 1e-4) * np.sqrt(tau)
    d2 = np.log(S_bin / K) / v - v / 2
    return norm.pdf(d2) / v


def clustered(d, col="ev_hedged"):
    g = d.groupby(["asset", "event"])[col].mean()
    se = g.std() / max(np.sqrt(len(g)), 1)
    return dict(n=len(d), n_events=len(g), ev=float(d[col].mean()),
                ev_ev=float(g.mean()), t=float(g.mean() / se) if se > 0 else np.nan,
                hit=float(d.label.mean()))


def s1_taker(m, fvcol, sigcol):
    sel = m[(m.ask > 0.02) & (m.ask < 0.98) & (m.spread <= 0.05)]
    sel = sel[(sel[fvcol] - sel.ask > 0.05) & (sel.tte_d >= 3) & (sel.tte_d < 8)]
    sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
    d = sel.dropna(subset=["n_ask", "S_T"]).copy()
    delta = delta_from(d[sigcol].values, d.tte_d.values, d.S_bin.values, d.K.values)
    px = d["n_ask"]
    d["ev_raw"] = d.label - px - fee(px)
    d["ev_hedged"] = d["ev_raw"] - delta * d["ret_T"]
    return d


def s2_maker(m, fvcol, sigcol, horizon_bars=8):
    sel = m[(m.ask > 0.02) & (m.ask < 0.98)]
    sel = sel[(sel[fvcol] - (sel.bid + 0.01) > 0.025) & (sel.tte_d < 8)]
    sel = dedup_trades(sel, cols=("slug",), cooldown_h=24)
    bys = {s: g.reset_index(drop=True) for s, g in
           m[["slug", "ts", "ask", "bid"]].groupby("slug")}
    trades = []
    for r in sel.itertuples():
        gs = bys.get(r.slug)
        g = gs[gs.ts > r.ts].head(horizon_bars)
        lim = r.bid + 0.01
        fill = g[g.ask <= lim]
        if len(fill) == 0:
            continue
        sig = getattr(r, sigcol)
        tau = max(r.tte_d / 365.0, 1e-9)
        v = max(sig, 1e-4) * math.sqrt(tau)
        d2 = math.log(r.S_bin / r.K) / v - v / 2
        delta = norm.pdf(d2) / v
        if not np.isfinite(r.ret_T):
            continue
        ev_raw = r.label - lim
        trades.append(dict(asset=r.asset, event=r.event, slug=r.slug, ts=r.ts,
                           ev_raw=ev_raw, ev_hedged=ev_raw - delta * r.ret_T,
                           label=r.label))
    d = pd.DataFrame(trades)
    d.attrs["fill_rate"] = len(d) / max(len(sel), 1)
    return d


def main(sample="train"):
    tdfs = (8, 12)
    allm = build_variants(tdfs)
    m = allm[allm.is_test] if sample == "test" else allm[~allm.is_test]
    variants = [("incumbent", "fv", "sigma"), ("seasonal", "fv_sea", "sig_sea")]
    for df_ in tdfs:
        variants.append((f"seasonal_t{df_}", f"fv_sea_t{df_}", "sig_sea"))
    rows = []
    for name, fvcol, sigcol in variants:
        d1 = s1_taker(m, fvcol, sigcol)
        st = clustered(d1)
        st.update(strategy="S1_taker", variant=name, sample=sample,
                  ev_raw=float(d1.ev_raw.mean()))
        rows.append(st)
        d2 = s2_maker(m, fvcol, sigcol)
        st = clustered(d2)
        st.update(strategy="S2_maker", variant=name, sample=sample,
                  ev_raw=float(d2.ev_raw.mean()), fill_rate=d2.attrs["fill_rate"])
        rows.append(st)
        print(name, "done", flush=True)
    t = pd.DataFrame(rows)[["strategy", "variant", "sample", "n", "n_events", "hit",
                            "ev_raw", "ev", "ev_ev", "t"] + (["fill_rate"] if True else [])]
    t.to_csv(OUT / f"signals_{sample}.csv", index=False)
    pd.set_option("display.width", 220)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "train")
