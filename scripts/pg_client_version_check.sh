#!/usr/bin/env sh
# Postgres client version guard for the nightly backup timer (deploy/oracle/cloud-init.yaml,
# btb-backup.service). The container pins postgresql-client-17 to match the Supabase server
# major (supabase/config.toml major_version = 17); if the base image ever drifts (unpinned
# upgrade, distro change), an older pg_dump client refuses to run against a newer server with
# an opaque "server version mismatch" mid-backup. This guard fails the unit with a readable
# line BEFORE anything is dumped.
#
# Usage:
#   sh scripts/pg_client_version_check.sh [postgres_url]
# With no argument it resolves the URL from config.yaml (backend.web.settings), so it behaves
# for both entrypoints and manual runs inside the container.

set -eu

CLIENT_MAJOR="$(psql --version 2>/dev/null | sed -E 's/^psql \(PostgreSQL\) ([0-9]+).*/\1/')"
if [ -z "${CLIENT_MAJOR:-}" ]; then
  echo "pg_client_version_check: psql not found in the container; cannot verify pg tooling" >&2
  exit 1
fi

URL="${1:-}"
if [ -z "$URL" ]; then
  URL="$(python3 - <<'PY'
import os
from backend.ops.state_backup import postgres_url
from backend.web.settings import load_config

url = postgres_url(str(load_config(os.environ.get("BTB_CONFIG", "config.yaml"))["web"]["database_url"]))
print(url or "")
PY
)"
fi

if [ -z "$URL" ]; then
  echo "pg_client_version_check: no external Postgres configured; nothing to check"
  exit 0
fi

SERVER_MAJOR="$(psql --no-align --tuples-only --command='SHOW server_version' "$URL" 2>/dev/null | awk -F. '{print $1}' | tr -d '[:space:]')"
if [ -z "$SERVER_MAJOR" ]; then
  echo "pg_client_version_check: could not reach the server to read server_version; failing the backup" >&2
  exit 1
fi

if [ "$CLIENT_MAJOR" -lt "$SERVER_MAJOR" ]; then
  echo "pg_client_version_check: FAIL client psql/pg_dump major $CLIENT_MAJOR < server major $SERVER_MAJOR (${URL##*@}); " \
       "rebuild the image with postgresql-client-$SERVER_MAJOR or the nightly dump will fail" >&2
  exit 1
fi

echo "pg_client_version_check: OK client major $CLIENT_MAJOR >= server major $SERVER_MAJOR (${URL##*@})"
