"""Mechanism-validation tests on the old-era fill panels.

(a) Calibration: Brier(fill vwap) vs Brier(FV), event-clustered diff.
(b) Incumbent-style signal: fv - traded_vwap > thr, tte bands -> hedged EV at
    next-print entry (NOT fill-realistic: no book; entry proxied by next slot vwap).
(c) Convergence: does subsequent traded price move toward FV (and/or FV toward price)?

Outputs: results/improve/mechanism-extend/{calibration.csv, signal_ev.csv, convergence.csv}
"""
import sys, math
import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, "/home/user/D1/scripts")
from common import DATA

ME = DATA.parent / "results/improve/mechanism-extend"
FEE_RATE = 0.07


def fee(p):
    return FEE_RATE * p * (1 - p)


def load():
    parts = []
    for a in ["BTC", "ETH"]:
        f = ME / f"fill_panel_{a}.parquet"
        if f.exists():
            parts.append(pd.read_parquet(f))
    p = pd.concat(parts, ignore_index=True)
    p["exp_dt"] = pd.to_datetime(p["T_pm"], unit="s", utc=True)
    p["event"] = p["exp_dt"].dt.date
    ex = p["exp_dt"].dt.tz_localize(None)
    p["quarter"] = ex.dt.to_period("Q").astype(str)
    p["year"] = ex.dt.year
    p["gap"] = p["fv"] - p["vwap"]  # >0: PM trades below options FV
    return p.sort_values(["slug", "ts"]).reset_index(drop=True)


def clus_t(df, col, by=("asset", "event")):
    g = df.groupby(list(by))[col].mean()
    if len(g) < 3:
        return np.nan, len(g)
    se = g.std() / math.sqrt(len(g))
    return (g.mean() / se if se > 0 else np.nan), len(g)


# ---------- (a) calibration ----------
def calibration(p):
    rows = []
    m = p[(p.vwap > 0.02) & (p.vwap < 0.98) & (p.extrap == 0)].copy()
    m["b_pm"] = (m.vwap - m.label) ** 2
    m["b_fv"] = (m.fv - m.label) ** 2
    m["d"] = m.b_pm - m.b_fv
    for name, sel in [("ALL", m), ("tte 0-1", m[m.tte_d < 1]),
                      ("tte 1-3", m[(m.tte_d >= 1) & (m.tte_d < 3)]),
                      ("tte 3-8", m[(m.tte_d >= 3) & (m.tte_d < 8)])] + \
                     [(f"Q {q}", m[m.quarter == q]) for q in sorted(m.quarter.unique())]:
        if len(sel) < 30:
            continue
        t, ne = clus_t(sel, "d")
        rows.append(dict(slice=name, n=len(sel), n_events=ne,
                         brier_pm=sel.b_pm.mean(), brier_fv=sel.b_fv.mean(),
                         diff=sel.d.mean(), t_diff=t))
    return pd.DataFrame(rows)


# ---------- (a2) directional mispricing: high-prob YES buckets ----------
def buckets(p):
    m = p[(p.extrap == 0)].copy()
    m["bucket"] = pd.cut(m.fv, [0.5, 0.7, 0.8, 0.9, 0.97, 1.0])
    rows = []
    for (yr, b), g in m.groupby(["year", "bucket"], observed=True):
        if len(g) < 30:
            continue
        t, ne = clus_t(g.assign(_d=g.fv - g.vwap), "_d")
        rows.append(dict(year=yr, fv_bucket=str(b), n=len(g), n_events=ne,
                         mean_vwap=g.vwap.mean(), mean_fv=g.fv.mean(),
                         mean_label=g.label.mean(), fv_minus_vwap=(g.fv - g.vwap).mean(),
                         t=t))
    return pd.DataFrame(rows)


# ---------- (b) incumbent signal ----------
def next_print(p, max_wait_s=7200):
    """For each slot, attach the next slot (>= ts+900) with prints within max_wait."""
    nxt = []
    for slug, g in p.groupby("slug"):
        ts = g.ts.values
        i2 = np.searchsorted(ts, ts + 900, side="left")
        ok = i2 < len(ts)
        n_ts = np.full(len(ts), np.nan)
        n_vwap = np.full(len(ts), np.nan)
        n_vbuy = np.full(len(ts), np.nan)
        n_sbin = np.full(len(ts), np.nan)
        idx2 = i2[ok]
        n_ts[ok] = ts[idx2]
        n_vwap[ok] = g.vwap.values[idx2]
        n_vbuy[ok] = g.vwap_buy.values[idx2]
        n_sbin[ok] = g.S_bin.values[idx2]
        late = n_ts - ts > max_wait_s
        n_vwap[late] = np.nan
        g2 = g.copy()
        g2["n_vwap"], g2["n_vwap_buy"], g2["n_Sbin"], g2["n_ts_"] = n_vwap, n_vbuy, n_sbin, n_ts
        nxt.append(g2)
    return pd.concat(nxt, ignore_index=True)


