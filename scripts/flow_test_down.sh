#!/usr/bin/env bash
# Stop the flow-test backend started by scripts/flow_test_up.sh: stop it by its PID file, remove the
# adb reverse rule for port 8000, and force-stop the Android app. Data in out/flowtest/ is kept.
# Port 8001 (an unrelated process) is never touched.
#
#   scripts/flow_test_down.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/out/flowtest"
PIDFILE="$DATA/backend.pid"
SERIAL="${ADB_SERIAL:-R9ZY80PKWTW}"
PORT=8000

if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE")"
  if kill -0 "$PID" 2>/dev/null && tr '\0' ' ' <"/proc/$PID/cmdline" 2>/dev/null | grep -q "backend.main:app"; then
    kill -TERM "$PID"
    for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
    if kill -0 "$PID" 2>/dev/null; then
      kill -KILL "$PID" 2>/dev/null
      echo "backend pid $PID did not stop on TERM; sent KILL"
    else
      echo "backend pid $PID stopped"
    fi
  else
    echo "pid file says $PID, but that process is not the flow-test backend; left alone"
  fi
  rm -f "$PIDFILE"
else
  echo "no $PIDFILE; no flow-test backend recorded"
fi

if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  echo "note: something still listens on port $PORT (not started by flow_test_up.sh?); not touched"
fi

if [[ "$(adb -s "$SERIAL" get-state 2>/dev/null || true)" == "device" ]]; then
  adb -s "$SERIAL" reverse --remove "tcp:$PORT" >/dev/null 2>&1 && echo "adb reverse tcp:$PORT removed" \
    || echo "no adb reverse rule for tcp:$PORT"
  adb -s "$SERIAL" shell am force-stop com.btb.ondevice && echo "app force-stopped"
else
  echo "phone $SERIAL not connected; adb steps skipped"
fi
