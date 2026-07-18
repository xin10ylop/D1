#!/bin/bash
# Paper bot runner: keeps the bot alive and (optionally) commits state so the
# track record survives restarts. Portable — resolves its own repo root, so it
# works on any machine (your SSH box, a VPS, this container).
#
#   PAPERBOT_GIT=1   -> auto-commit+push results/paperbot every 30 min (needs push access)
# Default: no git, just keeps the bot running and writing local state/logs.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p logs

# Singleton: only one runner may exist (prevents duplicate-bot spawn races).
exec 9>"$ROOT/logs/.runner.lock"
if ! flock -n 9; then
  echo "$(date -u) another runner already holds the lock; exiting" >> logs/paperbot_runner.log
  exit 0
fi

LAST_COMMIT=0
while true; do
  if ! pgrep -f "paperbot.py --daemon" > /dev/null; then
    ( cd "$ROOT/scripts" && nohup python3 paperbot.py --daemon >> "$ROOT/logs/paperbot.log" 2>&1 & )
    echo "$(date -u) paperbot (re)started" >> logs/paperbot_runner.log
  fi
  if [ "${PAPERBOT_GIT:-0}" = "1" ]; then
    NOW=$(date +%s)
    if [ $((NOW - LAST_COMMIT)) -gt 1800 ] && [ -n "$(git status --porcelain results/paperbot 2>/dev/null)" ]; then
      git add results/paperbot && git commit -q -m "paperbot: state $(date -u +%Y-%m-%dT%H:%M)" && git push -q 2>/dev/null
      LAST_COMMIT=$NOW
    fi
  fi
  sleep 120
done
