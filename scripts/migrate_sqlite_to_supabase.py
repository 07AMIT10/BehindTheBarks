#!/usr/bin/env python3
"""Migrate SQLite data to Supabase PostgreSQL for WagWatch.

Usage:
  python3 scripts/migrate_sqlite_to_supabase.py [sqlite_path] [postgres_url]
"""

from __future__ import annotations

import os
import sqlite3
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.store.db import init_db
from backend.store.models import AppSetting, Household, PairingCode, Station, Viewer


def migrate(sqlite_path: str, pg_url: str) -> None:
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

    print("Migration to Supabase completed successfully!")


if __name__ == "__main__":
    sqlite_file = sys.argv[1] if len(sys.argv) > 1 else "out/btb.db"
    dest_url = (
        sys.argv[2]
        if len(sys.argv) > 2
        else os.getenv("BTB_DATABASE_URL") or os.getenv("DATABASE_URL")
    )
    if not dest_url:
        print("Error: BTB_DATABASE_URL or DATABASE_URL environment variable (or argument 2) is required.")
        sys.exit(1)
    migrate(sqlite_file, dest_url)
