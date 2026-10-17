"""The user-editable pet profile (T1.8): a saved JSON file, two routes, one live broadcast.

Precedence is saved file > env > YAML > defaults, so a deployer can pre-fill the name and a viewer
can still change it. Render's ephemeral disk loses the file on redeploy — the fallback is the
config, never an error.
"""

import json
import time

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.profile_store import ProfileStore, validate_profile
from backend.web.settings import load_config

SAVED = {"dog_name": "Rex", "location": "Living room", "zone_label": "bowl"}


class NoLLM:
    enabled = False
    last_call = None


def cfg_for(tmp_path, **web):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    for k, v in web.items():
        cfg["web"][k] = v
    return cfg


def app_for(tmp_path, **web):
    cfg = cfg_for(tmp_path, **web)
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier()), cfg


# -- the store ---------------------------------------------------------------------------------
def test_store_falls_back_to_defaults_when_the_file_is_missing(tmp_path):
    store = ProfileStore(tmp_path / "out", defaults={"dog_name": "Your dog", "location": "", "zone_label": "feeding area"})
    assert store.load() == {"dog_name": "Your dog", "location": "", "zone_label": "feeding area"}


def test_store_survives_a_corrupt_file(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "profile.json").write_text("{not json")
    store = ProfileStore(tmp_path / "out", defaults={"dog_name": "Your dog"})
    assert store.load() == {"dog_name": "Your dog"}


def test_store_roundtrip_is_atomic_and_validated(tmp_path):
    store = ProfileStore(tmp_path / "out", defaults={})
    store.save(SAVED)
    assert store.load() == SAVED
    assert json.loads((tmp_path / "out" / "profile.json").read_text()) == SAVED
    assert not list((tmp_path / "out").glob("*.tmp"))  # the temp file is gone


def test_validate_profile_rejects_bad_input():
    validate_profile(SAVED)  # the happy path raises nothing
    for bad in ({"location": 3}, {"dog_name": None}, {"dog_name": "x" * 65}, {"location": "y" * 65},
                {"zone_label": "z" * 41}, {"dog_name": ["a"]}):
        try:
            validate_profile(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")
    # unknown keys are dropped, not rejected: a newer client cannot poison an older server
    assert validate_profile({"dog_name": "Rex", "wat": 1}) == {"dog_name": "Rex"}


# -- the routes --------------------------------------------------------------------------------
def test_put_then_get_returns_the_saved_profile(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as c:
        r = c.put("/profile", json=SAVED)
        assert r.status_code == 200 and r.json() == SAVED
        assert c.get("/profile").json() == SAVED
        assert c.get("/status").json()["profile"] == SAVED  # status carries it too


def test_put_broadcasts_the_profile_to_open_dashboards(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as c, c.websocket_connect("/ws") as dash:
        assert dash.receive_json()["type"] == "status"
        assert c.put("/profile", json=SAVED).status_code == 200
        deadline = time.time() + 3.0
        while time.time() < deadline:
            msg = dash.receive_json()
            if msg["type"] == "status" and (msg["data"].get("profile") or {}).get("dog_name") == "Rex":
                break
        else:
            raise AssertionError("no profile broadcast arrived")
        assert msg["data"]["profile"] == SAVED


def test_partial_put_merges_over_the_current_profile(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as c:
        c.put("/profile", json=SAVED)
        r = c.put("/profile", json={"dog_name": "Buddy"})
        assert r.status_code == 200
        assert r.json() == {"dog_name": "Buddy", "location": "Living room", "zone_label": "bowl"}


def test_put_rejects_invalid_bodies(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as c:
        assert c.put("/profile", json={"dog_name": "x" * 65}).status_code == 400
        assert c.put("/profile", json={"dog_name": 5}).status_code == 400
        assert c.get("/profile").json()["dog_name"] == "Your dog"  # nothing was written


def test_profile_routes_require_auth_when_enabled(tmp_path):
    app, _ = app_for(tmp_path, auth={"enabled": True, "dashboard_pin": "1234", "api_token": "t", "ingest_token": "i"})
    with TestClient(app) as c:
        assert c.get("/profile").status_code == 401
        assert c.put("/profile", json=SAVED).status_code == 401
        headers = {"Authorization": "Bearer t"}
        assert c.put("/profile", json=SAVED, headers=headers).status_code == 200
        assert c.get("/profile", headers=headers).json() == SAVED


def test_saved_file_wins_over_env_and_yaml(tmp_path):
    app, cfg = app_for(tmp_path)
    cfg["web"]["profile"] = {"dog_name": "FromEnv", "location": "", "zone_label": "feeding area"}
    with TestClient(app) as c:
        c.put("/profile", json={"dog_name": "Rex"})
        assert cfg["web"]["profile"]["dog_name"] == "Rex"  # the cfg dict is updated in place
