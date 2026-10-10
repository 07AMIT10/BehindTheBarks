#!/usr/bin/env bash
# Quick, non-interactive sanity check before the flow test. Changes nothing. Prints one PASS/FAIL/INFO line
# per check and exits 1 if any check FAILs.
#
#   scripts/flow_test_check.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/out/flowtest"
PIDFILE="$DATA/backend.pid"
SERIAL="${ADB_SERIAL:-R9ZY80PKWTW}"
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

# 2. the backend that answers is the flow-test one (pid file), in households mode
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  pass "flow-test backend is running (pid $(cat "$PIDFILE"))"
else
  fail "no live flow-test pid file at $PIDFILE"
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

# 4. adb device present and authorised
STATE="$(adb -s "$SERIAL" get-state 2>/dev/null || true)"
if [[ "$STATE" == "device" ]]; then
  pass "adb device $SERIAL is connected and authorised"
else
  fail "adb device $SERIAL not in 'device' state (got '${STATE:-nothing}'); check the USB cable and the USB debugging prompt"
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
