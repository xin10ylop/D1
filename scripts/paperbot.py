"""Paper-trading bot — identical to the live deployment except fills are simulated.

Strategy (frozen from backtest, results/REPORT.md):
  S1 taker : buy YES at ask when FV - ask > 0.05 and 3d <= tte < 8d
  S2 maker : rest YES bid at best_bid + 0.01 when FV - (bid+0.01) > 0.025, tte < 8d
  Assets   : BTC, ETH daily ladders (SOL/XRP excluded per OOS)
  Sizing   : f_pos = 10% of bankroll, clip $100 taker / $60 maker, min $3
  Hedge    : short perp, delta_$ = phi(d2)/(sig*sqrt(tau)) per share (paper),
             capital per share = entry + delta/10 (10x margin)
  Fees     : taker 0.07*p*(1-p); maker 0. Resolution redemption free.
  Exit     : hold to resolution (Binance 1m candle labeled 12:00 ET, close > K strictly)
  Dedup    : one position per market per 24h.

Live data (all public, no keys):
  Deribit  : option trades (last 6h) -> same smile fit as backtest (fv.fit_smiles),
             futures summary -> carry, index from trades
  Polymarket: gamma-api events (daily ladders), CLOB order books per YES token
  Binance  : data-api.binance.vision spot price + 1m klines for resolution

State: results/paperbot/state.json (bankroll, positions), trades.csv, journal.log
Run: python3 scripts/paperbot.py --once   (single scan)
     python3 scripts/paperbot.py          (loop every 10 min)
"""
import sys, os, json, time, math, re, pathlib, datetime as dt
import urllib.request, urllib.parse
import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fv as fvlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PB = ROOT / "results" / "paperbot"
PB.mkdir(parents=True, exist_ok=True)
STATE_F = PB / "state.json"
TRADES_F = PB / "trades.csv"
JOURNAL = PB / "journal.log"

ASSETS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
WORD = {"BTC": "bitcoin", "ETH": "ethereum"}
BANKROLL0 = 100.0
F_POS = 0.10
CLIP = {"taker": 100.0, "maker": 60.0}
THR = {"taker": 0.05, "maker": 0.025}
TTE_TAKER = (3.0, 8.0)
MAX_DEPLOY = 0.85
MIN_POS = 3.0
FEE_RATE = 0.07
COOLDOWN_H = 24


def log(msg):
    line = f"{dt.datetime.now(dt.timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with open(JOURNAL, "a") as f:
        f.write(line + "\n")


def http_json(url, timeout=45, retries=4):
    for a in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "paperbot/1.0"}), timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            if a == retries - 1:
                log(f"HTTP FAIL {url[:90]} {e}")
                return None
            time.sleep(1.5 ** a)


# ---------- live fair value (same code path as backtest) ----------

def live_smiles(asset, hours=6):
    """Fetch recent Deribit option trades, run the SAME smile fit as the backtest,
    keep the latest grid point per expiry."""
    now_ms = int(time.time() * 1000)
    rows, end = [], now_ms
    start = now_ms - hours * 3600 * 1000
    while True:
        r = http_json("https://www.deribit.com/api/v2/public/get_last_trades_by_currency_and_time?"
                      + urllib.parse.urlencode({"currency": asset, "kind": "option",
                                                "start_timestamp": start, "end_timestamp": end,
                                                "count": 1000, "sorting": "desc"}))
        if not r or "result" not in r:
            break
        tr = r["result"]["trades"]
        rows.extend(tr)
        if not r["result"].get("has_more") or not tr:
            break
        end = min(t["timestamp"] for t in tr) - 1
    if not rows:
        return None, None
    tr = pd.DataFrame(rows).drop_duplicates("trade_id")
    pre = asset + "-"
    tr = tr[tr["instrument_name"].str.startswith(pre)]
    toks = tr["instrument_name"].str.slice(len(pre)).str.split("-", expand=True)
    tr["exp_ts"] = toks[0].map({t: fvlib.parse_expiry(t) for t in toks[0].unique()})
    tr["strike"] = pd.to_numeric(toks[1], errors="coerce")
    tr = tr[tr["strike"].notna() & tr["iv"].notna() & (tr["iv"] > 0.5) & (tr["iv"] < 500)]
    tr = tr.sort_values("timestamp").reset_index(drop=True)
    S_idx = float(tr["index_price"].tail(50).median())
    sm = fvlib.fit_smiles(tr, grid_freq_s=900)
    if len(sm) == 0:
        return None, S_idx
    sm = sm.sort_values("grid_ts").groupby("exp_ts").last().reset_index()
    return sm, S_idx


