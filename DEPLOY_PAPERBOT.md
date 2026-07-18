# Running the paper bot 24/7 on your own machine

The bot is fully self-contained: it needs only Python + 3 packages and outbound
HTTPS to three public APIs (Deribit, Polymarket, Binance). **No API keys, no
database, no historical data files.** Everything it needs is fetched live each cycle.

## What it does each cycle (every 10 min)
1. Pull recent Deribit option trades → fit the vol smile per expiry (same code as the backtest).
2. Fetch live Polymarket BTC/ETH daily-ladder order books.
3. Compute options-implied fair value for every strike, aligned to the exact Binance
   12:00-ET resolution instant.
4. Enter (paper) when a gap clears the frozen thresholds; record a paper short-perp hedge.
5. At each expiry, fetch the real Binance 1-min candle close and settle the position.

State: `results/paperbot/state.json` (bankroll + open positions), `trades.csv`
(closed trades), `journal.log` (every decision). Bankroll starts at $100.

## Setup (Linux/macOS SSH box)

```bash
git clone <your-fork-of-this-repo> && cd D1        # or scp the scripts/ folder over
python3 -m pip install -r scripts/requirements_bot.txt

# smoke test — one scan, then exits:
cd scripts && python3 paperbot.py --once
```

You should see `scan: N live daily-ladder strikes` and a `cycle done` line.

## Keep it alive 24/7 — pick one

### Option A: the bundled runner (simplest)
```bash
nohup bash scripts/run_paperbot.sh > /dev/null 2>&1 &
```
Restarts the bot if it dies. Set `PAPERBOT_GIT=1` before launching if you want it to
auto-commit its state to your own repo every 30 min (needs push access).

### Option B: systemd (survives reboots — recommended for a real box)
Create `/etc/systemd/system/paperbot.service` (edit User and paths):
```ini
[Unit]
Description=Polymarket-Deribit paper bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/D1/scripts
ExecStart=/usr/bin/python3 /home/YOUR_USER/D1/scripts/paperbot.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now paperbot
journalctl -u paperbot -f          # watch it live
```

### Option C: tmux/screen (quick and dirty)
```bash
tmux new -s paperbot 'cd scripts && python3 paperbot.py'
# detach: Ctrl-b d   |   reattach: tmux attach -t paperbot
```

## Watching results
```bash
tail -f logs/paperbot.log                       # live decisions
cat results/paperbot/state.json | python3 -m json.tool   # bankroll + open positions
column -s, -t results/paperbot/trades.csv | less -S      # closed-trade ledger
```

## Notes / knobs (top of scripts/paperbot.py)
- `BANKROLL0=100`, `F_POS=0.10`, `THR={'taker':0.05,'maker':0.025}` — the frozen backtest config.
- Maker fills use the conservative backtest rule (fill only if the live ask trades through
  your limit; expire unfilled after 4h) — this *understates* fills vs. reality, so paper
  results lean pessimistic.
- The perp hedge is bookkept (P&L from real Binance entry vs. settlement prices), not executed.
- Timezone: resolution uses the market's own endDate (12:00 ET) and the matching Binance
  1-minute candle — identical to how Polymarket settles.

## Going from paper to real (later, deliberately)
Two functions become real order calls: the taker/maker entry in `scan_once()` (→ Polymarket
CLOB order) and the hedge line (→ Deribit perp order). Everything else — the signal, sizing,
fees, resolution — is already exactly what live would use. **Check Polymarket + Deribit
eligibility for your jurisdiction first.**
