import datetime as dt
import sqlite3
import tarfile

import pytest

from backend.ops.state_backup import (ARCHIVE_PREFIX, archive_name, create_archive, restore_archive,
                                      sqlite_path, upload_and_prune)


def _state(root):
    """A log dir with the files the app keeps, and a live SQLite database inside it."""
    log = root / "out"
    (log / "household-a").mkdir(parents=True)
    (log / "tuning.json").write_text('{"video_fps": 2.0}')
    (log / "household-a" / "session.jsonl").write_text('{"t": 1}\n')
    (log / "scratch.tmp").write_text("partial")
    db = log / "btb.db"
    con = sqlite3.connect(db)
    con.execute("create table household (id text)")
    con.execute("insert into household values ('h1')")
    con.commit()
    con.close()
    return log, db


def test_archive_holds_state_and_a_consistent_db_copy_but_not_the_live_files(tmp_path):
    log, db = _state(tmp_path)
    archive = create_archive(log, db, tmp_path / "a.tar.gz")
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert "state/tuning.json" in names
    assert "state/household-a/session.jsonl" in names
    assert "db/btb.db" in names
    assert "state/btb.db" not in names and "state/btb.db-wal" not in names
    assert "state/scratch.tmp" not in names


def test_restore_rebuilds_files_and_database(tmp_path):
    log, db = _state(tmp_path)
    archive = create_archive(log, db, tmp_path / "a.tar.gz")

    new_log = tmp_path / "fresh" / "out"
    new_db = new_log / "btb.db"
    restored = restore_archive(archive, new_log, new_db)
    assert (new_log / "tuning.json").read_text() == '{"video_fps": 2.0}'
    assert (new_log / "household-a" / "session.jsonl").exists()
    assert str(new_db) in restored
    rows = sqlite3.connect(new_db).execute("select id from household").fetchall()
    assert rows == [("h1",)]


def test_restore_refuses_to_overwrite_a_database_unless_forced(tmp_path):
    log, db = _state(tmp_path)
    archive = create_archive(log, db, tmp_path / "a.tar.gz")
    with pytest.raises(FileExistsError):
        restore_archive(archive, log, db)  # the database is already there
    restore_archive(archive, log, db, force=True)


def test_upload_keeps_only_the_newest_archives_under_the_label(tmp_path):
    class FakeR2:
        def __init__(self):
            self.objects = {f"backups/btb/{ARCHIVE_PREFIX}2026100{i}T000000Z.tar.gz": dt.datetime(2026, 10, i, tzinfo=dt.timezone.utc)
                            for i in range(1, 4)}
            self.objects["backups/other/keep-me.tar.gz"] = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
            self.deleted = []
            self.put = []

        def put_file(self, path, key, content_type):
            self.put.append(key)
            self.objects[key] = dt.datetime(2026, 10, 9, tzinfo=dt.timezone.utc)

        def list_keys(self, prefix):
            items = [(k, m) for k, m in self.objects.items() if k.startswith(prefix)]
            return sorted(items, key=lambda kv: kv[1])

        def delete_key(self, key):
            self.deleted.append(key)
            del self.objects[key]

    r2 = FakeR2()
    archive = tmp_path / "x.tar.gz"
    archive.write_bytes(b"x")
    new_key = archive_name(dt.datetime(2026, 10, 9, 12, tzinfo=dt.timezone.utc))
    deleted = upload_and_prune(r2, archive, new_key, keep=2)
    assert len(deleted) == 2  # four archives under the label, two kept
    assert "backups/other/keep-me.tar.gz" in r2.objects  # another label is never touched
    assert new_key in r2.objects


def test_archive_members_cannot_escape_the_restore_directory(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escaped.txt")
    with pytest.raises(Exception):
        restore_archive(evil, tmp_path / "out", None)
    assert not (tmp_path / "escaped.txt").exists()


def test_only_sqlite_urls_are_backed_up_locally(tmp_path):
    assert sqlite_path("sqlite:///out/btb.db").as_posix() == "out/btb.db"
    assert sqlite_path("postgresql://user:pw@host/db") is None


def test_postgres_urls_drop_the_sqlalchemy_driver_suffix():
    from backend.ops.state_backup import postgres_url
    assert postgres_url("postgresql+psycopg://u:p@db.example:5432/postgres") == "postgresql://u:p@db.example:5432/postgres"
    assert postgres_url("postgres://u:p@h/db") == "postgresql://u:p@h/db"
    assert postgres_url("sqlite:///out/btb.db") is None


def test_postgres_archive_runs_pg_dump_and_restore_runs_pg_restore(tmp_path):
    from backend.ops.state_backup import PG_MEMBER, create_archive, restore_archive
    calls = []

    def fake_runner(cmd, check):
        calls.append(cmd)
        if cmd[0] == "pg_dump":
            out = [a.split("=", 1)[1] for a in cmd if a.startswith("--file=")][0]
            open(out, "wb").write(b"PGDMP-fake")

    log = tmp_path / "out"
    log.mkdir()
    (log / "tuning.json").write_text("{}")
    archive = create_archive(log, None, tmp_path / "a.tar.gz", pg_url="postgresql://u@h/db", runner=fake_runner)
    with tarfile.open(archive) as tar:
        assert PG_MEMBER in tar.getnames()
    assert calls[0][0] == "pg_dump" and "--format=custom" in calls[0] and "--dbname=postgresql://u@h/db" in calls[0]

    with pytest.raises(FileExistsError):
        restore_archive(archive, tmp_path / "new", None, pg_url="postgresql://u@h/db", runner=fake_runner)
    restore_archive(archive, tmp_path / "new", None, force=True, pg_url="postgresql://u@h/db", runner=fake_runner)
    assert calls[-1][0] == "pg_restore" and "--clean" in calls[-1] and calls[-1][-1].endswith("postgres.dump")
