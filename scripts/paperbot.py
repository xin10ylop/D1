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


def fv_digital(smiles, S_idx, S_bin, carry, K, T_pm):
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
        frac = (tau - tB) / max(tA - tB, 1e-9)
        w = sig_at(rB, x) ** 2 * tB + (sig_at(rA, x) ** 2 * tA - sig_at(rB, x) ** 2 * tB) * frac
        w2 = sig_at(rB, x + .01) ** 2 * tB + (sig_at(rA, x + .01) ** 2 * tA - sig_at(rB, x + .01) ** 2 * tB) * frac
        extrap = 0
    elif len(later):
        rA = later.iloc[0]
        w = sig_at(rA, x) ** 2 * tau
        w2 = sig_at(rA, x + .01) ** 2 * tau
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
                    T_pm = dt.datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp() + 60
                    out.append(dict(asset=asset, slug=mk.get("slug", ""), question=q,
                                    K=float(sm.group(1).replace(",", "")),
                                    T_pm=T_pm, yes_token=toks[0]))
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
        if p["mode"] == "maker_pending":
            # last chance: did it actually trade through our limit before expiry
            # (covers fills that happened while the bot was down)?
            low = traded_low_since(p["yes_token"], p.get("last_fill_check", p["placed_ts"]),
                                   end_ts=min(p["placed_ts"] + 4 * 3600, p["T_pm"]))
            if low is not None and low <= p["entry"] + 1e-9:
                p["mode"] = "maker"
                log(f"MAKER FILLED (retro, pre-expiry) {p['slug']} @ {p['entry']}")
            else:
                log(f"MAKER CANCELLED AT EXPIRY (unfilled) {p['slug']}")
                continue
        sym = ASSETS[p["asset"]]
        S_T = binance_close_at(sym, p["T_pm"] - 60)  # candle labeled 12:00 ET opens at T_pm-60
        if S_T is None:
            still.append(p)
            continue
        won = S_T > p["K"]
        pm_pnl = (1.0 - p["entry"] if won else -p["entry"]) * p["shares"] - p["fee_paid"]
        hedge_pnl = -p["delta"] * math.log(S_T / p["S_entry"]) * p["shares"]
        pnl = pm_pnl + hedge_pnl
        st["bankroll"] += pnl
        append_trade(dict(closed=dt.datetime.now(dt.timezone.utc).isoformat(), **{k: p[k] for k in
                          ("asset", "slug", "K", "mode", "entry", "shares", "T_pm")},
                          S_T=S_T, won=int(won), pm_pnl=round(pm_pnl, 3),
                          hedge_pnl=round(hedge_pnl, 3), pnl=round(pnl, 3),
                          bankroll=round(st["bankroll"], 2)))
        log(f"RESOLVED {p['slug']} won={won} pnl=${pnl:+.2f} bankroll=${st['bankroll']:.2f}")
    st["positions"] = still


def traded_low_since(token_id, since_ts, end_ts=None):
    """Lowest traded price for the token in [since_ts, end_ts] (CLOB price
    history, 1-min fidelity). None if unavailable."""
    e = int(end_ts if end_ts else time.time())
    r = http_json(f"https://clob.polymarket.com/prices-history?market={token_id}"
                  f"&startTs={int(since_ts)}&endTs={e}&fidelity=1")
    if not r or not r.get("history"):
        return None
    try:
        return min(float(x["p"]) for x in r["history"])
    except Exception:
        return None


def check_maker_fills(st, books):
    """Maker orders fill when (a) the live best ask is at/below our limit, or
    (b) the token actually TRADED at/below our limit since placement (matches
    the backtest's tape-print fill rule; catches intra-cycle dips).

    Fix: fetch the book for EVERY pending order's market, not only markets
    that happen to signal this cycle."""
    for p in st["positions"]:
        if p["mode"] != "maker_pending":
            continue
        if time.time() > p["placed_ts"] + 4 * 3600:
            p["mode"] = "maker_expired"
            log(f"MAKER EXPIRED {p['slug']}")
            continue
        b = books.get(p["yes_token"])
        if b is None:
            b = clob_book(p["yes_token"])  # pending markets always get a book check
        filled = bool(b and b["ask"] <= p["entry"] + 1e-9)
        if not filled:
            low = traded_low_since(p["yes_token"], p.get("last_fill_check", p["placed_ts"]))
            filled = low is not None and low <= p["entry"] + 1e-9
        p["last_fill_check"] = time.time()
        if filled:
            p["mode"] = "maker"
            log(f"MAKER FILLED {p['slug']} @ {p['entry']}")
    st["positions"] = [p for p in st["positions"] if p["mode"] != "maker_expired"]


def deployed_capital(st):
    return sum(p["capital"] for p in st["positions"] if p["mode"] in ("taker", "maker", "maker_pending"))


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
        f = fv_digital(ctx["smiles"], ctx["S_idx"], ctx["S_bin"], ctx["carry"], mk["K"], mk["T_pm"])
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
            mode = "taker"
        elif s["gap_maker"] > THR["maker"]:
            mode = "maker_pending"
        if mode is None:
            continue
        avail = MAX_DEPLOY * st["bankroll"] - deployed_capital(st)
        entry = s["ask"] if mode == "taker" else round(s["bid"] + 0.01, 3)
        cap_share = entry + s["delta"] / 10
        clip = min(CLIP["taker" if mode == "taker" else "maker"], F_POS * st["bankroll"])
        shares = min(clip / max(entry, 0.02), max(avail, 0) / max(cap_share, 0.02))
        if mode == "taker":
            # walk visible asks for exact VWAP at our size (usually touch at $10 clips)
            rem, cost, got = shares, 0.0, 0.0
            for px, szx in s["asks"]:
                take = min(szx, rem)
                cost += take * px; got += take; rem -= take
                if rem <= 0:
                    break
            if got < shares * 0.5:
                continue
            shares = got
            entry = cost / got
        if shares * entry < MIN_POS:
            continue
        fee_paid = (FEE_RATE * entry * (1 - entry) * shares) if mode == "taker" else 0.0
        pos = dict(asset=s["asset"], slug=s["slug"], K=s["K"], T_pm=s["T_pm"],
                   yes_token=s["yes_token"], mode=mode, entry=round(entry, 4),
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