def dedup(df, cooldown_h=24):
    df = df.sort_values(["slug", "ts"])
    keep, last = [], {}
    for r in df.itertuples():
        if r.slug not in last or r.ts - last[r.slug] >= cooldown_h * 3600:
            keep.append(r.Index)
            last[r.slug] = r.ts
    return df.loc[keep]


def signal_ev(p, entry_col="n_vwap"):
    rows, trades_all = [], []
    m = p[(p.vwap > 0.02) & (p.vwap < 0.98) & (p.extrap == 0) & (p.sz >= 5)].copy()
    m["delta"] = norm.pdf(np.log(m.S_bin / m.K) / (m.sigma * np.sqrt(m.tte_d / 365))
                          - m.sigma * np.sqrt(m.tte_d / 365) / 2) / (m.sigma * np.sqrt(m.tte_d / 365))
    m["ret_T"] = np.log(m.S_T / m.S_bin)
    for thr in [0.025, 0.05]:
        for lo, hi in [(0, 8), (3, 8), (0, 3)]:
            sel = m[(m.gap > thr) & (m.tte_d >= lo) & (m.tte_d < hi)].copy()
            sel = dedup(sel)
            d = sel.dropna(subset=[entry_col, "S_T"]).copy()
            if len(d) < 5:
                continue
            px = d[entry_col]
            d["ev_raw"] = d.label - px - fee(px)
            d["ev_hedged"] = d.ev_raw - d.delta * d.ret_T
            t, ne = clus_t(d, "ev_hedged")
            traw, _ = clus_t(d, "ev_raw")
            rows.append(dict(slice="ALL", thr=thr, tte=f"{lo}-{hi}", n=len(d), n_events=ne,
                             ev_raw=d.ev_raw.mean(), t_raw=traw,
                             ev_hedged=d.ev_hedged.mean(), t_hedged=t, hit=d.label.mean(),
                             med_entry=px.median()))
            if thr == 0.05 and (lo, hi) == (3, 8):
                d["cfg"] = "S1-analog"
                trades_all.append(d)
            if thr == 0.025 and (lo, hi) == (0, 8):
                d["cfg"] = "S2-analog"
                trades_all.append(d)
            # by quarter (main cfgs only)
            if (thr, lo, hi) in [(0.05, 3, 8), (0.025, 0, 8), (0.05, 0, 8)]:
                for q, sq in d.groupby("quarter"):
                    if len(sq) < 3:
                        continue
                    tq, neq = clus_t(sq, "ev_hedged")
                    rows.append(dict(slice=f"Q {q}", thr=thr, tte=f"{lo}-{hi}", n=len(sq),
                                     n_events=neq, ev_raw=sq.ev_raw.mean(), t_raw=np.nan,
                                     ev_hedged=sq.ev_hedged.mean(), t_hedged=tq,
                                     hit=sq.label.mean(), med_entry=sq[entry_col].median()))
    tr = pd.concat(trades_all, ignore_index=True) if trades_all else pd.DataFrame()
    return pd.DataFrame(rows), tr


