#!/usr/bin/env python3
"""Migrate SQLite data to Supabase PostgreSQL for WagWatch.

Usage (preferred):
  MIGRATION_DATABASE_URL="postgresql://..." python3 scripts/migrate_sqlite_to_supabase.py [sqlite_path]

Deprecated (still accepted; the URL is visible in `ps` and shell history):
  python3 scripts/migrate_sqlite_to_supabase.py [sqlite_path] [postgres_url]

Env fallbacks for the target URL (kept for existing automations): BTB_DATABASE_URL,
then DATABASE_URL. Copies all 6 ORM tables and verifies row-count parity per table
at the end (exits 1 and lists all counts on any mismatch).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.store.db import init_db
from backend.store.models import (AppSetting, Household, HouseholdTelegramSubscriber,
                                 PairingCode, Station, Viewer)

# (table name, model, primary-key column) in copy order; households must exist before the rest.
TABLES = [
    ("households", Household, "id"),
    ("viewers", Viewer, "id"),
    ("stations", Station, "id"),
    ("pairing_codes", PairingCode, "code_hash"),
    ("app_settings", AppSetting, "key"),
    ("household_telegram_subscribers", HouseholdTelegramSubscriber, "id"),
]


def sqlite_count(src: sqlite3.Connection, table: str) -> int:
    return int(src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def pg_count(session: Session, table: str) -> int:
    model = next(m for name, m, _ in TABLES if name == table)
    return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def migrate(sqlite_path: str, pg_url: str) -> int:
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

    print(f"Connecting to SQLite: {sqlite_path}")
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    print("Connecting to PostgreSQL / Supabase...")
    engine = create_engine(pg_url)
    init_db(engine)

    with Session(engine) as session:
        # 1. Households
        h_rows = src.execute("SELECT * FROM households").fetchall()
        for r in h_rows:
            existing = session.get(Household, r["id"])
            if not existing:
                session.add(
                    Household(
                        id=r["id"],
                        name=r["name"],
                        dog_name=r["dog_name"],
                        location=r["location"],
                        recovery_hash=r["recovery_hash"],
                        llm_enabled=bool(r["llm_enabled"]),
                        log_enabled=bool(r["log_enabled"]),
                        created_at=r["created_at"],
                        deleted_at=r["deleted_at"],
                    )
                )
        session.commit()
        print(f"Migrated {len(h_rows)} households")

        # 2. Viewers
        v_rows = src.execute("SELECT * FROM viewers").fetchall()
        for r in v_rows:
            existing = session.get(Viewer, r["id"])
            if not existing:
                session.add(
                    Viewer(
                        id=r["id"],
                        household_id=r["household_id"],
                        role=r["role"],
                        label=r["label"],
                        token_hash=r["token_hash"],
                        mode_ceiling=r["mode_ceiling"],
                        expires_at=r["expires_at"],
                        revoked_at=r["revoked_at"],
                        created_at=r["created_at"],
                        last_seen=r["last_seen"],
                    )
                )
        session.commit()
        print(f"Migrated {len(v_rows)} viewers")

        # 3. Stations
        s_rows = src.execute("SELECT * FROM stations").fetchall()
        for r in s_rows:
            existing = session.get(Station, r["id"])
            if not existing:
                session.add(
                    Station(
                        id=r["id"],
                        household_id=r["household_id"],
                        name=r["name"],
                        dog_name=r["dog_name"],
                        pubkey_spki=r["pubkey_spki"],
                        created_at=r["created_at"],
                        revoked_at=r["revoked_at"],
                        last_seen=r["last_seen"],
                    )
                )
        session.commit()
        print(f"Migrated {len(s_rows)} stations")

        # 4. Pairing codes
        p_rows = src.execute("SELECT * FROM pairing_codes").fetchall()
        for r in p_rows:
            existing = session.get(PairingCode, r["code_hash"])
            if not existing:
                session.add(
                    PairingCode(
                        code_hash=r["code_hash"],
                        household_id=r["household_id"],
                        kind=r["kind"],
                        expires_at=r["expires_at"],
                        used_at=r["used_at"],
                        created_by_viewer=r["created_by_viewer"],
                        mode_ceiling=r["mode_ceiling"],
                        viewer_label=r["viewer_label"],
                    )
                )
        session.commit()
        print(f"Migrated {len(p_rows)} pairing codes")

        # 5. App settings
        a_rows = src.execute("SELECT * FROM app_settings").fetchall()
        for r in a_rows:
            existing = session.get(AppSetting, r["key"])
            if not existing:
                session.add(
                    AppSetting(
                        key=r["key"],
                        value=r["value"],
                        updated_at=r["updated_at"],
                    )
                )
        session.commit()
        print(f"Migrated {len(a_rows)} app settings")

        # 6. Telegram subscribers ( HouseholdTelegramSubscriber, previously skipped )
        t_rows = src.execute("SELECT * FROM household_telegram_subscribers").fetchall()
        for r in t_rows:
            existing = session.get(HouseholdTelegramSubscriber, r["id"])
            if not existing:
                session.add(
                    HouseholdTelegramSubscriber(
                        id=r["id"],
                        household_id=r["household_id"],
                        chat_id=r["chat_id"],
                        chat_type=r["chat_type"],
                        title=r["title"],
                        label=r["label"],
                        notify_barks=bool(r["notify_barks"]),
                        notify_distress=bool(r["notify_distress"]),
                        notify_battery=bool(r["notify_battery"]),
                        created_at=r["created_at"],
                    )
                )
        session.commit()
        print(f"Migrated {len(t_rows)} telegram subscribers")

        # Row-count parity: every table must match exactly (SQLite is the source of truth).
        mismatches: list[tuple[str, int, int]] = []
        print("\nRow-count parity check:")
        for table, _model, _pk in TABLES:
            s_cnt, pg_cnt = sqlite_count(src, table), pg_count(session, table)
            ok = "OK " if s_cnt == pg_cnt else "MISMATCH"
            print(f"  {ok} {table}: sqlite={s_cnt} postgres={pg_cnt}")
            if s_cnt != pg_cnt:
                mismatches.append((table, s_cnt, pg_cnt))
        if mismatches:
            print("\nERROR: row-count parity failed for:")
            for table, s_cnt, pg_cnt in mismatches:
                print(f"  {table}: sqlite={s_cnt} postgres={pg_cnt}")
            print("Fix the failing tables and rerun the migration before switching to Supabase.")
            return 1

    print("\nMigration to Supabase completed successfully!")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_path", nargs="?", default="out/btb.db",
                        help="source SQLite file (default: out/btb.db)")
    parser.add_argument("database_url_positional", nargs="?", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--database-url", "-d", dest="database_url_flag", default=None,
                        help="Postgres/Supabase URL; prefer the MIGRATION_DATABASE_URL env var "
                             "so the password is not visible in ps")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if args.database_url_positional is not None:
        print("WARNING: passing the database URL as a positional argument is deprecated and will "
              "be removed; use MIGRATION_DATABASE_URL=... python scripts/migrate_sqlite_to_supabase.py "
              "(the positional URL shows the password in `ps` and shell history).",
              file=sys.stderr)
        dest_url = args.database_url_positional
    else:
        dest_url = (args.database_url_flag or os.getenv("MIGRATION_DATABASE_URL")
                    or os.getenv("BTB_DATABASE_URL") or os.getenv("DATABASE_URL"))
    if not dest_url:
        print("Error: set MIGRATION_DATABASE_URL (preferred), BTB_DATABASE_URL, or DATABASE_URL; "
              "the deprecated positional argument is still accepted.")
        sys.exit(1)
    sys.exit(migrate(args.sqlite_path, dest_url))
