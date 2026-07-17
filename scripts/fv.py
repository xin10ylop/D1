"""Options-implied fair value engine for Polymarket strike digitals.

Pipeline:
  1. load Deribit option trades per asset (iv, index_price embedded)
  2. per (expiry, 15-min grid): weighted robust quadratic smile fit sigma(x),
     x = ln(K/F), using trades in an adaptive lookback window
  3. forward = index * exp(carry(tau)) from dated-futures basis curve
  4. PM strike translated onto Deribit underlier via Binance/index ratio
  5. digital P(S_T > K) = N(d2) - phi(d2)*sqrt(tau)*dsigma/dx  (skew corrected)
  6. total-variance interpolation in tau between straddling Deribit expiries
"""
import re, glob, math, datetime as dt
import numpy as np
import pandas as pd
from scipy.stats import norm
from common import DATA

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def parse_expiry(tok):
    m = re.match(r"(\d{1,2})([A-Z]{3})(\d{2})", tok)
    d, mon, yy = int(m.group(1)), MONTHS[m.group(2)], 2000 + int(m.group(3))
    return int(dt.datetime(yy, mon, d, 8, tzinfo=dt.timezone.utc).timestamp())


def load_trades(asset):
    """Load all option trades for one underlying, normalized."""
    if asset in ("BTC", "ETH"):
        files = sorted(glob.glob(str(DATA / f"deribit_opt_trades/{asset}_*.parquet")))
        pre = asset + "-"
    else:
        files = sorted(glob.glob(str(DATA / "deribit_opt_trades/USDC_*.parquet")))
        pre = f"{asset}_USDC-"
    parts = []
    for f in files:
        try:
            d = pd.read_parquet(f, columns=["timestamp", "instrument_name", "price", "iv",
                                            "mark_price", "index_price", "direction", "amount",
                                            "contracts", "block_trade_id"])
        except Exception:
            continue
        if len(d) == 0:
            continue
        d = d[d["instrument_name"].str.startswith(pre)]
        if len(d):
            parts.append(d)
    tr = pd.concat(parts, ignore_index=True)
    toks = tr["instrument_name"].str.slice(len(pre)).str.split("-", expand=True)
    tr["exp_ts"] = toks[0].map({t: parse_expiry(t) for t in toks[0].unique()})
    tr["strike"] = pd.to_numeric(toks[1].str.replace("d", ".", regex=False), errors="coerce")
    tr["cp"] = toks[2]
    tr = tr[tr["strike"].notna() & tr["iv"].notna() & (tr["iv"] > 0.5) & (tr["iv"] < 500)]
    tr["ts_s"] = tr["timestamp"] / 1000.0
    return tr.sort_values("timestamp").reset_index(drop=True)


def index_series(tr, freq="1min"):
    """Median index price per minute from the trade tape, ffilled."""
    t = pd.to_datetime(tr["timestamp"], unit="ms", utc=True)
    s = tr.set_index(t)["index_price"].resample(freq).median()
    return s.ffill()


def carry_curve(asset):
    """Hourly annualized carry rate per dated future: ln(F/index)/tau."""
    if asset in ("BTC", "ETH"):
        pat = str(DATA / f"deribit_fut/bars1h_{asset}-*.parquet")
        perp_name = f"{asset}-PERPETUAL"
    else:
        pat = str(DATA / f"deribit_fut/bars1h_{asset}_USDC-*.parquet")
        perp_name = f"{asset}_USDC-PERPETUAL"
    rows = []
    for f in glob.glob(pat):
        inst = f.split("bars1h_")[-1].replace(".parquet", "")
        if "PERPETUAL" in inst:
            continue
        tok = inst.split("-")[1]
        try:
            exp_ts = parse_expiry(tok)
        except Exception:
            continue
        d = pd.read_parquet(f)
        d["exp_ts"] = exp_ts
        d["inst"] = inst
        rows.append(d[["ts_ms", "close", "exp_ts", "inst"]])
    if not rows:
        return None
    fut = pd.concat(rows, ignore_index=True)
    perp_f = DATA / f"deribit_fut/bars1h_{perp_name}.parquet"
    if not perp_f.exists():
        return None
    perp = pd.read_parquet(perp_f)[["ts_ms", "close"]]
    perp = perp.rename(columns={"close": "perp"})
    fut = fut.merge(perp, on="ts_ms", how="left")
    fut["tau"] = (fut["exp_ts"] - fut["ts_ms"] / 1000) / (365 * 86400)
    fut = fut[fut["tau"] > 2 / 365]
    fut["carry"] = np.log(fut["close"] / fut["perp"]) / fut["tau"]
    fut = fut[np.abs(fut["carry"]) < 0.5]
    return fut[["ts_ms", "exp_ts", "tau", "carry"]]


