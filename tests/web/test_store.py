"""The household store (T2.1): SQLAlchemy models, an atomic pairing-code consume, and hashed secrets.

In-memory SQLite; every secret is stored hashed, so the plaintext must never appear in any column.
"""

import json
import threading
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.store import db, repo


@pytest.fixture()
def session():
    # StaticPool: an in-memory SQLite database is per-connection unless the pool shares one, which
    # is exactly what the concurrent-consume test needs.
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}, future=True)
    db.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def row_dump(session) -> str:
    """Every text/blob column of every table, as one string: plaintext secrets must not appear here."""
    out = {}
    for table in ("households", "viewers", "stations", "pairing_codes"):
        for row in session.execute(text(f"SELECT * FROM {table}")).mappings():
            out[f"{table}:{row['id' if 'id' in row else 'code_hash']}"] = dict(row)
    return json.dumps(out, default=str)


def test_household_and_viewer_roundtrip(session):
    h, key = repo.create_household(session, name="Home", dog_name="Rex", location="Living room")
    assert h.id and h.dog_name == "Rex" and key and h.llm_enabled is False and h.log_enabled is False
    v, token = repo.create_viewer(session, household_id=h.id, role="owner", label="mittens")
    assert v.role == "owner" and v.mode_ceiling == "full"
    found = repo.viewer_by_token(session, token)
    assert found is not None and found.id == v.id
    assert repo.viewer_by_token(session, "not-a-token") is None


def test_revoked_and_expired_viewers_return_none(session):
    h, _key = repo.create_household(session, name="Home")
    v, token = repo.create_viewer(session, household_id=h.id, role="viewer", label="v")
    repo.revoke_viewer(session, v.id)
    assert repo.viewer_by_token(session, token) is None

    v2, token2 = repo.create_viewer(session, household_id=h.id, role="viewer", label="temp",
                                    expires_at=time.time() - 1)
    assert repo.viewer_by_token(session, token2) is None


def test_pairing_code_is_single_use(session):
    h, _key = repo.create_household(session, name="Home")
    v, _ = repo.create_viewer(session, household_id=h.id, role="owner")
    code = repo.create_pairing_code(session, household_id=h.id, kind="station", created_by_viewer=v.id)
    first = repo.consume_pairing_code(session, code, kind="station")
    assert first is not None and first.household_id == h.id
    assert repo.consume_pairing_code(session, code, kind="station") is None  # used_at is set


def test_expired_pairing_code_fails(session, monkeypatch):
    h, _key = repo.create_household(session, name="Home")
    v, _ = repo.create_viewer(session, household_id=h.id, role="owner")
    code = repo.create_pairing_code(session, household_id=h.id, kind="station", created_by_viewer=v.id)
    monkeypatch.setattr(repo, "_now", lambda: time.time() + 10_000)  # past the 300 s TTL
    assert repo.consume_pairing_code(session, code, kind="station") is None


def test_kind_mismatch_fails(session):
    h, _key = repo.create_household(session, name="Home")
    v, _ = repo.create_viewer(session, household_id=h.id, role="owner")
    code = repo.create_pairing_code(session, household_id=h.id, kind="station", created_by_viewer=v.id)
    assert repo.consume_pairing_code(session, code, kind="viewer") is None  # kind must match


def test_concurrent_consume_has_exactly_one_winner(tmp_path):
    # A file DB, like production: SQLite serialises the two writers, which is what makes the atomic
    # UPDATE the single-use guard. (An in-memory StaticPool hands one shared connection to both
    # threads and cannot model this.)
    engine = create_engine(f"sqlite:///{tmp_path}/btb.db", future=True)
    db.Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, future=True)
    s = make()
    h, _key = repo.create_household(s, name="Home")
    v, _ = repo.create_viewer(s, household_id=h.id, role="owner")
    code = repo.create_pairing_code(s, household_id=h.id, kind="station", created_by_viewer=v.id)
    s.commit()

    winners = []
    barrier = threading.Barrier(2)

    def try_consume():
        sess = make()
        barrier.wait()
        if repo.consume_pairing_code(sess, code, kind="station") is not None:
            winners.append(sess)
        sess.close()

    threads = [threading.Thread(target=try_consume) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1


def test_station_roundtrip_and_revoke(session):
    h, _key = repo.create_household(session, name="Home")
    st = repo.create_station(session, household_id=h.id, name="Pixel 8", pubkey_spki=b"\x30\x59\x30\x13")
    assert repo.station_by_id(session, st.id).id == st.id
    repo.revoke_station(session, st.id)
    assert repo.station_by_id(session, st.id).revoked_at is not None
    assert repo.station_by_id(session, "missing") is None


def test_delete_household_cascades(session):
    h, _key = repo.create_household(session, name="Home")
    v, _ = repo.create_viewer(session, household_id=h.id, role="owner")
    repo.create_station(session, household_id=h.id, name="Phone", pubkey_spki=b"key")
    repo.create_pairing_code(session, household_id=h.id, kind="station", created_by_viewer=v.id)
    repo.delete_household(session, h.id)
    assert repo.get_household(session, h.id) is None
    assert session.execute(text("SELECT COUNT(*) FROM viewers")).scalar_one() == 0
    assert session.execute(text("SELECT COUNT(*) FROM stations")).scalar_one() == 0
    assert session.execute(text("SELECT COUNT(*) FROM pairing_codes")).scalar_one() == 0


def test_plaintext_secrets_never_reach_the_database(session):
    h, _key = repo.create_household(session, name="Home")
    viewer, viewer_token = repo.create_viewer(session, household_id=h.id, role="owner")
    station = repo.create_station(session, household_id=h.id, name="Phone", pubkey_spki=b"pubkey-bytes")
    code = repo.create_pairing_code(session, household_id=h.id, kind="station", created_by_viewer=viewer.id)
    repo.recover_household(session, "recovery-key-plaintext")  # no such household: must not raise
    session.commit()

    dump = row_dump(session)
    for secret in (viewer_token, code, "recovery-key-plaintext"):
        assert secret not in dump, secret
    assert viewer.token_hash != viewer_token and viewer.token_hash == db.sha256_hex(viewer_token)
    assert h.recovery_hash and h.id != viewer.id
    assert station.pubkey_spki == b"pubkey-bytes"  # the pubkey is public by design


def test_recovery_key_roundtrip(session):
    h, key = repo.create_household(session, name="Home", recovery_key="recovery-key-plaintext")
    global_key = key
    viewer, token = repo.recover_household(session, "recovery-key-plaintext")
    assert viewer.household_id == h.id and viewer.role == "owner"
    assert repo.viewer_by_token(session, token).id == viewer.id
    assert repo.recover_household(session, "wrong") is None
