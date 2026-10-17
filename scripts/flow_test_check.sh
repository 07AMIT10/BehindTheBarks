#!/usr/bin/env bash
# Quick, non-interactive sanity check before the flow test. Changes nothing. Prints one PASS/FAIL/INFO line
# per check and exits 1 if any check FAILs.
#
#   scripts/flow_test_check.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/out/flowtest"
PIDFILE="$DATA/backend.pid"
SERIAL="${ADB_SERIAL:-}"
PORT=8000
PKG=com.btb.ondevice
FAILS=0

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
info() { echo "INFO  $1"; }

# 1. backend health on 8000
if HEALTH="$(curl -fsS -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null)"; then
  pass "backend /health answers on 127.0.0.1:$PORT"
else
  fail "backend /health does not answer on 127.0.0.1:$PORT (run scripts/flow_test_up.sh)"
fi

# 2. the process on port 8000 is the flow-test backend (same identification as flow_test_down.sh), and the PID file agrees
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
LISTEN_PIDS=""
LISTENING=0
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then LISTENING=1; fi
if command -v ss >/dev/null 2>&1; then
  LISTEN_PIDS="$(ss -Hltnp "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -un | tr '\n' ' ' || true)"
elif command -v lsof >/dev/null 2>&1; then
  LISTEN_PIDS="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | grep -E '^[0-9]+$' | sort -un | tr '\n' ' ' || true)"
fi
LISTEN_PIDS="${LISTEN_PIDS% }"
[[ -n "$LISTEN_PIDS" ]] && LISTENING=1
if [[ -n "$LISTEN_PIDS" && "$LISTEN_PIDS" != *" "* ]] && is_flowtest_backend "$LISTEN_PIDS"; then
  PIDFILE_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ "$PIDFILE_PID" == "$LISTEN_PIDS" ]]; then
    pass "flow-test backend is running (pid $LISTEN_PIDS, matches the PID file)"
  else
    fail "flow-test backend (pid $LISTEN_PIDS) listens on $PORT, but the PID file $PIDFILE is missing or stale: run scripts/flow_test_down.sh (it stops it by port)"
  fi
elif [[ -n "$LISTEN_PIDS" ]]; then
  fail "port $PORT is held by pid $LISTEN_PIDS, which is not the flow-test backend ($(cmdline_of "$LISTEN_PIDS" | cut -c1-120)); not touched"
elif [[ "$LISTENING" == 1 ]]; then
  fail "port $PORT is in use, but its owner cannot be identified (ss and lsof missing or no permission)"
else
  fail "no flow-test backend on port $PORT (run scripts/flow_test_up.sh)"
fi
AUTH="$(curl -fsS -m 3 "http://127.0.0.1:$PORT/auth/status" 2>/dev/null || true)"
if [[ "$AUTH" == *'"mode":"households"'* || "$AUTH" == *'"mode": "households"'* ]]; then
  pass "auth mode is households (/auth/status)"
else
  fail "auth mode is not households: ${AUTH:-no answer}"
fi

# 3. dashboard build
if [[ -f "$ROOT/frontend/out/index.html" ]]; then
  pass "dashboard build present (frontend/out/index.html)"
else
  fail "dashboard build missing (scripts/flow_test_up.sh builds it)"
fi

# 4. adb device present and authorised (ADB_SERIAL must be exported: the previous hard-coded
#    default serial was removed for the flow-test too, see scripts/flow_test_up.sh)
if [[ -z "$SERIAL" ]]; then
  STATE=""
  fail "ADB_SERIAL is not set — export it to the phone serial printed by 'adb devices'"
else
  STATE="$(adb -s "$SERIAL" get-state 2>/dev/null || true)"
  if [[ "$STATE" == "device" ]]; then
    pass "adb device $SERIAL is connected and authorised"
  else
    fail "adb device $SERIAL not in 'device' state (got '${STATE:-nothing}'); check the USB cable and the USB debugging prompt"
  fi
fi

if [[ "$STATE" == "device" ]]; then
  # 5. app installed
  if adb -s "$SERIAL" shell pm list packages "$PKG" 2>/dev/null | grep -q "package:$PKG"; then
    pass "app $PKG is installed"
  else
    fail "app $PKG is not installed (build and install: see docs/FLOW_TEST_RUNBOOK.md step 0)"
  fi

  # 6. phone awake and unlocked (no keyguard showing)
  if adb -s "$SERIAL" shell dumpsys power 2>/dev/null | grep -q "mWakefulness=Awake"; then
    WINDOW="$(adb -s "$SERIAL" shell dumpsys window 2>/dev/null || true)"
    if echo "$WINDOW" | grep -q "isKeyguardShowing=true"; then
      fail "phone is locked (keyguard showing): unlock it by hand"
    else
      pass "phone is awake and unlocked"
    fi
  else
    fail "phone is not awake: press the power key and unlock it by hand"
  fi

  # 7. adb reverse for the backend port
  if adb -s "$SERIAL" reverse --list 2>/dev/null | grep -q "tcp:$PORT"; then
    pass "adb reverse tcp:$PORT is set"
  else
    fail "adb reverse tcp:$PORT is not set (scripts/flow_test_up.sh sets it)"
  fi
fi

# 8. ports: 8000 is ours; 8001 belongs to an unrelated process and is only reported
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  pass "port $PORT is listening"
else
  fail "port $PORT is not listening"
fi
if (exec 3<>"/dev/tcp/127.0.0.1/8001") 2>/dev/null; then
  info "port 8001 is listening (unrelated process, left alone)"
else
  info "port 8001 is not listening (unrelated, left alone)"
fi

echo
if [[ "$FAILS" -eq 0 ]]; then
  echo "RESULT: all checks PASS"
  exit 0
fi
echo "RESULT: $FAILS check(s) FAIL"
exit 1
