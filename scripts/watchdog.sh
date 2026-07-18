#!/bin/bash
# Keeps the quotes downloader alive; exits when all quote-days are done.
cd /home/user/D1
while true; do
  if ! pgrep -f "python3 scripts/dl_polymarket.py" > /dev/null; then
    nohup python3 scripts/dl_polymarket.py 64 >> logs/dl_watchdog.log 2>&1 &
    echo "$(date -u) restarted downloader" >> logs/watchdog.log
  fi
  sleep 60
done