def carry_at(carry_df, ts_ms, tau_target):
    """Nearest-hour carry linearly interpolated in tau; 0 if unavailable."""
    if carry_df is None or len(carry_df) == 0:
        return 0.0
    h = int(ts_ms // 3600000 * 3600000)
    win = carry_df[(carry_df["ts_ms"] >= h - 7200000) & (carry_df["ts_ms"] <= h + 3600000)]
    if len(win) == 0:
        return 0.0
    win = win.sort_values("tau")
    return float(np.interp(tau_target, win["tau"], win["carry"]))


def fit_smiles(tr, grid_freq_s=900, max_tau_d=9.5, min_trades=8, min_strikes=4):
    """For each (expiry, grid point) fit sigma(x) = a + b x + c x^2 (robust, weighted).

    Returns DataFrame [exp_ts, grid_ts, S, a, b, c, n, nk, resid].
    """
    out = []
    for exp_ts, g in tr.groupby("exp_ts"):
        g = g.sort_values("timestamp")
        ts = g["timestamp"].values / 1000.0
        iv = g["iv"].values / 100.0
        K = g["strike"].values
        S = g["index_price"].values
        t0 = max(ts[0], exp_ts - max_tau_d * 86400)
        grid = np.arange(math.ceil(t0 / grid_freq_s) * grid_freq_s, exp_ts, grid_freq_s)
        if len(grid) == 0:
            continue
        for gt in grid:
            tau = (exp_ts - gt) / (365 * 86400)
            look = min(6 * 3600, max(1800, (exp_ts - gt) / 6))
            lo, hi = np.searchsorted(ts, [gt - look, gt])
            if hi - lo < min_trades:
                continue
            i = slice(lo, hi)
            x = np.log(K[i] / S[i])  # moneyness vs index (carry folded into fit)
            keep = np.abs(x) < max(0.25, 3 * np.nanmedian(iv[i]) * math.sqrt(tau))
            if keep.sum() < min_trades or len(np.unique(K[i][keep])) < min_strikes:
                continue
            xx, yy = x[keep], iv[i][keep]
            age = gt - ts[i][keep]
            w = np.exp(-age / (look / 2.0))
            xsc = max(float(np.std(xx)), 1e-4)
            u = xx / xsc
            X = np.column_stack([np.ones_like(u), u, u * u])
            lam = 0.05 * float(np.sum(w))  # ridge on slope+curvature in scaled coords
            R = np.diag([0.0, math.sqrt(lam) * 0.1, math.sqrt(lam)])
            try:
                A = np.vstack([X * w[:, None], R])
                yb = np.concatenate([yy * w, np.zeros(3)])
                beta = np.linalg.lstsq(A, yb, rcond=None)[0]
                r = yy - X @ beta
                mad = np.median(np.abs(r)) + 1e-6
                w2 = w / (1 + (r / (3 * mad)) ** 2)
                A = np.vstack([X * w2[:, None], R])
                yb = np.concatenate([yy * w2, np.zeros(3)])
                beta = np.linalg.lstsq(A, yb, rcond=None)[0]
                resid = float(np.sqrt(np.average((yy - X @ beta) ** 2, weights=w2)))
            except Exception:
                continue
            xlo, xhi = float(np.quantile(xx, 0.03)), float(np.quantile(xx, 0.97))
            out.append((exp_ts, gt, float(np.median(S[i][keep])), beta[0], beta[1] / xsc,
                        beta[2] / (xsc * xsc), int(keep.sum()),
                        int(len(np.unique(K[i][keep]))), resid, xlo, xhi))
    return pd.DataFrame(out, columns=["exp_ts", "grid_ts", "S", "a", "b", "c", "n", "nk",
                                      "resid", "xlo", "xhi"])


def smile_sigma(row, x):
    """Evaluate fitted smile; flat extrapolation outside the fitted strike range."""
    a, b, c = row["a"], row["b"], row["c"]
    xl = np.clip(x, row.get("xlo", -0.35), row.get("xhi", 0.35))
    sig = a + b * xl + c * xl * xl
    return np.clip(sig, 0.01, 5.0)


def digital_above(F, K, tau, sig, slope):
    """P(S_T > K) with skew correction; slope = dsigma/dx at K."""
    if tau <= 0:
        return float(F > K)
    v = sig * math.sqrt(tau)
    d2 = (math.log(F / K)) / v - v / 2
    return float(np.clip(norm.cdf(d2) - norm.pdf(d2) * math.sqrt(tau) * slope, 0.0, 1.0))
