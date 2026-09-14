#!/bin/bash
# Local launchd entry: subscription-only generation, one locked posting cycle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs
export LLM_PROVIDER=claude_subscription
export X_LIVE_POST=true
exec "$ROOT/venv/bin/python3" "$ROOT/scripts/local_post.py" "$@" >> "$ROOT/logs/launchd.log" 2>&1
