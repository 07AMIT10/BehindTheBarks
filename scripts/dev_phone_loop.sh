#!/usr/bin/env bash
# Scripted WagWatch dev loop (plan task T0.4): one command per step, driven by adb.
#
#   scripts/dev_phone_loop.sh start-backend      # uvicorn on 127.0.0.1:8000, log at /tmp/btb-backend.log
#   scripts/dev_phone_loop.sh start-phone        # adb reverse + MainActivity START intent (DebugPhone)
#   scripts/dev_phone_loop.sh stop-phone         # tap the real "Stop Monitoring" button (uiautomator)
#   scripts/dev_phone_loop.sh status             # GET /status
#   scripts/dev_phone_loop.sh shot 390 out.png   # headless Chrome screenshot at phone width
#   scripts/dev_phone_loop.sh sim [extra args]   # run scripts/sim_phone.py with the default URL
#   scripts/dev_phone_loop.sh clean              # undo all of the above
#
# Requires: the phone unlocked with USB debugging authorised (README rule 1).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_PORT="${BTB_PORT:-8000}"
BACKEND_LOG="${BTB_BACKEND_LOG:-/tmp/btb-backend.log}"
BACKEND_PID_FILE="/tmp/btb-backend.pid"
CHROME="${BTB_CHROME:-/home/amit/.local/bin/google-chrome}"
DASH_URL="http://localhost:${BACKEND_PORT}/"
INGEST_URL="ws://127.0.0.1:${BACKEND_PORT}/ingest-events"

start-backend() {
  if [[ -f "$BACKEND_PID_FILE" ]] && kill -0 "$(cat "$BACKEND_PID_FILE")" 2>/dev/null; then
    echo "backend already running (pid $(cat "$BACKEND_PID_FILE")), log: $BACKEND_LOG"
    return 0
  fi
  WEB_PIPELINE=remote nohup .venv/bin/python -m uvicorn backend.main:app \
    --host 127.0.0.1 --port "$BACKEND_PORT" >"$BACKEND_LOG" 2>&1 &
  echo $! >"$BACKEND_PID_FILE"
  echo "backend pid $(cat "$BACKEND_PID_FILE"), log: $BACKEND_LOG"
  for _ in $(seq 1 50); do
    if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null 2>&1; then
      echo "healthy"; return 0
    fi
    sleep 0.2
  done
  echo "backend did not become healthy; last log lines:" >&2
  tail -20 "$BACKEND_LOG" >&2
  if grep -q "address already in use" "$BACKEND_LOG"; then
    echo "hint: port ${BACKEND_PORT} is taken by another process; rerun with BTB_PORT=<free port>" >&2
  fi
  return 1
}

start-phone() {
  adb reverse "tcp:${BACKEND_PORT}" "tcp:${BACKEND_PORT}"
  adb shell am start -n com.btb.ondevice/.ui.MainActivity \
    -a com.btb.ondevice.action.START \
    --es extra_server_url "$INGEST_URL" \
    --es extra_device_name DebugPhone
  echo "monitoring intent sent; wait ~5 s then run: $0 status"
}

stop-phone() {
  # Locate the real button via uiautomator: never hard-code coordinates.
  adb shell uiautomator dump /sdcard/ui.xml >/dev/null
  adb shell cat /sdcard/ui.xml >/tmp/btb-ui.xml
  local coords
  coords=$(python3 - <<'PY'
import re
import sys

xml = open("/tmp/btb-ui.xml", encoding="utf-8").read()
m = re.search(r'<node[^>]*text="Stop Monitoring"[^>]*>', xml)
if not m:
    sys.exit("no node with text=\"Stop Monitoring\" in the current UI dump")
b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', m.group(0))
if not b:
    sys.exit("Stop Monitoring node has no bounds attribute")
x1, y1, x2, y2 = map(int, b.groups())
print((x1 + x2) // 2, (y1 + y2) // 2)
PY
)
  # shellcheck disable=SC2086
  adb shell input tap $coords
  echo "tapped Stop Monitoring at $coords"
}

status() {
  curl -fsS "http://127.0.0.1:${BACKEND_PORT}/status" | .venv/bin/python -m json.tool
}

shot() {
  local width="${1:?usage: shot <width> <out.png> [url]}"
  local out="${2:?usage: shot <width> <out.png> [url]}"
  local url="${3:-$DASH_URL}"
  "$CHROME" --headless=new --no-sandbox --hide-scrollbars \
    --window-size="${width},844" --virtual-time-budget=8000 \
    --screenshot="$out" "$url" >/dev/null 2>&1
  echo "wrote $out ($(stat -c%s "$out") bytes) from $url"
}

sim() {
  .venv/bin/python scripts/sim_phone.py --url "$INGEST_URL" "$@"
}

clean() {
  adb reverse --remove-all || true
  adb shell am force-stop com.btb.ondevice || true
  if [[ -f "$BACKEND_PID_FILE" ]]; then
    kill "$(cat "$BACKEND_PID_FILE")" 2>/dev/null || true
    rm -f "$BACKEND_PID_FILE"
    echo "backend stopped"
  fi
}

cmd="${1:-help}"
shift || true
case "$cmd" in
  start-backend | start-phone | stop-phone | status | clean) "$cmd" "$@" ;;
  shot) shot "$@" ;;
  sim) sim "$@" ;;
  *)
    sed -n '2,15p' "$0"
    exit 1
    ;;
esac
