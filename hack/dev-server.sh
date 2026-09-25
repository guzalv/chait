#!/usr/bin/env bash
# hack/dev-server.sh — throwaway chait server for manual testing.
#
# Spawns server.py in the background on a random port with an isolated
# temp data dir (fresh DB, no data loss risk to any real instance), then
# exits immediately. Uses setsid+disown so the server survives after this
# script (and its parent shell/tool) exits — a plain trailing `&` is not
# enough since job control can still kill it when the parent terminates.
#
# Usage:
#   hack/dev-server.sh              # start on a random port
#   hack/dev-server.sh 3200         # start on a specific port
#   hack/dev-server.sh stop         # stop the last instance started by this script

set -euo pipefail
cd "$(dirname "$0")/.."

RUN_DIR="/tmp/chait-dev"
PID_FILE="$RUN_DIR/server.pid"
mkdir -p "$RUN_DIR"

if [[ "${1:-}" == "stop" ]]; then
  [[ -f "$PID_FILE" ]] || { echo "No dev server tracked ($PID_FILE not found)."; exit 1; }
  pid="$(cat "$PID_FILE")"
  if kill "$pid" 2>/dev/null; then echo "Stopped server (pid $pid)."; else echo "Process $pid not running."; fi
  rm -f "$PID_FILE"
  exit 0
fi

[[ -x .venv/bin/python ]] || { echo "Run 'make deps' first (.venv missing)."; exit 1; }

PORT="${1:-$(( (RANDOM % 20000) + 20000 ))}"
DATA_DIR="$(mktemp -d /tmp/chait-dev-XXXXXX)"
PASS="dev-$(openssl rand -hex 4)"
LOG_FILE="$DATA_DIR/server.log"

setsid env CHAIT_DATA_DIR="$DATA_DIR" CHAIT_PORT="$PORT" CHAIT_HUMAN_PASS="$PASS" \
  .venv/bin/python server.py > "$LOG_FILE" 2>&1 < /dev/null &
pid=$!
disown
echo "$pid" > "$PID_FILE"

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$PORT/health" > /dev/null 2>&1 && break
  sleep 0.2
done

echo "chait dev server running:"
echo "  URL:      http://127.0.0.1:$PORT"
echo "  Login:    admin / $PASS"
echo "  Data dir: $DATA_DIR"
echo "  Log:      $LOG_FILE"
echo "  PID:      $pid"
echo "  Stop:     hack/dev-server.sh stop"
