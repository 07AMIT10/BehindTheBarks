"""T2.10 retention: old session logs, expired pairing codes, dead viewers and idle runtimes are swept.

Every clock is injected so the tests assert exact ages rather than "it eventually cleaned up".
"""

import os
import time

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.store import repo as store_repo
from backend.store.db import sha256_hex
from backend.store.models import PairingCode, Viewer
from backend.web.settings import load_config

DAY = 86_400.0


def app(tmp_path, *, retention_days: int = 7, idle_evict_s: float = 600.0):
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["database_url"] = f"sqlite:///{tmp_path / 'out' / 'btb.db'}"
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["log_retention_days"] = retention_days
    cfg["web"]["registry"] = {"idle_evict_s": idle_evict_s, "sweep_every_s": 3600}
    return create_app(cfg, pipeline=None, interpreter=None, notifier=None)


def sweep(c, now=None):
    """Run the async registry.sweep() on the TestClient's own loop (runtimes are bound to it).

    BlockingPortal.call is positional-only, so `now` is passed positionally.
    """
    return c.portal.call(c.app.state.registry.sweep, now)


def test_session_logs_older_than_the_retention_window_are_deleted(tmp_path):
    app_ = app(tmp_path, retention_days=7)
    out = tmp_path / "out"
    household_dir = out / "h-old"
    household_dir.mkdir(parents=True)
    old = household_dir / "session-20200101.jsonl"
    fresh = household_dir / "session-29990101.jsonl"
    old.write_text('{"seq":1}\n')
    fresh.write_text('{"seq":1}\n')
    now = time.time()
    os.utime(old, (now - 10 * DAY, now - 10 * DAY))     # 10 days old: gone
    os.utime(fresh, (now - 1 * DAY, now - 1 * DAY))     # 1 day old: kept

    with TestClient(app_) as c:
        stats = sweep(c, now=time.time())

    assert stats["logs_deleted"] == 1
    assert not old.exists()
    assert fresh.exists(), "a log inside the retention window must be kept"


def test_sweep_never_crosses_household_directories(tmp_path):
    app_ = app(tmp_path, retention_days=7)
    out = tmp_path / "out"
    for hid in ("h-a", "h-b"):
        d = out / hid
        d.mkdir(parents=True)
        (d / "session-old.jsonl").write_text("{}\n")
    now = time.time()
    for hid in ("h-a", "h-b"):
        os.utime(out / hid / "session-old.jsonl", (now - 30 * DAY, now - 30 * DAY))
    # An unrelated file in the log root must survive.
    keep = out / "important.txt"
    keep.write_text("not a session log")
    os.utime(keep, (now - 30 * DAY, now - 30 * DAY))

    with TestClient(app_) as c:
        sweep(c, now=time.time())

    assert keep.exists(), "the sweeper must only touch session-*.jsonl"
    for hid in ("h-a", "h-b"):
        assert not (out / hid / "session-old.jsonl").exists()


def test_expired_pairing_codes_are_removed(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        with app_.state.db_session() as s:
            hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
            with app_.state.db_session() as s2:
                store_repo.create_pairing_code(s2, household_id=hid, kind="viewer")
                fresh_code = store_repo.create_pairing_code(s2, household_id=hid, kind="viewer")
                now = time.time()
                codes = s2.query(PairingCode).all()
                # The first code we made is the one that has already lapsed.
                codes[0].expires_at = now - 5 * DAY
                codes[1].expires_at = now + DAY
                s2.commit()
                expired_id = codes[0].code_hash
                fresh_id = sha256_hex(fresh_code)
        stats = sweep(c, now=time.time())
        assert stats["pairing_codes_deleted"] == 1
        with app_.state.db_session() as s2:
            remaining = {pc.code_hash for pc in s2.query(PairingCode).all()}
        assert fresh_id in remaining and expired_id not in remaining


def test_viewers_past_their_expiry_are_marked_revoked(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        with app_.state.db_session() as s:
            live, _ = store_repo.create_viewer(s, household_id=hid, role="viewer", label="live")
            dead, _ = store_repo.create_viewer(s, household_id=hid, role="viewer", label="dead")
            live.expires_at = None
            dead.expires_at = time.time() - DAY
            s.commit()
            live_id, dead_id = live.id, dead.id
        sweep(c, now=time.time())
        with app_.state.db_session() as s:
            assert s.get(Viewer, live_id).revoked_at is None
            assert s.get(Viewer, dead_id).revoked_at is not None


def test_idle_runtimes_are_evicted_by_the_same_sweep(tmp_path):
    app_ = app(tmp_path, idle_evict_s=1.0)
    with TestClient(app_) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        c.get("/me")  # materialises the runtime
        assert app_.state.registry.peek(hid) is not None
        stats = sweep(c, now=time.time() + 10_000)
        assert hid in stats["runtimes_evicted"]
        assert app_.state.registry.peek(hid) is None


def test_a_busy_household_is_never_evicted(tmp_path):
    app_ = app(tmp_path, idle_evict_s=1.0)
    with TestClient(app_) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        c.get("/me")
        lr = app_.state.registry.peek(hid)
        lr.viewers = 1  # someone is watching
        stats = sweep(c, now=time.time() + 10_000)
        assert hid not in stats["runtimes_evicted"]