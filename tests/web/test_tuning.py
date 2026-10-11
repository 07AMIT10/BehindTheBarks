import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.store import db as store_db
from backend.store.models import AppSetting
from backend.web.hub import Hub
from backend.web.settings import load_config
from backend.web.tuning import SETTING_KEY, DbTuningStore, Tuning, TuningError

TOKEN = "op-secret"
DEFAULTS = {"video_fps": 4.0, "ws_frame_fps": 4.0, "gzip_enabled": True, "gzip_min_size": 1000}


class NoLLM:
    enabled = False
    last_call = None


def db_sessions(tmp_path):
    """A fresh SQLite database file with the app's schema, as a session factory."""
    engine = store_db.make_engine(f"sqlite:///{tmp_path / 'btb.db'}")
    store_db.init_db(engine)
    return sessionmaker(bind=engine, future=True)


def client(tmp_path, *, api_token=TOKEN):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["database_url"] = f"sqlite:///{tmp_path / 'btb.db'}"  # isolated from the repo's out/
    cfg["web"]["video"]["fps"] = 50
    cfg["web"]["auth"]["api_token"] = api_token
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return TestClient(app)


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def attached(tmp_path, defaults=DEFAULTS):
    sessions = db_sessions(tmp_path)
    t = Tuning(defaults)
    t.attach(DbTuningStore(sessions))
    return t, sessions


# -- validation -------------------------------------------------------------------------------------

def test_rejects_unknown_keys_and_out_of_range_values_without_changing_anything(tmp_path):
    t, _ = attached(tmp_path)
    with pytest.raises(TuningError, match="not tunable"):
        t.update({"video_fps": 2.0, "bogus": 1})
    with pytest.raises(TuningError, match="must be <= 30"):
        t.update({"video_fps": 2.0, "ws_frame_fps": 99})
    with pytest.raises(TuningError, match="must be true or false"):
        t.update({"gzip_enabled": "yes"})
    with pytest.raises(TuningError, match="must be a number"):
        t.update({"video_fps": True})  # bool is not a number here
    assert t.snapshot() == DEFAULTS


def test_config_defaults_are_not_range_checked(tmp_path):
    # an operator's config.yaml value outside the PATCH bounds must not stop the server from starting
    t = Tuning({**DEFAULTS, "video_fps": 50})
    assert t.get("video_fps") == 50.0


# -- storage in the database ------------------------------------------------------------------------

def test_overrides_are_stored_in_the_database_and_shared_by_instances(tmp_path):
    sessions = db_sessions(tmp_path)
    a = Tuning(DEFAULTS)
    a.attach(DbTuningStore(sessions))
    a.update({"video_fps": 1.5, "gzip_enabled": False})

    b = Tuning(DEFAULTS)  # a second instance on the same database, e.g. after a host switch
    b.attach(DbTuningStore(sessions))
    assert b.get("video_fps") == 1.5 and b.get("gzip_enabled") is False
    assert b.get("ws_frame_fps") == 4.0


def test_only_overrides_are_stored_so_config_changes_still_apply(tmp_path):
    t, sessions = attached(tmp_path)
    t.update({"video_fps": 1.5})
    with sessions() as s:
        stored = json.loads(s.get(AppSetting, SETTING_KEY).value)
    assert stored == {"video_fps": 1.5}  # only what the operator changed

    later = Tuning({**DEFAULTS, "gzip_min_size": 5000})  # config.yaml changed after the override
    later.attach(DbTuningStore(sessions))
    assert later.get("video_fps") == 1.5 and later.get("gzip_min_size") == 5000


def test_refresh_picks_up_a_change_saved_on_the_other_instance(tmp_path):
    sessions = db_sessions(tmp_path)
    a, b = Tuning(DEFAULTS), Tuning(DEFAULTS)
    a.attach(DbTuningStore(sessions))
    b.attach(DbTuningStore(sessions))
    b.update({"ws_frame_fps": 12.0})
    assert a.get("ws_frame_fps") == 4.0  # not yet seen
    a.refresh()
    assert a.get("ws_frame_fps") == 12.0


def test_a_failed_save_changes_nothing(tmp_path):
    class BrokenStore:
        def load(self):
            return None

        def save(self, overrides):
            raise RuntimeError("database is down")

    t = Tuning(DEFAULTS)
    t.attach(BrokenStore())
    with pytest.raises(RuntimeError):
        t.update({"video_fps": 1.0})
    assert t.get("video_fps") == 4.0


