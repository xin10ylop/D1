#!/bin/bash
# Paper bot runner: keeps the bot alive, commits state periodically so the
# paper track record survives container restarts.
cd /home/user/D1
LAST_COMMIT=0
while true; do
  if ! pgrep -f "python3 scripts/paperbot.py$" > /dev/null; then
    cd scripts && nohup python3 paperbot.py >> ../logs/paperbot.log 2>&1 &
    cd /home/user/D1
    echo "$(date -u) paperbot (re)started" >> logs/paperbot_runner.log
  fi
  NOW=$(date +%s)
  if [ $((NOW - LAST_COMMIT)) -gt 1800 ]; then
    if ! git diff --quiet --stat -- results/paperbot 2>/dev/null || [ -n "$(git status --porcelain results/paperbot)" ]; then
      git add results/paperbot && git commit -q -m "paperbot: state update $(date -u +%Y-%m-%dT%H:%M)" && git push -q 2>/dev/null
      LAST_COMMIT=$NOW
    fi
  fi
  sleep 120
done
