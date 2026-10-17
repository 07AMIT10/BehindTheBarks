from backend.store import db as store_db


def test_sqlite_engine_creates_missing_parent_directories(tmp_path):
    # A fresh checkout or container has no out/ yet: the first start must not fail on the database file.
    db_file = tmp_path / "out" / "nested" / "btb.db"
    engine = store_db.make_engine(f"sqlite:///{db_file}")
    store_db.init_db(engine)
    assert db_file.exists()
