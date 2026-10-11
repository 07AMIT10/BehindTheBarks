"""Back up and restore the app's on-disk state (SQLite DB, event logs, profile, tuning.json) to R2.

Why: a reclaimed or replaced VM takes its disk with it. With this, a fresh VM restores in minutes.
An external Postgres (BTB_DATABASE_URL) is not part of the archive; back that up at the provider.

    python -m backend.ops.state_backup backup --keep 14
    python -m backend.ops.state_backup list
    python -m backend.ops.state_backup restore            # latest archive into the configured paths
    python -m backend.ops.state_backup restore --key backups/btb/btb-state-....tar.gz --force

Run inside the container (the Dockerfile copies backend/), so the paths match the running app.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Callable

from backend.store.storage import build_r2
from backend.web.settings import load_config

ARCHIVE_PREFIX = "btb-state-"
DEFAULT_LABEL = "btb"
DB_MEMBER = "db/btb.db"
PG_MEMBER = "db/postgres.dump"
STATE_MEMBER = "state"


def sqlite_path(database_url: str) -> Path | None:
    """The file behind a sqlite:/// URL, or None for any other database (Postgres is external)."""
    if not database_url.startswith("sqlite:///"):
        return None
    return Path(database_url[len("sqlite:///"):])


def postgres_url(database_url: str) -> str | None:
    """A libpq-style URL for pg_dump/pg_restore, or None for SQLite. Strips SQLAlchemy's +driver suffix."""
    if not database_url.startswith(("postgresql", "postgres")):
        return None
    scheme, rest = database_url.split("://", 1)
    return f"postgresql://{rest}" if scheme.startswith("postgresql") or scheme == "postgres" else None


def create_archive(log_dir: Path, db_path: Path | None, out_path: Path, *,
                   pg_url: str | None = None, runner: Callable[..., Any] = subprocess.run) -> Path:
    """Write a .tar.gz with the log directory and a consistent copy of the database.

    SQLite: an online-backup copy. Postgres (Supabase): a pg_dump custom-format file. pg_dump must be
    at least the server's major version, or it refuses to run."""
    skip: set[Path] = set()
    with tempfile.TemporaryDirectory() as tmp:
        staged_db = None
        if db_path is not None and db_path.exists():
            # The online backup API copies a live database consistently, even mid-write.
            staged_db = Path(tmp) / "btb.db"
            src = sqlite3.connect(str(db_path))
            try:
                dst = sqlite3.connect(str(staged_db))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            for suffix in ("", "-wal", "-shm"):
                skip.add(Path(str(db_path.resolve()) + suffix))
        with tarfile.open(out_path, "w:gz") as tar:
            if log_dir.is_dir():
                for path in sorted(log_dir.rglob("*")):
                    if path.is_dir() or path.resolve() in skip or path.suffix == ".tmp":
                        continue
                    tar.add(path, arcname=f"{STATE_MEMBER}/{path.relative_to(log_dir).as_posix()}")
            if staged_db is not None:
                tar.add(staged_db, arcname=DB_MEMBER)
            if pg_url is not None:
                dump = Path(tmp) / "postgres.dump"
                # The URL is passed as an argument; on a single-operator VM that is acceptable.
                runner(["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                        f"--file={dump}", f"--dbname={pg_url}"], check=True)
                tar.add(dump, arcname=PG_MEMBER)
    return out_path


def restore_archive(archive: Path, log_dir: Path, db_path: Path | None, *, force: bool = False,
                    pg_url: str | None = None, runner: Callable[..., Any] = subprocess.run) -> list[str]:
    """Unpack an archive into the configured paths. Refuses to overwrite an existing database unless forced."""
    restored: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, filter="data")  # rejects absolute paths and links that escape tmp
        root = Path(tmp)
        staged_state = root / STATE_MEMBER
        if staged_state.is_dir():
            log_dir.mkdir(parents=True, exist_ok=True)
            for path in sorted(staged_state.rglob("*")):
                if path.is_dir():
                    continue
                dest = log_dir / path.relative_to(staged_state)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                restored.append(str(dest))
        staged_db = root / DB_MEMBER
        if staged_db.exists() and db_path is not None:
            if db_path.exists() and not force:
                raise FileExistsError(f"{db_path} already exists; pass --force to replace it")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("-wal", "-shm"):  # stale journal files would corrupt the restored copy
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            shutil.copy2(staged_db, db_path)
            restored.append(str(db_path))
        staged_dump = root / PG_MEMBER
        if staged_dump.exists() and pg_url is not None:
            if not force:
                raise FileExistsError("the archive holds a Postgres dump; pass --force to restore it over the database")
            # --clean/--if-exists drops and recreates what the dump contains, so it replaces the data.
            runner(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
                    f"--dbname={pg_url}", str(staged_dump)], check=True)
            restored.append(f"postgres ({pg_url.split('@')[-1]})")
    return restored