def test_a_failed_refresh_keeps_the_current_values(tmp_path):
    class FlakyStore:
        def __init__(self):
            self.up = True

        def load(self):
            if not self.up:
                raise RuntimeError("database is briefly unreachable")
            return None

        def save(self, overrides):
            pass

    store = FlakyStore()
    t = Tuning(DEFAULTS)
    t.attach(store)  # a database that is down at startup should fail the app, so attach is not tolerant
    t.update({"video_fps": 2.0})
    store.up = False
    t.refresh()
    assert t.get("video_fps") == 2.0


def test_a_bad_stored_value_is_ignored_with_a_warning(tmp_path, caplog):
    sessions = db_sessions(tmp_path)
    with sessions() as s:
        s.add(AppSetting(key=SETTING_KEY, value=json.dumps({"video_fps": 999}), updated_at=0.0))
        s.commit()
    t = Tuning(DEFAULTS)
    with caplog.at_level(logging.WARNING):
        t.attach(DbTuningStore(sessions))
    assert t.get("video_fps") == 4.0
    assert "ignoring stored tuning overrides" in caplog.text


def test_legacy_tuning_json_is_imported_once(tmp_path):
    legacy = tmp_path / "tuning.json"
    legacy.write_text(json.dumps({"video_fps": 2.5}))
    sessions = db_sessions(tmp_path)
    t = Tuning(DEFAULTS)
    t.attach(DbTuningStore(sessions), legacy_path=legacy)
    assert t.get("video_fps") == 2.5
    legacy.write_text(json.dumps({"video_fps": 9.0}))  # a later edit to the file is not picked up
    again = Tuning(DEFAULTS)
    again.attach(DbTuningStore(sessions), legacy_path=legacy)
    assert again.get("video_fps") == 2.5


# -- the live effect on the WebSocket fan-out ----------------------------------------------------

def test_hub_reads_the_frame_rate_on_every_publish(tmp_path):
    now = [0.0]
    t = Tuning(DEFAULTS)
    hub = Hub(frame_fps=lambda: t.get("ws_frame_fps"), clock=lambda: now[0])
    assert hub.publish("frame", {"ts": 0.0}) is not None
    now[0] = 0.2
    assert hub.publish("frame", {"ts": 0.2}) is None  # 4 fps: 0.25 s gap
    t.update({"ws_frame_fps": 10.0})  # admin raises it: applies to the very next frame
    now[0] = 0.3
    assert hub.publish("frame", {"ts": 0.3}) is not None


# -- admin endpoints ----------------------------------------------------------------------------

def test_admin_endpoints_need_the_operator_token(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/admin/tuning").status_code == 401
        assert c.get("/admin/tuning", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.patch("/admin/tuning", json={"video_fps": 2}).status_code == 401


def test_admin_api_is_disabled_without_an_api_token(tmp_path):
    with client(tmp_path, api_token="") as c:
        assert c.get("/admin/tuning").status_code == 403


def test_admin_can_read_and_change_the_knobs_live(tmp_path):
    with client(tmp_path) as c:
        before = c.get("/admin/tuning", headers=auth()).json()
        assert before["video_fps"] == 50.0  # the test config's value
        r = c.patch("/admin/tuning", headers=auth(), json={"video_fps": 2, "gzip_enabled": False})
        assert r.status_code == 200 and r.json()["video_fps"] == 2.0 and r.json()["gzip_enabled"] is False
        assert c.get("/admin/tuning", headers=auth()).json()["gzip_enabled"] is False


def test_admin_settings_survive_a_restart_of_the_app(tmp_path):
    with client(tmp_path) as c:
        c.patch("/admin/tuning", headers=auth(), json={"video_fps": 2.0})
    with client(tmp_path) as c:  # same database, new process
        assert c.get("/admin/tuning", headers=auth()).json()["video_fps"] == 2.0


def test_admin_rejects_bad_values_with_400(tmp_path):
    with client(tmp_path) as c:
        r = c.patch("/admin/tuning", headers=auth(), json={"video_fps": 0})
        assert r.status_code == 400 and "video_fps" in r.json()["detail"]


# -- compression ---------------------------------------------------------------------------------

def test_json_is_gzipped_only_when_enabled(tmp_path):
    with client(tmp_path) as c:
        c.patch("/admin/tuning", headers=auth(), json={"gzip_min_size": 100})  # /status is ~870 bytes
        assert c.get("/status", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"
        c.patch("/admin/tuning", headers=auth(), json={"gzip_enabled": False})
        assert "content-encoding" not in c.get("/status", headers={"Accept-Encoding": "gzip"}).headers


def test_video_stream_is_never_gzipped(tmp_path):
    with client(tmp_path) as c:
        r = c.get("/video?max_frames=1", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in r.headers
        assert b"\xff\xd8" in r.content
