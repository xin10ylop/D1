"""FV upgrade research: seasonal vol-time, RV blend, Student-t tails, mark-IV smiles.

Recomputes FV for existing panel rows from smiles_{asset}.parquet without a full
panel rebuild. Baseline mode reproduces the incumbent build_panel2 FV.
All tuning on train (expiry < 2026-05-16).
"""
import sys, math, pathlib
import numpy as np
import pandas as pd
from scipy.stats import norm, t as tdist

ROOT = pathlib.Path("/home/user/D1")
DATA = ROOT / "data"
OUT = ROOT / "results/improve/fv-upgrade"
sys.path.insert(0, str(ROOT / "scripts"))

YR = 365 * 86400.0
SPLIT = pd.Timestamp("2026-05-16", tz="UTC")
SPLIT_S = int(SPLIT.timestamp())
SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}


# ---------------------------------------------------------------- vol profile
def hourly_profile(asset, train_only=True, by_week=True):
    """Mean squared 1m log-return per hour-of-week (or hour-of-day), normalized to mean 1."""
    kl = pd.read_parquet(DATA / f"binance/{SYM[asset]}_1m.parquet")
    ts = kl["open_time_us"].values / 1e6
    if train_only:
        kl = kl[ts < SPLIT_S]
        ts = ts[ts < SPLIT_S]
    r = np.diff(np.log(kl["close"].values))
    tsr = ts[1:]
    # winsorize squared returns at 99.9th pct to keep single prints from owning an hour
    r2 = r * r
    cap = np.quantile(r2, 0.999)
    r2 = np.minimum(r2, cap)
    dtidx = pd.to_datetime(tsr, unit="s", utc=True)
    if by_week:
        key = dtidx.dayofweek * 24 + dtidx.hour
        nkey = 168
    else:
        key = dtidx.hour
        nkey = 24
    prof = pd.Series(r2).groupby(np.asarray(key)).mean()
    prof = prof.reindex(range(nkey)).ffill()
    prof = prof / prof.mean()
    return prof.values  # weight per hour slot, mean 1


