#!/usr/bin/env bash
# Restore the display settings that scripts/adaptive_ui_android.py changes for its matrix:
#   wm size (override), wm density (override), font_scale, accelerometer_rotation, user_rotation.
# Nothing else is touched (no brightness, timeout, lock, security or animation settings).
#
# Originals come from, in order: the environment (FONT_SCALE, ACCEL_ROTATION, USER_ROTATION), the file
# the matrix writes before it changes anything (/tmp/btb-display-orig.env), then the defaults
# 1.0 / 1 / 0. Serial: ANDROID_SERIAL, default R9ZY80PKWTW.
#
#   scripts/adaptive_ui_reset.sh

set -euo pipefail

SERIAL="${ANDROID_SERIAL:-R9ZY80PKWTW}"
ORIG_FILE="${BTB_DISPLAY_ORIG_FILE:-/tmp/btb-display-orig.env}"

if [[ -f "$ORIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ORIG_FILE"
fi
FONT_SCALE="${FONT_SCALE:-1.0}"
ACCEL_ROTATION="${ACCEL_ROTATION:-1}"
USER_ROTATION="${USER_ROTATION:-0}"

adb_s() { adb -s "$SERIAL" "$@"; }

adb_s shell wm size reset
adb_s shell wm density reset
adb_s shell settings put system font_scale "$FONT_SCALE"
adb_s shell settings put system accelerometer_rotation "$ACCEL_ROTATION"
adb_s shell settings put system user_rotation "$USER_ROTATION"

echo "restored: font_scale=$FONT_SCALE accelerometer_rotation=$ACCEL_ROTATION user_rotation=$USER_ROTATION, wm size/density overrides reset"