# ---------- (c) convergence ----------
def convergence(p):
    """Regress future price/FV changes on current gap, event-clustered SE."""
    rows = []
    m = p[(p.vwap > 0.02) & (p.vwap < 0.98) & (p.extrap == 0)].copy()
    for h_slots, tol in [(4, 2), (24, 8), (96, 24)]:
        tgt = []
        for slug, g in m.groupby("slug"):
            ts = g.ts.values
            # base = next print slot after t (fresh noise, >= t+900)
            i1 = np.searchsorted(ts, ts + 900, side="left")
            i2 = np.searchsorted(ts, ts + h_slots * 900, side="left")
            ok = (i2 < len(ts)) & (i1 < len(ts)) & (i2 > i1)
            f = {k: np.full(len(ts), np.nan) for k in ["b_vwap", "b_fv", "f_vwap", "f_fv"]}
            idx0 = np.where(ok)[0]
            within = ((ts[i2[ok]] - ts[idx0]) <= (h_slots + tol) * 900) & \
                     ((ts[i1[ok]] - ts[idx0]) <= 4 * 900)
            sel = idx0[within]
            f["b_vwap"][sel] = g.vwap.values[i1[sel]]
            f["b_fv"][sel] = g.fv.values[i1[sel]]
            f["f_vwap"][sel] = g.vwap.values[i2[sel]]
            f["f_fv"][sel] = g.fv.values[i2[sel]]
            g2 = g.copy()
            for k, v in f.items():
                g2[k] = v
            tgt.append(g2)
        mm = pd.concat(tgt).dropna(subset=["f_vwap", "f_fv", "b_vwap", "b_fv"])
        if len(mm) < 50:
            continue
        mm = mm.copy()
        mm["dp"] = mm.f_vwap - mm.b_vwap
        mm["dfv"] = mm.f_fv - mm.b_fv
        for dep, name in [("dp", "price_to_fv"), ("dfv", "fv_to_price")]:
            x = (mm.gap if dep == "dp" else -mm.gap).values
            y = mm[dep].values
            X = np.column_stack([np.ones(len(x)), x])
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            resid = y - X @ beta
            # cluster-robust (event) SE for slope
            meat = np.zeros((2, 2))
            for _, gi in mm.assign(_r=resid).groupby(["asset", "event"]):
                Xi = np.column_stack([np.ones(len(gi)), (gi.gap if dep == "dp" else -gi.gap).values])
                ui = gi._r.values
                s = Xi.T @ ui
                meat += np.outer(s, s)
            bread = np.linalg.inv(X.T @ X)
            V = bread @ meat @ bread
            se = math.sqrt(V[1, 1])
            rows.append(dict(horizon_h=h_slots * 0.25, dep=name, n=len(mm),
                             beta=beta[1], se=se, t=beta[1] / se if se > 0 else np.nan))
        # gap magnitude decay: condition on slot-t gap (independent noise),
        # measure |gap| change from next-print slot t1 to slot t2
        mm["absgap0"] = (mm.b_fv - mm.b_vwap).abs()
        mm["absgap1"] = (mm.f_fv - mm.f_vwap).abs()
        big = mm[mm.gap.abs() > 0.025]
        if len(big) > 30:
            dd = big.absgap1 - big.absgap0
            t, ne = clus_t(big.assign(_d=dd), "_d")
            rows.append(dict(horizon_h=h_slots * 0.25, dep="absgap_change(|gap|>2.5c)",
                             n=len(big), beta=dd.mean(), se=np.nan, t=t))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    p = load()
    print(f"panel: {len(p)} slots, {p.slug.nunique()} markets, "
          f"{p.quarter.min()}..{p.quarter.max()}")
    print(p.groupby("quarter").agg(slots=("ts", "size"), mkts=("slug", "nunique"),
                                   medsz=("sz", "median")).to_string())
    cal = calibration(p)
    cal.to_csv(ME / "calibration.csv", index=False)
    print("\n=== (a) Brier: fill VWAP vs FV (diff>0 => FV better) ===")
    print(cal.round(4).to_string(index=False))

    bk = buckets(p)
    bk.to_csv(ME / "buckets.csv", index=False)
    print("\n=== (a2) high-prob YES buckets: is PM below FV, and who is right? ===")
    print(bk.round(4).to_string(index=False))

    p2 = next_print(p)
    sig, trades = signal_ev(p2)
    sig.to_csv(ME / "signal_ev.csv", index=False)
    if len(trades):
        trades.to_csv(ME / "signal_trades.csv", index=False)
    print("\n=== (b) incumbent-style signal, hedged EV per share (entry=next-print vwap) ===")
    print(sig.round(4).to_string(index=False))
    sig_b, _ = signal_ev(p2, entry_col="n_vwap_buy")
    sig_b.to_csv(ME / "signal_ev_buyvwap.csv", index=False)
    print("\n=== (b') same, entry = next-print taker-BUY vwap (conservative) ===")
    print(sig_b[sig_b["slice"] == "ALL"].round(4).to_string(index=False))

    parts = []
    for era, sel in [("ALL", p), ("2024", p[p.year == 2024]), ("2025H1", p[(p.year == 2025) & (p.exp_dt < pd.Timestamp("2025-07-01", tz="UTC"))]), ("2025H2", p[p.exp_dt >= pd.Timestamp("2025-07-01", tz="UTC")])]:
        c = convergence(sel)
        c["era"] = era
        parts.append(c)
    conv = pd.concat(parts, ignore_index=True)
    conv.to_csv(ME / "convergence.csv", index=False)
    print("\n=== (c) convergence (skip-slot: response measured from next print, "
          "beta = fraction of gap closed) ===")
    print(conv.round(4).to_string(index=False))