def build_voltime(profile, t0, t1, by_week=True):
    """Cumulative seasonal vol-time at hourly breakpoints covering [t0, t1].

    Returns (hour_ts, cumV) for np.interp: V(t2)-V(t1) = seasonal variance-time
    in *seconds-equivalent* units (mean weight 1 => equals clock time on average).
    """
    h0 = int(t0 // 3600) * 3600 - 86400 * 8
    h1 = int(t1 // 3600) * 3600 + 86400 * 2
    hours = np.arange(h0, h1 + 3600, 3600)
    dtidx = pd.to_datetime(hours, unit="s", utc=True)
    if by_week:
        key = (dtidx.dayofweek * 24 + dtidx.hour).values
    else:
        key = dtidx.hour.values
    w = profile[key]
    cum = np.concatenate([[0.0], np.cumsum(w * 3600.0)])
    bp = np.concatenate([hours, [hours[-1] + 3600]])
    return bp.astype(float), cum


# ---------------------------------------------------------------- EWMA RV
def ewma_rv(asset, halflife_min):
    """Annualized EWMA realized vol from binance 1m closes; returns Series on ts(sec)."""
    kl = pd.read_parquet(DATA / f"binance/{SYM[asset]}_1m.parquet")
    ts = (kl["open_time_us"].values / 1e6 + 60)  # close time
    r = np.diff(np.log(kl["close"].values))
    r2 = r * r
    cap = np.quantile(r2, 0.9999)
    r2 = np.minimum(r2, cap)
    lam = math.exp(-math.log(2) / halflife_min)
    var = pd.Series(r2).ewm(alpha=1 - lam, adjust=False).mean().values
    ann = np.sqrt(var * 365 * 1440)
    return pd.Series(ann, index=ts[1:].astype("int64"))


# ---------------------------------------------------------------- FV recompute
def load_smiles(asset):
    sm = pd.read_parquet(DATA / f"smiles_{asset}.parquet")
    sm = sm[(sm["n"] >= 8) & (sm["nk"] >= 4)].copy()
    return sm


def attach_smiles(p, sm):
    """merge_asof the straddling smile rows (above/forward, below/backward) per grid_ts."""
    sm = sm.rename(columns={"grid_ts": "ts"})[
        ["ts", "exp_ts", "a", "b", "c", "xlo", "xhi"]].copy()
    sm["ts"] = sm["ts"].astype("int64")
    sm["exp_ts"] = sm["exp_ts"].astype("int64")
    p = p.copy()
    p["_row"] = np.arange(len(p))
    ps = p.sort_values("T_pm", kind="mergesort")
    sms = sm.sort_values("exp_ts", kind="mergesort")
    ab = pd.merge_asof(ps, sms, left_on="T_pm", right_on="exp_ts", by="ts",
                       direction="forward", suffixes=("", "_A"))
    ab = ab.rename(columns={"exp_ts": "expA", "a": "aA", "b": "bA", "c": "cA",
                            "xlo": "xloA", "xhi": "xhiA"})
    be = pd.merge_asof(ps[["_row", "ts", "T_pm"]], sms, left_on="T_pm", right_on="exp_ts",
                       by="ts", direction="backward", allow_exact_matches=False)
    be = be.rename(columns={"exp_ts": "expB", "a": "aB", "b": "bB", "c": "cB",
                            "xlo": "xloB", "xhi": "xhiB"})
    m = ab.merge(be.drop(columns=["ts", "T_pm"]), on="_row")
    m = m.sort_values("_row").reset_index(drop=True)
    return m


def _sig(a, b, c, xlo, xhi, x):
    xl = np.clip(x, xlo, xhi)
    return np.clip(a + b * xl + c * xl * xl, 0.01, 5.0)


def recompute_fv(m, voltime=None, rv=None, w_iv=None, tdf=None):
    """FV on attached frame m.

    voltime: (bp, cum) from build_voltime -> seasonal variance-time; None = clock time.
    rv: Series of annualized EWMA vol indexed by second-ts; w_iv: dict tte_band->w or scalar.
    tdf: Student-t df for the terminal distribution (None = normal).
    Returns (fv, sigma) arrays aligned to m.
    """
    ts = m["ts"].values.astype(float)
    T = m["T_pm"].values.astype(float)
    x = m["x"].values
    tau = (T - ts) / YR
    hasA = m["expA"].notna().values
    hasB = m["expB"].notna().values

    def V(t):
        return np.interp(t, voltime[0], voltime[1])

    n = len(m)
    w = np.full(n, np.nan)
    w2 = np.full(n, np.nan)

    # interp rows
    msk = hasA & hasB
    if msk.any():
        expA = m["expA"].values[msk].astype(float)
        expB = m["expB"].values[msk].astype(float)
        tA = (expA - ts[msk]) / YR
        tB = (expB - ts[msk]) / YR
        xg = x[msk]
        sA = _sig(m["aA"].values[msk], m["bA"].values[msk], m["cA"].values[msk],
                  m["xloA"].values[msk], m["xhiA"].values[msk], xg)
        sB = _sig(m["aB"].values[msk], m["bB"].values[msk], m["cB"].values[msk],
                  m["xloB"].values[msk], m["xhiB"].values[msk], xg)
        sA2 = _sig(m["aA"].values[msk], m["bA"].values[msk], m["cA"].values[msk],
                   m["xloA"].values[msk], m["xhiA"].values[msk], xg + 0.01)
        sB2 = _sig(m["aB"].values[msk], m["bB"].values[msk], m["cB"].values[msk],
                   m["xloB"].values[msk], m["xhiB"].values[msk], xg + 0.01)
        wA, wB = sA * sA * tA, sB * sB * tB
        wA2, wB2 = sA2 * sA2 * tA, sB2 * sB2 * tB
        if voltime is None:
            frac = (tau[msk] - tB) / np.maximum(tA - tB, 1e-9)
        else:
            frac = (V(T[msk]) - V(expB)) / np.maximum(V(expA) - V(expB), 1e-9)
        w[msk] = wB + (wA - wB) * frac
        w2[msk] = wB2 + (wA2 - wB2) * frac

    # extrap rows (only expiry above)
    msk = hasA & ~hasB
    if msk.any():
        expA = m["expA"].values[msk].astype(float)
        tA = (expA - ts[msk]) / YR
        xg = x[msk]
        sA = _sig(m["aA"].values[msk], m["bA"].values[msk], m["cA"].values[msk],
                  m["xloA"].values[msk], m["xhiA"].values[msk], xg)
        sA2 = _sig(m["aA"].values[msk], m["bA"].values[msk], m["cA"].values[msk],
                   m["xloA"].values[msk], m["xhiA"].values[msk], xg + 0.01)
        if voltime is None:
            ratio = tau[msk] / tA
        else:
            ratio = (V(T[msk]) - V(ts[msk])) / np.maximum(V(expA) - V(ts[msk]), 1e-12)
        w[msk] = sA * sA * tA * ratio
        w2[msk] = sA2 * sA2 * tA * ratio

    sig = np.sqrt(np.maximum(w, 1e-10) / np.maximum(tau, 1e-12))
    sig2 = np.sqrt(np.maximum(w2, 1e-10) / np.maximum(tau, 1e-12))
    slope = (sig2 - sig) / 0.01

    # RV blend on sigma
    if rv is not None and w_iv is not None:
        rvv = np.interp(ts, rv.index.values.astype(float), rv.values)
        if np.isscalar(w_iv):
            wv = np.full(n, float(w_iv))
        else:
            wv = np.ones(n)
            tte = tau * 365
            for (lo, hi), val in w_iv.items():
                wv[(tte >= lo) & (tte < hi)] = val
        sig = wv * sig + (1 - wv) * rvv
        slope = slope * wv

    v = sig * np.sqrt(tau)
    d2 = -x / v - v / 2
    if tdf is None:
        dig = norm.cdf(d2) - norm.pdf(d2) * np.sqrt(tau) * slope
    else:
        c = math.sqrt(tdf / (tdf - 2.0))
        dig = tdist.cdf(d2 * c, tdf) - tdist.pdf(d2 * c, tdf) * c * np.sqrt(tau) * slope
    fv = np.clip(dig, 0.0, 1.0)
    fv[~hasA] = np.nan
    return fv, sig


# ---------------------------------------------------------------- metrics
def tte_band(tte):
    return pd.cut(tte, [0, 0.333, 1, 3, 8, 100],
                  labels=["0-8h", "8h-1d", "1-3d", "3-8d", "8d+"])


def calib_table(df, fvcol, label="label"):
    d = df.dropna(subset=[fvcol]).copy()
    p = d[fvcol].clip(1e-4, 1 - 1e-4)
    y = d[label].values
    out = {"n": len(d), "brier": float(np.mean((p - y) ** 2)),
           "logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))}
    return out


def calib_by(df, fvcol, bycol):
    rows = []
    for k, g in df.dropna(subset=[fvcol]).groupby(bycol, observed=True):
        r = calib_table(g, fvcol)
        r[str(bycol)] = k
        p = g[fvcol]
        r["mean_fv"] = float(p.mean())
        r["mean_label"] = float(g["label"].mean())
        rows.append(r)
    return pd.DataFrame(rows)
