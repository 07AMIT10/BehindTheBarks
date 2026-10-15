"""Engine/session plumbing and secret hashing for the household store."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase

SCHEMA_VERSION = 1


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """UUID4 hex: opaque, unguessable, and safe as a primary key across sqlite and Postgres."""
    return uuid.uuid4().hex


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def equal_digest(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def new_token() -> str:
    """A viewer session credential: 256 bits, url-safe."""
    return secrets.token_urlsafe(32)


def new_pairing_code() -> str:
    """A pairing code is one-time and short-lived: 128 bits is plenty (brute force loses to the TTL)."""
    return secrets.token_urlsafe(16)


def normalize_db_url(url: str) -> str:
    """Normalize database connection URLs across dialects and drivers.

    Handles:
    - postgres:// -> postgresql:// (Render, Supabase, Heroku compatibility)
    - postgresql:// -> postgresql+psycopg:// when psycopg 3 is installed and no driver is specified
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        try:
            import psycopg  # noqa: F401
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        except ImportError:
            pass
    return url


def _ensure_sqlite_parent_dir(url: str) -> None:
    """Create the directory holding a file-backed SQLite database (fresh clones and CI have no out/).

    Handles `sqlite:///relative/x.db`, `sqlite:////abs/x.db` and `sqlite://` / `:memory:` (no file).
    """
    from sqlalchemy.engine import make_url

    database = make_url(url).database
    if not database or database == ":memory:" or database.startswith("file:"):
        return
    parent = os.path.dirname(os.path.abspath(database))
    os.makedirs(parent, exist_ok=True)


def make_engine(url: str, **kwargs: Any) -> Engine:
    """An engine configured per dialect: SQLite needs foreign keys and WAL, Postgres has pooling."""
    url = normalize_db_url(url)
    if url.startswith("sqlite"):
        _ensure_sqlite_parent_dir(url)
        kwargs.setdefault("connect_args", {})
        kwargs["connect_args"].setdefault("check_same_thread", False)
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001 - sqlalchemy event signature
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")  # concurrent readers while a writer works
                cur.execute("PRAGMA synchronous=NORMAL")  # crash-safe against abrupt power cuts
            cur.close()

        return engine

    # PostgreSQL (Supabase, Neon, Render): connection liveness checking and recycling
    kwargs.setdefault("pool_pre_ping", True)
    kwargs.setdefault("pool_recycle", 300)
    return create_engine(url, **kwargs)


def init_db(engine: Engine) -> None:
    """Create tables (no Alembic in v1) and stamp the schema version.

    To migrate later: add a step that compares `schema_version` in `meta` against the code's
    SCHEMA_VERSION and applies an ordered list of `ALTER TABLE` statements, then re-stamps it.
    """
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"))
        # portable upsert (INSERT OR REPLACE is SQLite-only; BTB_DATABASE_URL may point at Postgres)
        conn.execute(text("DELETE FROM meta WHERE key = 'schema_version'"))
        conn.execute(text("INSERT INTO meta (key, value) VALUES ('schema_version', :v)"),
                     {"v": str(SCHEMA_VERSION)})
