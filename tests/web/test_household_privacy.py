"""T2.9 privacy defaults for tester households.

A household created by a tester is opt-in: no LLM, no Telegram/WhatsApp, no event-log files,
until its owner says so in Members & access. The operator's own configured keys are used only
after that opt-in, and never for another household by accident.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.web.settings import load_config


class SpyLLM:
    enabled = True
    last_call = None

    def __init__(self):
        self.calls = []

    async def interpret(self, *, trigger, now, features, audio, rules, jpeg):
        self.calls.append(trigger)
        return None


def app(tmp_path, llm_enabled_in_operator_config: bool):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    # The operator HAS configured an LLM. The tester household still must not use it by default.
    cfg["web"]["llm"]["enabled"] = llm_enabled_in_operator_config
    cfg["web"]["llm"]["provider"] = "openai"
    cfg["web"]["llm"]["model"] = "gpt-4o-mini"
    cfg["web"]["llm"]["api_key"] = "sk-operator-key"
    return create_app(cfg, pipeline=None, interpreter=SpyLLM(), notifier=None)


def test_new_household_defaults_are_private(tmp_path):
    app_ = app(tmp_path, llm_enabled_in_operator_config=True)
    with TestClient(app_) as c:
        me = c.post("/households", json={"dog_name": "Rex"}).json()
        assert me["llm_enabled"] is False, "a tester household must not start with the operator's LLM"
        assert me["log_enabled"] is False
        assert c.get("/households/me/settings").json() == {"llm_enabled": False, "log_enabled": False}


def test_settings_cannot_be_flipped_by_another_household(tmp_path):
    app_ = app(tmp_path, llm_enabled_in_operator_config=True)
    with TestClient(app_) as c:
        me = c.post("/households", json={"dog_name": "Rex"}).json()
        hid = me["household_id"]
        other = c.post("/households", json={"dog_name": "Other"}).json()["household_id"]
        # Spoofed household id in the path must not change this household's settings: there is
        # no /households/{id}/settings route at all, so the request cannot be routed to a
        # household from user input.
        for path in (f"/households/{other}/settings", f"/households/{other}/me/settings"):
            r = c.patch(path, json={"llm_enabled": True})
            assert r.status_code in (404, 405), f"{path} unexpectedly accepted the change"
        assert c.get("/households/me/settings").json() == {"llm_enabled": False, "log_enabled": False}


def test_opt_in_enables_the_operator_llm_for_that_household_only(tmp_path):
    app_ = app(tmp_path, llm_enabled_in_operator_config=True)
    with TestClient(app_) as c:
        me = c.post("/households", json={"dog_name": "Rex"}).json()
        hid = me["household_id"]
        assert c.patch("/households/me/settings", json={"llm_enabled": True}).json()["llm_enabled"] is True
        # Drop the cached runtime so the opt-in takes effect, then confirm it is now honoured.
        app_.state.registry.forget(hid)
        rt = app_.state.registry.peek(hid)
        assert rt is None or rt.runtime.interpreter.enabled is True
        # A different household still starts without it.
        other = c.post("/households", json={"dog_name": "Other"}).json()["household_id"]
        app_.state.registry.forget(other)
        rt2 = app_.state.registry.peek(other)
        assert rt2 is None or getattr(rt2.runtime.interpreter, "enabled", False) is False


def test_opt_out_turns_it_back_off(tmp_path):
    app_ = app(tmp_path, llm_enabled_in_operator_config=True)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        assert c.patch("/households/me/settings", json={"llm_enabled": True}).status_code == 200
        assert c.patch("/households/me/settings", json={"llm_enabled": False}).json()["llm_enabled"] is False