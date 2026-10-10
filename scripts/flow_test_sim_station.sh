#!/usr/bin/env bash
# Run a simulated second station for the grid demo (scripts/sim_phone.py) against the flow-test backend.
# Mint a pairing code in the dashboard first (Members, or the Pair a phone modal), then:
#
#   scripts/flow_test_sim_station.sh <pairing-code>
#
# The station pairs with the code, keeps its key in out/flowtest/sim-station-<time>.json, and streams
# frames for SIM_SECONDS (default 1800) or until Ctrl-C. The code is passed as --pair-code=<code> so that a
# code starting with '-' is not read as an option.

set -euo pipefail

if [[ $# -lt 1 || -z "${1:-}" ]]; then
  echo "usage: $0 <pairing-code>" >&2
  exit 2
fi
CODE="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/out/flowtest"
PY="$ROOT/.venv/bin/python"
SECONDS_RUN="${SIM_SECONDS:-1800}"
KEYFILE="$DATA/sim-station-$(date +%Y%m%d-%H%M%S).json"

mkdir -p "$DATA"
if ! curl -fsS -m 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "FAIL: no backend on 127.0.0.1:8000. Start it with scripts/flow_test_up.sh first." >&2
  exit 1
fi

echo "pairing the simulated station (keyfile $KEYFILE) and streaming for ${SECONDS_RUN} s ..."
exec "$PY" "$ROOT/scripts/sim_phone.py" \
  --url ws://127.0.0.1:8000/ingest-events \
  --pair-url http://127.0.0.1:8000/pair \
  --pair-code="$CODE" \
  --keyfile "$KEYFILE" \
  --device SimStation2 \
  --seconds "$SECONDS_RUN" \
  --stop close1000
