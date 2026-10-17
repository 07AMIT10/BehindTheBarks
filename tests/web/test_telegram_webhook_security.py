"""Telegram webhook security (privacy fix): the public /telegram/webhook can no longer be fed.

Verifies:
1. A webhook secret configured in `web.notify.telegram.webhook_secret`: missing or wrong
   `X-Telegram-Bot-Api-Secret-Token` is rejected without processing the body.
2. The right secret token is accepted.
3. A callback query naming an unknown household id does NOT materialise a HouseholdRuntime
   (registry size unchanged) and is answered 404.
4. Without a secret configured, the old behaviour keeps working (pin-mode regression).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def _app(tmp_path, secret: str = ""):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    if secret:
        cfg["web"]["notify"]["telegram"]["webhook_secret"] = secret
    return create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def _cb(hid: str) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cb_1",
            "data": f"treat:{hid}",
            "message": {"message_id": 2, "chat": {"id": -100123}, "caption": "x"},
            "from": {"first_name": "Test"},
        },
    }


def test_forged_or_missing_secret_token_is_rejected(tmp_path):
    app_ = _app(tmp_path, "S3CRET-TOKEN")
    with TestClient(app_) as client:
        r = client.post("/telegram/webhook", json=_cb("legacy"))
        assert r.status_code == 403
        assert r.json()["error"] == "unauthorized"
        r = client.post("/telegram/webhook", json=_cb("legacy"),
                        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
        assert r.status_code == 403


def test_valid_secret_token_is_accepted(tmp_path):
    app_ = _app(tmp_path, "S3CRET-TOKEN")
    with TestClient(app_) as client:
        r = client.post("/telegram/webhook", json=_cb("legacy"),
                        headers={"X-Telegram-Bot-Api-Secret-Token": "S3CRET-TOKEN"})
        assert r.status_code == 200
        assert r.json()["ok"] is True


def test_unknown_household_id_does_not_create_a_runtime(tmp_path):
    app_ = _app(tmp_path)
    with TestClient(app_) as client:
        before = len(app_.state.registry._households)
        r = client.post("/telegram/webhook", json=_cb("does-not-exist"))
        assert r.status_code == 404
        assert r.json()["error"] == "unknown_household"
        assert len(app_.state.registry._households) == before  # no lazily-created runtime


def test_without_a_secret_configured_the_old_behaviour_stands(tmp_path):
    app_ = _app(tmp_path)
    with TestClient(app_) as client:
        assert client.post("/telegram/webhook", json=_cb("legacy")).status_code == 200
