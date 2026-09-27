from pathlib import Path

from backend.web.settings import LLMSettings, is_demo, load_config


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return p


def test_web_defaults_filled_when_section_missing(tmp_path):
    cfg = load_config(_write(tmp_path, "data: {fps: 8}\n"), env={})
    assert cfg["data"] == {"fps": 8}
    assert cfg["web"]["pipeline"] == "mock"
    assert cfg["web"]["state"]["persist_s"] == 3.0
    assert cfg["web"]["llm"]["timeout_s"] == 8.0
    assert cfg["web"]["profile"] == {"dog_name": "Bruno", "location": "Kitchen", "zone_label": "feeding area"}


def test_partial_web_section_is_deep_merged(tmp_path):
    cfg = load_config(_write(tmp_path, "web: {state: {persist_s: 5}}\n"), env={})
    assert cfg["web"]["state"]["persist_s"] == 5
    assert cfg["web"]["state"]["cooldown_s"] == 60.0


def test_env_overrides_notify_and_demo(tmp_path):
    cfg = load_config(_write(tmp_path, ""), env={"NOTIFY_MODE": "telegram", "DEMO_MODE": "1"})
    assert is_demo(cfg)
    assert cfg["web"]["notify"]["mode"] == "dashboard_only"  # demo forces it


def test_llm_settings_from_env_only(tmp_path):
    cfg = load_config(_write(tmp_path, ""), env={})
    env = {"LLM_PROVIDER": "openrouter", "LLM_BASE_URL": "https://x/v1",
           "LLM_API_KEY": "k", "LLM_MODEL": "m", "LLM_VISION": "0"}
    s = LLMSettings.from_config(cfg, env)
    assert (s.provider, s.base_url, s.model, s.vision, s.enabled) == ("openrouter", "https://x/v1", "m", False, True)
    assert s.extra_headers == {"HTTP-Referer": "http://localhost:3000", "X-Title": "Behind The Barks"}


def test_llm_disabled_without_key_or_in_demo(tmp_path):
    cfg = load_config(_write(tmp_path, ""), env={})
    assert not LLMSettings.from_config(cfg, {"LLM_BASE_URL": "u", "LLM_MODEL": "m"}).enabled
    demo = load_config(_write(tmp_path, ""), env={"DEMO_MODE": "1"})
    env = {"LLM_BASE_URL": "u", "LLM_MODEL": "m", "LLM_API_KEY": "k"}
    assert not LLMSettings.from_config(demo, env).enabled
    assert LLMSettings.from_config(cfg, {**env, "LLM_PROVIDER": "groq"}).extra_headers == {}
