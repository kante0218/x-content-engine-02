#!/bin/bash
# launchd から呼ぶラッパー。venv python を直接叩いて pipeline.py を回す。
# 注意: プロジェクトは ~/x-automation-wakana に置くこと。
#       Desktop/Documents/Downloads はmacOS TCC保護でlaunchdが実行できない。
set -uo pipefail
DIR="/Users/user/x-automation-wakana"
cd "$DIR"
ts() { date '+%Y-%m-%d %H:%M:%S %z'; }
echo "[$(ts)] start" >> logs/launchd.log

# スリープ復帰直後はWiFi未接続のことが多く Anthropic API が Connection error になる。
# api.anthropic.com に到達できるまで最大3分待つ(10秒間隔×18回)。
for i in $(seq 1 18); do
  if curl -sS -o /dev/null --max-time 5 https://api.anthropic.com/ 2>/dev/null; then
    echo "[$(ts)] network ready (try $i)" >> logs/launchd.log
    break
  fi
  echo "[$(ts)] waiting for network (try $i)" >> logs/launchd.log
  sleep 10
done

# Connection error 等の一時障害に備え、生成+投稿を最大3回まで再試行。
# (生成段階で失敗した回は投稿前に中断するので二重投稿にはならない)
for attempt in 1 2 3; do
  "$DIR/venv/bin/python3" "$DIR/scripts/pipeline.py" >> logs/launchd.log 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "[$(ts)] pipeline ok (attempt $attempt)" >> logs/launchd.log
    break
  fi
  echo "[$(ts)] pipeline failed rc=$rc (attempt $attempt)" >> logs/launchd.log
  [ "$attempt" -lt 3 ] && sleep 30
done
echo "[$(ts)] end" >> logs/launchd.log
