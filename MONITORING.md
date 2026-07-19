# D1 — Polymarket×Deribit strike paper bot · /opt/d1-paperbot

Same ops pattern as the toll/snipe/efc bots: systemd service + `report.py` scorecard + journalctl.

**Daily scorecard (P&L, win rate, EV vs backtest bands — the main one):**
```
cd /opt/d1-paperbot
BOT_DATA_DIR=results/paperbot venv/bin/python3 scripts/report.py 7
```
(change `7` to any lookback in days; `30` for the fuller picture)

**Alive? (one line):**
```
systemctl is-active d1-paperbot
```

**Health detail (uptime, memory):**
```
systemctl status d1-paperbot --no-pager | grep -E "Active|Memory"
```

**Watch it trade live (Ctrl-C to exit — doesn't stop the bot):**
```
journalctl -u d1-paperbot -f
```

**Any errors or halts in the last 24h (empty = all good):**
```
journalctl -u d1-paperbot --since "24 hours ago" --no-pager | grep -E "ERROR|HALT|MISMATCH"
```

---

### What the scorecard's band verdict means
`report.py` compares live hedged EV/share against the backtest band for each mode:

| verdict | meaning |
|---|---|
| 🟢 in band | live EV matches backtest — healthy |
| 🟡 above band | better than backtest — small-sample luck or a tailwind, not a worry |
| 🟠 below band | live edge weaker than backtest over n≥20 — check fills/FV freshness |
| 🔴 negative | losing money after the hedge — investigate before scaling |
| small sample | n<20 closed trades — verdict deferred (normal early on) |

Bands (hedged EV/share, from `results/REPORT.md`, OOS 2026-05-16→07-16):
taker `[+1.0c, +10.0c]` center +5.4c · maker `[+2.0c, +7.0c]` center +4.3c · win rate ~43%.

### Log keywords the grep catches
- `ERROR` — a venue data fetch failed for an asset this cycle (that asset skipped)
- `HALT`  — no usable market data for any asset this cycle (no entries made)
- `MISMATCH` — Deribit index vs Binance spot diverged >2% (basis/oracle sanity)

### First-time install (once)
```
sudo mkdir -p /opt/d1-paperbot && sudo chown $USER /opt/d1-paperbot
git clone <repo> /opt/d1-paperbot && cd /opt/d1-paperbot
python3 -m venv venv && venv/bin/pip install -r scripts/requirements_bot.txt
sudo cp deploy/paperbot.service /etc/systemd/system/d1-paperbot.service
sudoedit /etc/systemd/system/d1-paperbot.service   # set User + the /opt paths
sudo systemctl daemon-reload && sudo systemctl enable --now d1-paperbot
systemctl is-active d1-paperbot          # -> active
journalctl -u d1-paperbot -f             # watch first cycle
```
No API keys needed — the bot runs on public Deribit/Polymarket/Binance endpoints.
