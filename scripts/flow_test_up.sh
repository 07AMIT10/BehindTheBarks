#!/usr/bin/env bash
# Start the flow-test backend for the human tester: households mode on 127.0.0.1:8000 with persistent data
# in out/flowtest/ (SQLite database, event logs, backend.log). Serves the built dashboard from frontend/out.
#
#   scripts/flow_test_up.sh
#
# The same households switch the tests use (tests/web/test_households.py): BTB_AUTH_MODE=households, and the
# generated out/flowtest/config.yaml sets web.auth.mode=households too. config.yaml itself is not edited.
# Stop it with scripts/flow_test_down.sh. Port 8001 (an unrelated process) is never touched.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/out/flowtest"
PY="$ROOT/.venv/bin/python"
PORT=8000
SERIAL="${ADB_SERIAL:-}"
PIDFILE="$DATA/backend.pid"
LOG="$DATA/backend.log"

mkdir -p "$DATA"

# Same identification as scripts/flow_test_down.sh: the flow-test backend is "uvicorn backend.main:app" with
# "--port 8000" as a whole token, and never "--port 8001". Anything else on port 8000 is not ours.
cmdline_of() {
  local args=""
  args="$(ps -o args= -p "$1" 2>/dev/null || true)"
  if [[ -z "$args" && -r "/proc/$1/cmdline" ]]; then args="$(tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null || true)"; fi
  printf '%s' "$args"
}
is_flowtest_backend() {
  local args re8000='--port[[:space:]=]8000([[:space:]]|$)' re8001='--port[[:space:]=]8001'
  args="$(cmdline_of "$1")"
  [[ -n "$args" ]] || return 1
  [[ "$args" == *"uvicorn backend.main:app"* ]] || return 1
  [[ "$args" =~ $re8000 ]] || return 1
  [[ "$args" =~ $re8001 ]] && return 1
  return 0
}
# Sets LISTENING (1 if something accepts TCP on 127.0.0.1:$1) and LISTEN_PIDS (space-separated listener pids).
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

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "FAIL: the flow-test backend is already running (pid $(cat "$PIDFILE")). Run scripts/flow_test_down.sh first."
  exit 1
fi
find_listeners "$PORT"
if [[ "$LISTENING" == 1 ]]; then
  if [[ -n "$LISTEN_PIDS" && "$LISTEN_PIDS" != *" "* ]] && is_flowtest_backend "$LISTEN_PIDS"; then
    echo "FAIL: a flow-test backend (pid $LISTEN_PIDS) already listens on port $PORT, but its PID file $PIDFILE is missing or stale."
    echo "      Run scripts/flow_test_down.sh (it finds the backend by port) and rerun."
  elif [[ -n "$LISTEN_PIDS" ]]; then
    echo "FAIL: port $PORT is held by pid $LISTEN_PIDS, which is not the flow-test backend ($(cmdline_of "$LISTEN_PIDS" | cut -c1-120))."
    echo "      Not touching it; stop it, then rerun."
  else
    echo "FAIL: port $PORT is in use by a process whose owner cannot be identified (ss and lsof missing or no permission). Not touching it."
  fi
  exit 1
fi

# Dashboard build: rebuild when frontend/out is missing or older than any frontend source file.
STALE="$(find "$ROOT/frontend" \( -path "$ROOT/frontend/node_modules" -o -path "$ROOT/frontend/out" -o -path "$ROOT/frontend/.next" \) -prune \
         -o -type f -newer "$ROOT/frontend/out/index.html" -print -quit 2>/dev/null || true)"
if [[ ! -f "$ROOT/frontend/out/index.html" || -n "$STALE" ]]; then
  echo "building the dashboard (frontend/out missing or older than the frontend sources) ..."
  npm --prefix "$ROOT/frontend" run build >"$DATA/frontend-build.log" 2>&1 || {
    echo "FAIL: npm build failed; see $DATA/frontend-build.log"; exit 1; }
fi

# Config copy for this run: the backend reads config.yaml from its working directory.
"$PY" - "$ROOT/config.yaml" "$DATA" "$DATA/config.yaml" <<'EOF'
import sys
from pathlib import Path
import yaml
src, data, dst = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
cfg = yaml.safe_load(Path(src).read_text(encoding="utf-8")) or {}
web = cfg.setdefault("web", {})
web["pipeline"] = "remote"
web["log_dir"] = str(data)
web["database_url"] = f"sqlite:///{data / 'btb.db'}"
auth = web.setdefault("auth", {})
auth["mode"] = "households"
dst.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
EOF

(
  cd "$DATA"
  export PYTHONPATH="$ROOT" WEB_PIPELINE=remote BTB_AUTH_MODE=households
  exec nohup "$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1
) &
echo $! >"$PIDFILE"
PID="$(cat "$PIDFILE")"

healthy=0
for _ in $(seq 1 60); do
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then healthy=1; break; fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "FAIL: the backend exited during start-up. Last lines of $LOG:"
    tail -n 20 "$LOG" || true
    rm -f "$PIDFILE"
    exit 1
  fi
  sleep 0.5
done
if [[ "$healthy" != 1 ]]; then
  echo "FAIL: /health did not answer within 30 s (pid $PID). See $LOG"
  exit 1
fi

PHONE="no phone connected over USB (the phone-side steps need one)"
if [[ -z "$SERIAL" ]]; then
  echo "note: ADB_SERIAL is unset — export it to your phone's serial (adb devices) for the phone-side steps; adb skipped." >&2
elif [[ "$(adb -s "$SERIAL" get-state 2>/dev/null || true)" == "device" ]]; then
  adb -s "$SERIAL" reverse tcp:$PORT tcp:$PORT >/dev/null
  PHONE="phone $SERIAL connected; adb reverse tcp:$PORT -> tcp:$PORT set"
fi

cat <<EOF
Flow-test backend is up (pid $PID, households mode, data in $DATA, log $LOG).

  Dashboard:  http://localhost:8000/
              Open exactly this address. The pairing QR is built from the address you open, so
              127.0.0.1 or the LAN address would give the phone a URL that does not match the adb reverse.
  Phone:      $PHONE
              The phone reaches the backend at localhost:8000 (adb reverse). The QR carries
              ws://localhost:8000/ingest-events.

First steps:
  1. Run scripts/flow_test_check.sh and fix any FAIL line.
  2. Open http://localhost:8000/ in Chrome (not 127.0.0.1, not the LAN IP).
  3. Create a household on the dashboard (dog name). Save the recovery key.
  4. Follow docs/FLOW_TEST_RUNBOOK.md from step 2 (tick each box as you go).
  5. Stop everything at the end with scripts/flow_test_down.sh.
EOF
