#!/usr/bin/env python3
"""One-glance paper-bot review: bankroll, P&L, win rate, open + closed trades.

Reads local files only (no network). Run: python3 scripts/status.py
"""
import json, pathlib, datetime as dt, csv

ROOT = pathlib.Path(__file__).resolve().parent.parent
PB = ROOT / "results" / "paperbot"
STATE = PB / "state.json"
TRADES = PB / "trades.csv"
START = 100.0


def money(x):
    return f"${x:,.2f}"


def load_trades():
    if not TRADES.exists():
        return []
    with open(TRADES) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("K", "entry", "shares", "S_T", "pm_pnl", "hedge_pnl", "pnl", "bankroll"):
            try:
                r[k] = float(r[k])
            except (KeyError, ValueError):
                r[k] = 0.0
        r["won"] = str(r.get("won", "0")).strip() in ("1", "1.0", "True", "true")
    return rows


def main():
    st = json.loads(STATE.read_text()) if STATE.exists() else {"bankroll": START, "positions": []}
    bank = float(st.get("bankroll", START))
    pos = st.get("positions", [])
    closed = load_trades()
    now = dt.datetime.now(dt.timezone.utc)

    open_capital = sum(p.get("capital", 0) for p in pos if p.get("mode") in ("taker", "maker", "maker_pending"))
    realized = bank - START

    print("=" * 66)
    print(f"  PAPER BOT   {now:%Y-%m-%d %H:%M UTC}")
    print("=" * 66)
    print(f"  Bankroll   {money(bank)}   start {money(START)}   P&L {money(realized)} ({realized/START*100:+.1f}%)")
    print(f"  Deployed   {money(open_capital)} across {len(pos)} open position(s)")

    if closed:
        n = len(closed)
        wins = sum(1 for r in closed if r["won"])
        tot = sum(r["pnl"] for r in closed)
        by = {}
        for r in closed:
            m = r.get("mode", "?"); by.setdefault(m, [0, 0.0])
            by[m][0] += 1; by[m][1] += r["pnl"]
        print(f"  Closed     {n} trades   win rate {wins}/{n} = {wins/n*100:.0f}%   realized {money(tot)}")
        print("             " + "  ".join(f"{m}: {c}t {money(p)}" for m, (c, p) in by.items()))
        avg = tot / n
        print(f"             avg {money(avg)}/trade ({avg/ (sum(r['entry']*r['shares'] for r in closed)/n) *100:+.1f}% on size)")
    else:
        print("  Closed     0 trades yet (positions settle at their 12:00-ET expiry)")

    print(f"\n  OPEN ({len(pos)}):")
    if not pos:
        print("    (none right now — most 10-min scans find nothing, as in the backtest)")
    for p in sorted(pos, key=lambda x: x.get("T_pm", 0)):
        hleft = (p.get("T_pm", 0) - now.timestamp()) / 3600
        edge = p.get("fv_at_entry", 0) - p.get("entry", 0)
        tag = {"taker": "TAKER", "maker": "MAKER(filled)", "maker_pending": "maker(resting)"}.get(p.get("mode"), p.get("mode"))
        print(f"    {tag:15} {p['asset']} ${p['K']:>8.0f}  entry {p['entry']:.3f}  fv {p.get('fv_at_entry',0):.3f}"
              f"  edge {edge:+.3f}  {p['shares']:.0f}sh (${p['shares']*p['entry']:.0f})  hedge ${p.get('delta',0)*p['shares']:.0f}  {hleft:.0f}h left")

    if closed:
        print(f"\n  LAST {min(10,len(closed))} CLOSED:")
        for r in closed[-10:]:
            wl = "WIN " if r["won"] else "loss"
            when = str(r.get("closed", ""))[:16]
            print(f"    {when}  {wl}  {r['asset']} ${r['K']:>8.0f}  entry {r['entry']:.3f}"
                  f"  P&L {money(r['pnl'])}  (pm {money(r['pm_pnl'])} + hedge {money(r['hedge_pnl'])})")
    print()


if __name__ == "__main__":
    main()
