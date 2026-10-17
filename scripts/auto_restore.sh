#!/usr/bin/env sh
# Auto-restore bootstrap wrapper, called by the Dockerfile CMD when BTB_AUTO_RESTORE=1.
#
# Why this exists: backend.ops.state_backup's `restore` CLI has no "skip the database" flag. It
# always applies every db/** member it finds in the archive, so the old entrypoint
# (`python -m backend.ops.state_backup restore 2>/dev/null || true`) had two bad behaviors:
#   * For Supabase/Postgres the CLI refuses without --force whenever a Postgres dump is present
#     (even on an empty database), the failure was discarded by 2>/dev/null, and the auto-restore
#     silently never restored anything for Render;
#   * For SQLite on an ephemeral disk (Render free tier), every cold start silently rolled the
#     database back to the previous night's 03:15 archive.
# (Plus restore output was invisible either way.)
#
# This wrapper adds the missing policy without touching backend/**:
#   * default:  restore only state/** (event logs, profile, tuning.json) from the newest archive
#               and SKIP every db/** member, with an explicit skip line;
#   * BTB_RESTORE_DB=1: perform the full restore, database included (the --force the CLI demands
#               is implied by this explicit opt-in).
# All output goes to stdout/stderr so docker/Render logs show exactly what happened.
#
# Environment:
#   BTB_AUTO_RESTORE  must be 1 (the Dockerfile gates the call itself); manual invocations
#                     without it are refused.
#   BTB_RESTORE_DB    1 = also restore db/** (backend SQLite database and/or Supabase pg dump).
#   BTB_RESTORE_KEY   optional explicit R2 key; default is the newest archive under the label.
#   BTB_BACKUP_LABEL  archive label (default: btb).
#
# NOTE: intentionally derives from backend.ops.state_backup internals (_r2_or_exit/_paths/
# restore_archive) instead of duplicating R2/IO logic; if the backend agent reshapes that CLI,
# this wrapper must be updated in the same change.

set -eu

if [ "${BTB_AUTO_RESTORE:-}" != "1" ]; then
  echo "auto_restore: BTB_AUTO_RESTORE != 1; refusing to restore (call with BTB_AUTO_RESTORE=1)" >&2
  exit 0
fi

exec python3 <<'PY'
import os
import sys
import tarfile
import tempfile
from pathlib import Path

from backend.ops import state_backup as sb

restore_db_flag = os.environ.get("BTB_RESTORE_DB", "") == "1"
r2 = sb._r2_or_exit()
label = os.environ.get("BTB_BACKUP_LABEL", sb.DEFAULT_LABEL)
prefix = f"backups/{label}/"

key = os.environ.get("BTB_RESTORE_KEY") or None
if not key:
    found = [k for k, _ in r2.list_keys(prefix) if Path(k).name.startswith(sb.ARCHIVE_PREFIX)]
    if not found:
        print(f"auto_restore: no archives under {prefix}; starting with an empty state directory")
        sys.exit(0)
    key = found[-1]
print(f"auto_restore: restoring from {key}")

log_dir, db_path, pg_url = sb._paths()

with tempfile.TemporaryDirectory() as tmp:
    local = Path(tmp) / Path(key).name
    r2.get_file(key, local)

    # Inspect the archive so the decision is visible in the logs before anything is unpacked.
    with tarfile.open(local, "r:gz") as tar:
        members = tar.getnames()
    db_members = sorted(n for n in members if n in (sb.DB_MEMBER, sb.PG_MEMBER))
    state_members = sum(1 for n in members if n.startswith(f"{sb.STATE_MEMBER}/"))

    if restore_db_flag:
        if db_members:
            print(f"auto_restore: BTB_RESTORE_DB=1 -> restoring {', '.join(db_members)} "
                  "(the --force the CLI needs is implied by this explicit opt-in)")
        else:
            print("auto_restore: BTB_RESTORE_DB=1, but the archive holds no db/ members")
        restored = sb.restore_archive(local, log_dir, db_path, force=True, pg_url=pg_url)
    else:
        if db_members:
            print(f"auto_restore: SKIPPING {', '.join(db_members)} (state-only bootstrap; set "
                  "BTB_RESTORE_DB=1 to also restore the database)")
        else:
            print("auto_restore: archive holds no db/ members; restoring state only")
        # db_path=None and pg_url=None pin the restore to state/** — db members are never unpacked.
        restored = sb.restore_archive(local, log_dir, None, force=False, pg_url=None)
    print(f"auto_restore: {state_members} state file(s) in archive; restored {len(restored)} path(s):")
    for path in restored:
        print(f"  {path}")
PY
