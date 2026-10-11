"""Unit tests for PostgreSQL URL normalization and engine configuration (Supabase / Render)."""

from backend.store.db import make_engine, normalize_db_url


def test_normalize_db_url_handles_legacy_postgres_scheme():
    legacy_url = "postgres://postgres:secret@db.projectref.supabase.co:5432/postgres"
    norm = normalize_db_url(legacy_url)
    assert norm.startswith("postgresql+psycopg://")
    assert "db.projectref.supabase.co:5432/postgres" in norm


def test_normalize_db_url_maps_postgresql_to_psycopg3():
    standard_url = "postgresql://postgres:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    norm = normalize_db_url(standard_url)
    assert norm.startswith("postgresql+psycopg://")
    assert "pooler.supabase.com:6543/postgres" in norm


def test_normalize_db_url_preserves_sqlite():
    sqlite_url = "sqlite:///out/btb.db"
    assert normalize_db_url(sqlite_url) == sqlite_url
    assert normalize_db_url("sqlite:///:memory:") == "sqlite:///:memory:"


def test_make_engine_configures_postgres_pooling():
    url = "postgres://user:pass@db.ref.supabase.co:5432/postgres"
    engine = make_engine(url)
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    assert engine.pool._pre_ping is True
    assert engine.pool._recycle == 300


def test_make_engine_preserves_sqlite_behavior():
    engine = make_engine("sqlite:///:memory:")
    assert engine.dialect.name == "sqlite"