def archive_name(now: dt.datetime, label: str = DEFAULT_LABEL) -> str:
    return f"backups/{label}/{ARCHIVE_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"


def upload_and_prune(r2: Any, archive: Path, key: str, keep: int) -> list[str]:
    """Upload the archive, then delete the oldest archives under the same label beyond `keep`."""
    r2.put_file(archive, key, content_type="application/gzip")
    prefix = key.rsplit("/", 1)[0] + "/"
    existing = [k for k, _ in r2.list_keys(prefix) if Path(k).name.startswith(ARCHIVE_PREFIX)]
    deleted: list[str] = []
    if keep > 0:
        for old in existing[:-keep]:
            r2.delete_key(old)
            deleted.append(old)
    return deleted


def _r2_or_exit() -> Any:
    r2 = build_r2(load_config(os.environ.get("BTB_CONFIG", "config.yaml")))
    if r2 is None:
        print("R2 is not configured (BTB_R2_* in the environment).", file=sys.stderr)
        raise SystemExit(2)
    return r2


def _paths() -> tuple[Path, Path | None, str | None]:
    cfg = load_config(os.environ.get("BTB_CONFIG", "config.yaml"))
    url = str(cfg["web"]["database_url"])
    return Path(str(cfg["web"]["log_dir"])), sqlite_path(url), postgres_url(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup", help="archive the state and upload it to R2")
    b.add_argument("--keep", type=int, default=14, help="archives to keep under this label (0 = keep all)")
    sub.add_parser("list", help="list archives in R2")
    r = sub.add_parser("restore", help="download an archive and unpack it into the configured paths")
    r.add_argument("--key", help="archive key (default: the newest)")
    r.add_argument("--force", action="store_true", help="replace an existing database")
    args = parser.parse_args(argv)

    label = os.environ.get("BTB_BACKUP_LABEL", DEFAULT_LABEL)
    prefix = f"backups/{label}/"
    if args.cmd == "backup":
        r2 = _r2_or_exit()
        log_dir, db_path, pg_url = _paths()
        key = archive_name(dt.datetime.now(dt.timezone.utc), label)
        with tempfile.TemporaryDirectory() as tmp:
            archive = create_archive(log_dir, db_path, Path(tmp) / Path(key).name, pg_url=pg_url)
            print(f"archived {archive.stat().st_size} bytes -> {key}")
            deleted = upload_and_prune(r2, archive, key, args.keep)
        for old in deleted:
            print(f"pruned {old}")
        return 0
    if args.cmd == "list":
        for key, modified in _r2_or_exit().list_keys(prefix):
            print(f"{modified:%Y-%m-%d %H:%M:%S} UTC  {key}")
        return 0
    # restore
    r2 = _r2_or_exit()
    key = args.key
    if not key:
        found = [k for k, _ in r2.list_keys(prefix) if Path(k).name.startswith(ARCHIVE_PREFIX)]
        if not found:
            print(f"no archives under {prefix}", file=sys.stderr)
            return 1
        key = found[-1]
    log_dir, db_path, pg_url = _paths()
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / Path(key).name
        r2.get_file(key, local)
        try:
            restored = restore_archive(local, log_dir, db_path, force=args.force, pg_url=pg_url)
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    print(f"restored from {key}:")
    for path in restored:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
