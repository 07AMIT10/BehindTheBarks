#!/usr/bin/env bash
# Stop the flow-test backend started by scripts/flow_test_up.sh: stop it by its PID file, or, if the PID file is
# missing or stale, by the process listening on port 8000. Only a process whose command line is the flow-test
# backend (uvicorn backend.main:app ... --port 8000, and not --port 8001) is ever stopped. Remove the adb reverse
# rule for port 8000 and force-stop the Android app. Data in out/flowtest/ is kept.
# Port 8001 (an unrelated process) is never touched.
#
#   scripts/flow_test_down.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/out/flowtest"
PIDFILE="$DATA/backend.pid"
SERIAL="${ADB_SERIAL:-}"
PORT=8000

# Sets LISTENING (1 if something accepts TCP on 127.0.0.1:$1), LISTEN_PIDS (space-separated pids of the listeners,
# empty if neither ss nor lsof can tell). Uses ss first, lsof second; both may be absent.
find_listeners() {
  local port="$1" out=""
  LISTENING=0
  LISTEN_PIDS=""
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then LISTENING=1; fi
  if command -v ss >/dev/null 2>&1; then
    out="$(ss -Hltnp "sport = :$port" 2>/dev/null || true)"
    LISTEN_PIDS="$(printf '%s\n' "$out" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -un | tr '\n' ' ' || true)"
  elif command -v lsof >/dev/null 2>&1; then
    out="$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    LISTEN_PIDS="$(printf '%s\n' "$out" | grep -E '^[0-9]+$' | sort -un | tr '\n' ' ' || true)"
  fi
  LISTEN_PIDS="${LISTEN_PIDS% }"
  if [[ -n "$LISTEN_PIDS" ]]; then LISTENING=1; fi
}

# Command line of pid $1 (ps, with /proc as a fallback).
cmdline_of() {
  local args=""
  args="$(ps -o args= -p "$1" 2>/dev/null || true)"
  if [[ -z "$args" && -r "/proc/$1/cmdline" ]]; then args="$(tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null || true)"; fi
  printf '%s' "$args"
}

# True only for the flow-test backend on port 8000: must contain "uvicorn backend.main:app" and "--port 8000"
# as a whole token, and must never contain "--port 8001".
is_flowtest_backend() {
  local args re8000='--port[[:space:]=]8000([[:space:]]|$)' re8001='--port[[:space:]=]8001'
  args="$(cmdline_of "$1")"
  [[ "$PORT" == 8000 ]] || return 1
  [[ -n "$args" ]] || return 1
  [[ "$args" == *"uvicorn backend.main:app"* ]] || return 1
  [[ "$args" =~ $re8000 ]] || return 1
  [[ "$args" =~ $re8001 ]] && return 1
  return 0
}

# SIGTERM, wait up to 5 s, then SIGKILL. Only ever called with a pid that passed is_flowtest_backend.
stop_backend_pid() {
  local pid="$1" i
  kill -TERM "$pid" 2>/dev/null || true
  for i in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
    echo "backend pid $pid did not stop within 5 s on TERM; sent KILL"
  else
    echo "backend pid $pid stopped"
  fi
}

STOPPED=0
if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null && is_flowtest_backend "$PID"; then
    stop_backend_pid "$PID"
    STOPPED=1
  elif [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    echo "pid file says $PID, but that process is not the flow-test backend; left alone"
  else
    echo "pid file says ${PID:-nothing}, but no such process is running (stale pid file)"
  fi
  rm -f "$PIDFILE"
else
  echo "no $PIDFILE; looking for the flow-test backend by port $PORT"
fi

# Fallback: whatever listens on port 8000 is stopped only if it is the flow-test backend.
find_listeners "$PORT"
if [[ "$LISTENING" != 1 ]]; then
  echo "port $PORT is free"
elif [[ -z "$LISTEN_PIDS" ]]; then
  echo "port $PORT is in use, but its owner cannot be identified (ss and lsof missing or no permission); not touched"
elif [[ "$LISTEN_PIDS" == *" "* ]]; then
  echo "port $PORT has more than one listener (pids $LISTEN_PIDS); not touched"
elif is_flowtest_backend "$LISTEN_PIDS"; then
  echo "found the flow-test backend on port $PORT by port (pid $LISTEN_PIDS; PID file missing or stale)"
  stop_backend_pid "$LISTEN_PIDS"
  STOPPED=1
else
  echo "port $PORT is held by pid $LISTEN_PIDS, which is not the flow-test backend ($(cmdline_of "$LISTEN_PIDS" | cut -c1-120)); not touched"
fi

find_listeners "$PORT"
if [[ "$LISTENING" == 1 ]]; then
  echo "note: port $PORT is still in use after the stop attempt; see the line above"
elif [[ "$STOPPED" == 1 ]]; then
  echo "port $PORT is now free"
fi

if [[ -z "$SERIAL" ]]; then
  echo "ADB_SERIAL is unset; phone-side adb steps skipped (app not force-stopped, adb reverse left as-is)"
elif [[ "$(adb -s "$SERIAL" get-state 2>/dev/null || true)" == "device" ]]; then
  adb -s "$SERIAL" reverse --remove "tcp:$PORT" >/dev/null 2>&1 && echo "adb reverse tcp:$PORT removed" \
    || echo "no adb reverse rule for tcp:$PORT"
  adb -s "$SERIAL" shell am force-stop com.btb.ondevice && echo "app force-stopped"
else
  echo "phone with serial \$ADB_SERIAL not connected; adb steps skipped"
fi