def live_carry(asset):
    r = http_json(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={asset}&kind=future")
    if not r or "result" not in r:
        return []
    now = time.time()
    out = []
    perp = next((x for x in r["result"] if "PERPETUAL" in x["instrument_name"]), None)
    if perp is None or not perp.get("mark_price"):
        return []
    for x in r["result"]:
        if "PERPETUAL" in x["instrument_name"] or not x.get("mark_price"):
            continue
        tok = x["instrument_name"].split("-")[1]
        try:
            exp = fvlib.parse_expiry(tok)
        except Exception:
            continue
        tau = (exp - now) / (365 * 86400)
        if tau > 2 / 365:
            out.append((tau, math.log(x["mark_price"] / perp["mark_price"]) / tau))
    return sorted(out)


def binance_spot(sym):
    r = http_json(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}")
    return float(r["price"]) if r else None


def binance_close_at(sym, t_open_utc):
    """Close of the 1m candle opening at t_open_utc (unix seconds)."""
    r = http_json(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval=1m"
                  f"&startTime={int(t_open_utc*1000)}&limit=1")
    if r and len(r) and int(r[0][0]) == int(t_open_utc * 1000):
        return float(r[0][4])
    return None


_VOLPROF = None


def vol_profile(asset):
    """168-vector hour-of-week variance multipliers (train-estimated).
    Falls back to flat (== clock time) if the CSV is absent."""
    global _VOLPROF
    if _VOLPROF is None:
        f = pathlib.Path(__file__).resolve().parent / "vol_profile_hourweek.csv"
        try:
            df = pd.read_csv(f)
            _VOLPROF = {a: df[a].values.astype(float) for a in df.columns if a != "hour_of_week"}
        except Exception:
            _VOLPROF = {}
    return _VOLPROF.get(asset, np.ones(168))


def cumvar_fn(asset, t0, t1):
    """Cumulative seasonal variance-time V(t) on a 10-min grid over [t0, t1]."""
    prof = vol_profile(asset)
    grid = np.arange(t0 - 600, t1 + 3600, 600.0)
    # hour-of-week index 0 = Monday 00:00 UTC (unix epoch was Thursday -> +72h)
    how = (((grid / 3600.0) + 72) % 168).astype(int)
    dV = prof[how] * (600.0 / (365 * 86400))
    V = np.cumsum(dV)
    return lambda t: float(np.interp(t, grid, V))


def fv_digital(smiles, S_idx, S_bin, carry, K, T_pm, asset="BTC"):
    now = time.time()
    tau = (T_pm - now) / (365 * 86400)
    if tau <= 0 or smiles is None or S_idx is None or S_bin is None:
        return None
    K_eff = K * S_idx / S_bin
    cr = float(np.interp(tau, [c[0] for c in carry], [c[1] for c in carry])) if carry else 0.0
    F = S_idx * math.exp(cr * tau)
    x = math.log(K_eff / F)
    sm = smiles[(smiles.exp_ts > now)].sort_values("exp_ts")
    later = sm[sm.exp_ts >= T_pm]
    earlier = sm[sm.exp_ts < T_pm]

    def sig_at(row, xx):
        xl = min(max(xx, row.xlo), row.xhi)
        return float(np.clip(row.a + row.b * xl + row.c * xl * xl, 0.01, 5.0))

    if len(earlier) and len(later):
        rB, rA = earlier.iloc[-1], later.iloc[0]
        tB, tA = (rB.exp_ts - now) / (365 * 86400), (rA.exp_ts - now) / (365 * 86400)
        V = cumvar_fn(asset, now, float(rA.exp_ts))
        frac = (V(T_pm) - V(rB.exp_ts)) / max(V(rA.exp_ts) - V(rB.exp_ts), 1e-12)
        frac = min(max(frac, 0.0), 1.0)
        w = sig_at(rB, x) ** 2 * tB + (sig_at(rA, x) ** 2 * tA - sig_at(rB, x) ** 2 * tB) * frac
        w2 = sig_at(rB, x + .01) ** 2 * tB + (sig_at(rA, x + .01) ** 2 * tA - sig_at(rB, x + .01) ** 2 * tB) * frac
        extrap = 0
    elif len(later):
        rA = later.iloc[0]
        tA = (rA.exp_ts - now) / (365 * 86400)
        V = cumvar_fn(asset, now, float(rA.exp_ts))
        ratio = (V(T_pm) - V(now)) / max(V(rA.exp_ts) - V(now), 1e-12)
        ratio = min(max(ratio, 0.0), 1.0)
        w = sig_at(rA, x) ** 2 * tA * ratio
        w2 = sig_at(rA, x + .01) ** 2 * tA * ratio
        extrap = 1
    else:
        return None
    sig = math.sqrt(max(w, 1e-10) / tau)
    sig2 = math.sqrt(max(w2, 1e-10) / tau)
    slope = (sig2 - sig) / 0.01
    v = sig * math.sqrt(tau)
    d2 = math.log(F / K_eff) / v - v / 2
    p = float(np.clip(norm.cdf(d2) - norm.pdf(d2) * math.sqrt(tau) * slope, 0, 1))
    delta = float(norm.pdf(d2) / v)  # $ per share per unit log-return
    return dict(fv=p, sigma=sig, delta=delta, tau_d=tau * 365, extrap=extrap)


# ---------- Polymarket live markets & books ----------

def pm_daily_ladders():
    """Active daily 'above' markets for BTC/ETH — events fetched by constructed slug
    (the daily ladder slug pattern is deterministic)."""
    out = []
    today = dt.datetime.now(dt.timezone.utc).date()
    for asset, word in WORD.items():
        for d in range(0, 9):
            day = today + dt.timedelta(days=d)
            base = f"{word}-above-on-{day.strftime('%B').lower()}-{day.day}"
            for slug_ev in (f"{base}-{day.year}", base):
                r = http_json(f"https://gamma-api.polymarket.com/events?slug={slug_ev}")
                if not r or not len(r) or not r[0].get("markets"):
                    continue
                for mk in r[0]["markets"]:
                    if mk.get("closed"):
                        continue
                    q = mk.get("question", "")
                    sm = re.search(r"\$([\d,]+(?:\.\d+)?)", q)
                    if not sm:
                        continue
                    try:
                        toks = json.loads(mk.get("clobTokenIds", "[]"))
                    except Exception:
                        toks = []
                    if len(toks) != 2:
                        continue
                    end = mk.get("endDate")
                    if not end:
                        continue
                    end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
                    T_pm = end_dt.timestamp() + 60
                    # AUDIT FIX (DST guard): resolution is defined as the 12:00 ET candle.
                    # Trust endDate only if it IS 12:00 ET; otherwise the settlement candle
                    # lookup and tau are silently wrong (e.g. across the Nov 2026 DST shift).
                    try:
                        from zoneinfo import ZoneInfo
                        et = end_dt.astimezone(ZoneInfo("America/New_York"))
                        if (et.hour, et.minute, et.second) != (12, 0, 0):
                            log(f"SKIP {mk.get('slug','')} endDate {end} is not 12:00 ET")
                            continue
                    except Exception:
                        pass
                    out.append(dict(asset=asset, slug=mk.get("slug", ""), question=q,
                                    K=float(sm.group(1).replace(",", "")),
                                    T_pm=T_pm, yes_token=toks[0],
                                    condition_id=mk.get("conditionId", "")))
                break
    return out


def clob_book(token_id):
    r = http_json(f"https://clob.polymarket.com/book?token_id={token_id}")
    if not r:
        return None
    bids = sorted([(float(x["price"]), float(x["size"])) for x in r.get("bids", [])], reverse=True)
    asks = sorted([(float(x["price"]), float(x["size"])) for x in r.get("asks", [])])
    if not bids or not asks:
        return None
    return dict(bid=bids[0][0], bid_sz=bids[0][1], ask=asks[0][0], ask_sz=asks[0][1], asks=asks)


# ---------- state ----------

def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return dict(bankroll=BANKROLL0, positions=[], last_entry={}, created=dt.datetime.now(dt.timezone.utc).isoformat())


def save_state(st):
    STATE_F.write_text(json.dumps(st, indent=1))


def append_trade(row):
    df = pd.DataFrame([row])
    if TRADES_F.exists():
        df = pd.concat([pd.read_csv(TRADES_F), df], ignore_index=True)
    df.to_csv(TRADES_F, index=False)


# ---------- core cycle ----------

def resolve_positions(st):
    now = time.time()
    still = []
    for p in st["positions"]:
        if now < p["T_pm"] + 120:  # candle close + buffer
            still.append(p)
            continue
        if p["mode"] in ("maker_pending", "am_pending"):
            # last chance: did it actually trade through our limit before expiry
            # (covers fills that happened while the bot was down)?
            life = 1800 if p["mode"] == "am_pending" else 4 * 3600
            low = traded_low_since(p.get("condition_id", ""), p.get("last_fill_check", p["placed_ts"]),
                                   end_ts=min(p["placed_ts"] + life, p["T_pm"]))
            if low is not None and low <= p["entry"] + 1e-9:
                p["mode"] = "maker" if p["mode"] == "maker_pending" else "taker"
                log(f"PENDING FILLED (retro, pre-expiry) {p['slug']} @ {p['entry']}")
            else:
                # am_pending would have taker-fallen-back at an unknowable past ask:
                # conservative = cancel, never fabricate a fallback fill
                log(f"PENDING CANCELLED AT EXPIRY (unfilled) {p['slug']}")
                continue
        sym = ASSETS[p["asset"]]
        S_T = binance_close_at(sym, p["T_pm"] - 60)  # candle labeled 12:00 ET opens at T_pm-60
        if S_T is None:
            # AUDIT FIX: a permanently unfetchable settlement candle would lock this
            # position's capital forever with no signal. Alert loudly once it is stale.
            if now > p["T_pm"] + 6 * 3600:
                log(f"ERROR STUCK POSITION {p['slug']} unresolved {int((now-p['T_pm'])/3600)}h "
                    f"past T_pm — settlement candle at {int(p['T_pm']-60)} not found; "
                    f"check endDate/DST alignment")
            still.append(p)
            continue
        won = S_T > p["K"]
        pm_pnl = (1.0 - p["entry"] if won else -p["entry"]) * p["shares"] - p["fee_paid"]
        # AUDIT FIX: a short perp with $notional = delta*shares earns -notional*(S_T/S0 - 1).
        # The former log(S_T/S0) form overstated hedge P&L by delta*(e^r - 1 - r) >= 0
        # (~+1c/share on the incumbent trade mix).
        hedge_pnl = -p["delta"] * (S_T / p["S_entry"] - 1.0) * p["shares"]
        pnl = pm_pnl + hedge_pnl
        st["bankroll"] += pnl
        append_trade(dict(closed=dt.datetime.now(dt.timezone.utc).isoformat(), **{k: p[k] for k in
                          ("asset", "slug", "K", "mode", "entry", "shares", "T_pm")},
                          S_T=S_T, won=int(won), pm_pnl=round(pm_pnl, 3),
                          hedge_pnl=round(hedge_pnl, 3), pnl=round(pnl, 3),
                          bankroll=round(st["bankroll"], 2)))
        log(f"RESOLVED {p['slug']} won={won} pnl=${pnl:+.2f} bankroll=${st['bankroll']:.2f}")
    st["positions"] = still


def traded_low_since(condition_id, since_ts, end_ts=None):
    """AUDIT FIX: lowest ACTUAL YES sell-print in [since_ts, end_ts] from the data-api
    trade tape. The previous implementation used clob prices-history, which returns the
    MIDPOINT series (verified live: p == (bid+ask)/2), so a resting bid was credited a
    "fill" whenever the mid dipped -- phantom fills with no trade. Only an aggressive
    SELL print on the YES token at/below our limit can actually fill a resting YES bid.
    Returns None if the tape is unavailable (caller must then NOT assume a fill)."""
    e = int(end_ts if end_ts else time.time())
    lows, offset = [], 0
    while offset <= 2000:
        r = http_json(f"https://data-api.polymarket.com/trades?market={condition_id}"
                      f"&limit=500&offset={offset}")
        if not isinstance(r, list):
            return None if not lows else min(lows)
        if not r:
            break
        for t in r:
            try:
                ts = float(t.get("timestamp", 0))
                if ts > e:
                    continue
                if t.get("outcome") != "Yes" or str(t.get("side", "")).upper() != "SELL":
                    continue
                if ts >= since_ts:
                    lows.append(float(t["price"]))
            except Exception:
                continue
        if float(r[-1].get("timestamp", 0)) < since_ts:
            break
        offset += 500
    return min(lows) if lows else None


def check_maker_fills(st, books):
    """Pending-order state machine.

    maker_pending (S2, bid+1c): fill if best ask <= limit OR the token traded
    at/below the limit since placement (tape truth); expire unfilled at 4h.
    am_pending (S1 aggressive maker, ask-1c): same fill rule; if unfilled at
    30m, FALL BACK TO TAKER at the live ask (adopted execution policy)."""
    for p in st["positions"]:
        if p["mode"] not in ("maker_pending", "am_pending"):
            continue
        b = books.get(p["yes_token"])
        if b is None:
            b = clob_book(p["yes_token"])  # pending markets always get a book check
        filled = bool(b and b["ask"] <= p["entry"] + 1e-9)
        if not filled:
            # AUDIT FIX: cap the tape window at the order's life — after an outage,
            # prints later than (placement + life) could not have filled an order
            # that would already have been cancelled.
            life = 1800 if p["mode"] == "am_pending" else 4 * 3600
            low = traded_low_since(p.get("condition_id", ""),
                                   p.get("last_fill_check", p["placed_ts"]),
                                   end_ts=min(time.time(), p["placed_ts"] + life))
            filled = low is not None and low <= p["entry"] + 1e-9
        p["last_fill_check"] = time.time()
        if filled:
            if p["mode"] == "maker_pending":
                p["mode"], p["exec"] = "maker", "maker_fill"
                log(f"MAKER FILLED {p['slug']} @ {p['entry']}")
            else:
                p["mode"], p["exec"] = "taker", "am_fill"  # S1 book, maker-priced fill, no fee
                log(f"AM FILLED {p['slug']} @ {p['entry']}")
            continue
        if p["mode"] == "am_pending" and time.time() > p["placed_ts"] + 1800:
            # taker fallback: cross at live ask, pay the fee
            if b and b.get("ask"):
                old = p["entry"]
                p["entry"] = float(b["ask"])
                p["shares"] = round(min(p["shares"] * old / max(p["entry"], 0.02), p["shares"]), 2)
                p["fee_paid"] = round(FEE_RATE * p["entry"] * (1 - p["entry"]) * p["shares"], 4)
                p["capital"] = round(p["shares"] * (p["entry"] + p["delta"] / 10), 2)
                p["mode"] = "taker"
                p["exec"] = "taker_fallback"
                log(f"AM FALLBACK->TAKER {p['slug']} @ {p['entry']:.3f} (limit was {old:.3f})")
            else:
                p["mode"] = "maker_expired"  # no book: drop rather than guess
                log(f"AM EXPIRED (no book for fallback) {p['slug']}")
            continue
        if p["mode"] == "maker_pending" and time.time() > p["placed_ts"] + 4 * 3600:
            p["mode"] = "maker_expired"
            log(f"MAKER EXPIRED {p['slug']}")
    st["positions"] = [p for p in st["positions"] if p["mode"] != "maker_expired"]


def deployed_capital(st):
    return sum(p["capital"] for p in st["positions"]
               if p["mode"] in ("taker", "maker", "maker_pending", "am_pending"))


def scan_once():
    st = load_state()
    resolve_positions(st)

    mkts = pm_daily_ladders()
    log(f"scan: {len(mkts)} live daily-ladder strikes")
    fv_ctx = {}
    for a in ASSETS:
        sm, S_idx = live_smiles(a)
        fv_ctx[a] = dict(smiles=sm, S_idx=S_idx, carry=live_carry(a), S_bin=binance_spot(ASSETS[a]))
        ok = sm is not None and fv_ctx[a]["S_bin"] is not None
        log(f"  {a}: smiles={'ok' if sm is not None else 'FAIL'} expiries={0 if sm is None else len(sm)} "
            f"S_idx={S_idx} S_bin={fv_ctx[a]['S_bin']}")
        if not ok:
            log(f"ERROR {a} market-data fetch failed this cycle "
                f"(smiles={sm is not None}, spot={fv_ctx[a]['S_bin'] is not None}) — skipping asset")
        elif S_idx and fv_ctx[a]["S_bin"] and abs(S_idx / fv_ctx[a]["S_bin"] - 1) > 0.02:
            log(f"MISMATCH {a} Deribit index vs Binance spot differ >2% "
                f"({S_idx} vs {fv_ctx[a]['S_bin']}) — basis check")
    if all(fv_ctx[a]["smiles"] is None or fv_ctx[a]["S_bin"] is None for a in ASSETS):
        log("HALT no usable market data for any asset this cycle — no entries made")

    books = {}
    signals = []
    now = time.time()
    for mk in mkts:
        ctx = fv_ctx[mk["asset"]]
        if ctx["smiles"] is None or ctx["S_bin"] is None:
            continue
        tte_d = (mk["T_pm"] - now) / 86400
        if tte_d <= 0 or tte_d >= 8:
            continue
        f = fv_digital(ctx["smiles"], ctx["S_idx"], ctx["S_bin"], ctx["carry"], mk["K"], mk["T_pm"],
                       asset=mk["asset"])
        if f is None:
            continue
        b = clob_book(mk["yes_token"])
        if b is None or (b["ask"] - b["bid"]) > 0.05 or b["ask"] <= 0.02 or b["ask"] >= 0.98:
            continue
        books[mk["yes_token"]] = b
        gap_t = f["fv"] - b["ask"]
        gap_m = f["fv"] - (b["bid"] + 0.01)
        signals.append(dict(**mk, **f, **{k: b[k] for k in ("bid", "ask", "bid_sz", "ask_sz")},
                            gap_taker=gap_t, gap_maker=gap_m, tte_d=tte_d, asks=b["asks"]))

    check_maker_fills(st, books)

    open_slugs = {(p["slug"]) for p in st["positions"]}
    n_entered = 0
    for s in sorted(signals, key=lambda x: -max(x["gap_taker"], 0)):
        last = st["last_entry"].get(s["slug"], 0)
        if s["slug"] in open_slugs or now - last < COOLDOWN_H * 3600:
            continue
        mode = None
        if s["gap_taker"] > THR["taker"] and TTE_TAKER[0] <= s["tte_d"] < TTE_TAKER[1]:
            # adopted execution upgrade: aggressive maker at ask-1c, taker fallback @30m
            mode = "am_pending"
        elif s["gap_maker"] > THR["maker"]:
            mode = "maker_pending"
        if mode is None:
            continue
        avail = MAX_DEPLOY * st["bankroll"] - deployed_capital(st)
        entry = round(s["ask"] - 0.01, 3) if mode == "am_pending" else round(s["bid"] + 0.01, 3)
        cap_share = entry + s["delta"] / 10
        clip = min(CLIP["taker" if mode == "am_pending" else "maker"], F_POS * st["bankroll"])
        shares = min(clip / max(entry, 0.02), max(avail, 0) / max(cap_share, 0.02))
        if shares * entry < MIN_POS:
            continue
        fee_paid = 0.0  # maker placement; fee charged only if the 30m taker fallback fires
        pos = dict(asset=s["asset"], slug=s["slug"], K=s["K"], T_pm=s["T_pm"],
                   yes_token=s["yes_token"], condition_id=s.get("condition_id", ""),
                   mode=mode, entry=round(entry, 4),
                   shares=round(shares, 2), fee_paid=round(fee_paid, 4),
                   delta=round(s["delta"], 3), S_entry=fv_ctx[s["asset"]]["S_bin"],
                   fv_at_entry=round(s["fv"], 4), capital=round(shares * cap_share, 2),
                   placed_ts=now)
        st["positions"].append(pos)
        st["last_entry"][s["slug"]] = now
        n_entered += 1
        log(f"ENTER {mode} {s['slug']} K={s['K']} entry={entry:.3f} fv={s['fv']:.3f} "
            f"shares={shares:.1f} (${shares*entry:.2f}) delta=${s['delta']:.1f}/sh hedge=${s['delta']*shares:.0f} notional")

    top = sorted(signals, key=lambda x: -max(x["gap_taker"], x["gap_maker"]))[:8]
    for s in top:
        log(f"  sig {s['asset']} K={s['K']:>8} tte={s['tte_d']:.1f}d pm {s['bid']:.2f}/{s['ask']:.2f} "
            f"fv={s['fv']:.3f} gapT={s['gap_taker']:+.3f} gapM={s['gap_maker']:+.3f}{' EXTRAP' if s['extrap'] else ''}")
    log(f"cycle done: signals={len(signals)} entered={n_entered} open={len(st['positions'])} "
        f"deployed=${deployed_capital(st):.2f} bankroll=${st['bankroll']:.2f}")
    save_state(st)


if __name__ == "__main__":
    if "--once" in sys.argv:
        scan_once()
    else:
        # Singleton guard: at most one daemon regardless of how many runners exist.
        import fcntl
        _lock = open(PB / ".bot.lock", "w")
        try:
            fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another paperbot daemon already running; exiting")
            sys.exit(0)
        _lock.write(str(os.getpid())); _lock.flush()
        log("daemon started (singleton lock acquired)")
        while True:
            try:
                scan_once()
            except Exception as e:
                import traceback
                log(f"CYCLE ERROR {e}\n{traceback.format_exc()}")
            time.sleep(600)
