#!/usr/bin/env python3
"""Daily scorecard: P&L, win rate, EV vs backtest bands. Matches the ops pattern
of the other bots.

Usage:
    python3 report.py [DAYS]                 # default 7; data dir from $BOT_DATA_DIR
    BOT_DATA_DIR=results/paperbot python3 report.py 7
    python3 report.py 30 results/paperbot    # explicit data dir as 2nd arg

Reads state.json + trades.csv only (no network, no heavy deps).

Backtest bands (hedged EV per share, from results/REPORT.md, OOS 2026-05-16..07-16):
  taker : OOS +5.4c/sh (event-wtd +6.6c), train +8.6c  -> expect band [+1.0c, +10.0c]
  maker : OOS +4.3c/sh (tape-verified, t=2.75)         -> expect band [+2.0c,  +7.0c]
  win rate ~43% both modes; avg entry ~0.50.
A live EV that sits BELOW its band over a meaningful sample (n>=20) is the
signal that something has drifted (bad fills, stale FV, regime change) -> investigate.
"""
import sys, os, json, csv, math, pathlib, datetime as dt

# (low, center, high) hedged EV per share, and expected win rate
BANDS = {
    "taker": {"ev": (0.010, 0.054, 0.100), "win": 0.43},
    "maker": {"ev": (0.020, 0.043, 0.070), "win": 0.43},
    "all":   {"ev": (0.015, 0.045, 0.090), "win": 0.43},
}
MIN_N = 20  # below this, EV band verdict is "small sample"


def datadir():
    if len(sys.argv) > 2:
        return pathlib.Path(sys.argv[2])
    return pathlib.Path(os.environ.get("BOT_DATA_DIR", "results/paperbot"))


def load(dd):
    st = {"bankroll": 100.0, "positions": []}
    sf = dd / "state.json"
    if sf.exists():
        st = json.loads(sf.read_text())
    rows = []
    tf = dd / "trades.csv"
    if tf.exists():
        with open(tf) as f:
            for r in csv.DictReader(f):
                for k in ("K", "entry", "shares", "S_T", "pm_pnl", "hedge_pnl", "pnl"):
                    try:
                        r[k] = float(r[k])
                    except (KeyError, ValueError):
                        r[k] = 0.0
                r["won"] = str(r.get("won", "0")).strip() in ("1", "1.0", "True", "true")
                rows.append(r)
    return st, rows


def within_days(rows, days):
    if days <= 0:
        return rows
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    out = []
    for r in rows:
        try:
            t = dt.datetime.fromisoformat(str(r.get("closed", "")).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
        if t >= cut:
            out.append(r)
    return out


def band_verdict(ev_share, n, mode):
    lo, ctr, hi = BANDS[mode]["ev"]
    if n < MIN_N:
        return f"small sample (n={n}<{MIN_N}) — band check deferred"
    if ev_share < 0:
        return "🔴 NEGATIVE — below band, investigate"
    if ev_share < lo:
        return f"🟠 BELOW band [{lo*100:.1f},{hi*100:.1f}]c — investigate"
    if ev_share > hi:
        return f"🟡 above band — small-sample luck or regime tailwind"
    return f"🟢 in band [{lo*100:.1f},{hi*100:.1f}]c (center {ctr*100:.1f}c)"


def scorecard(rows, mode_label, mode_key):
    if not rows:
        print(f"  {mode_label:6}  no closed trades in window")
        return
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    pnl = sum(r["pnl"] for r in rows)
    size = sum(r["entry"] * r["shares"] for r in rows)
    ev_share = sum(r["pnl"] for r in rows) / sum(r["shares"] for r in rows)
    hedge = sum(r["hedge_pnl"] for r in rows)
    roi = pnl / size * 100 if size else 0.0
    wr = wins / n
    wr_exp = BANDS[mode_key]["win"]
    print(f"  {mode_label:6}  n={n:<4} win {wins}/{n}={wr*100:.0f}% (exp ~{wr_exp*100:.0f}%)  "
          f"P&L ${pnl:+.2f}  EV {ev_share*100:+.2f}c/sh  ROI {roi:+.1f}%  hedgePnL ${hedge:+.2f}")
    print(f"          EV vs backtest: {band_verdict(ev_share, n, mode_key)}")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else 7
    dd = datadir()
    st, allrows = load(dd)
    rows = within_days(allrows, days)
    now = dt.datetime.now(dt.timezone.utc)
    bank = float(st.get("bankroll", 100.0))
    openpos = st.get("positions", [])

    print("=" * 70)
    print(f"  PAPER-BOT SCORECARD   last {days}d   {now:%Y-%m-%d %H:%M UTC}   [{dd}]")
    print("=" * 70)
    print(f"  Bankroll ${bank:,.2f}   |   open positions: {len(openpos)}   "
          f"|   closed (window): {len(rows)}  (all-time: {len(allrows)})")
    print("-" * 70)
    scorecard(rows, "ALL", "all")
    for m, key in [("taker", "taker"), ("maker", "maker")]:
        scorecard([r for r in rows if r.get("mode") == m], m, key)
    print("-" * 70)

    # open exposure
    dep = sum(p.get("capital", 0) for p in openpos)
    print(f"  OPEN EXPOSURE ${dep:.2f} across {len(openpos)} position(s):")
    for p in sorted(openpos, key=lambda x: x.get("T_pm", 0))[:12]:
        hleft = (p.get("T_pm", 0) - now.timestamp()) / 3600
        edge = p.get("fv_at_entry", 0) - p.get("entry", 0)
        print(f"    {p.get('mode',''):14} {p['asset']} ${p['K']:>8.0f}  entry {p['entry']:.3f}  "
              f"edge {edge:+.3f}  {p['shares']:.0f}sh  {hleft:.0f}h")
    if not openpos:
        print("    (none)")

    # last few closed
    if rows:
        print("-" * 70)
        print(f"  LAST {min(8,len(rows))} CLOSED:")
        for r in rows[-8:]:
            print(f"    {str(r.get('closed',''))[:16]}  {'WIN ' if r['won'] else 'loss'}  "
                  f"{r.get('mode',''):6} {r['asset']} ${r['K']:>8.0f}  "
                  f"P&L ${r['pnl']:+.2f} (pm ${r['pm_pnl']:+.2f} + hdg ${r['hedge_pnl']:+.2f})")
    print()


if __name__ == "__main__":
    main()
