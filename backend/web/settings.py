"""Web-side config: config.yaml `web:` section with defaults, plus env overrides. No other module
reads os.environ or config.yaml directly; they receive the dict from here."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

WEB_DEFAULTS: dict[str, Any] = {
    "pipeline": "mock",
    "log_dir": "out",
    "ws": {"frame_fps": 8, "client_queue": 64, "history": 2000},
    "video": {"fps": 8},
    "state": {
        "persist_s": 3.0, "no_dog_unknown_s": 2.0, "llm_override_conf": 0.7, "llm_stale_s": 12.0,
        "cooldown_s": 60.0, "cooldowns": {}, "always_notify": ["fearful", "aggressive", "disinterested"],
        "quiet": ["unknown"], "tick_hz": 4, "live_hz": 1,
    },
    "llm": {
        "min_interval_s": 3.0, "heartbeat_s": 10.0, "audio_trigger_score": 0.6, "timeout_s": 8.0,
        "context_s": 3.0, "max_feature_samples": 6, "image_max_side": 512, "json_mode": True,
        "temperature": 0.2, "max_tokens": 200, "openrouter_referer": "http://localhost:3000",
        "openrouter_title": "Behind The Barks",
    },
    "notify": {"mode": "dashboard_only"},
    "demo": {"manifest": "data/fallback/manifest.json", "clips_dir": "backend/demo/clips", "telegram_first": False},
    "ingest": {"stale_s": 5.0, "status_every_s": 2.0, "fps_window_s": 3.0, "max_message_bytes": 2_000_000},
    "remote": {"stale_s": 2.0, "clock_samples": 5, "ping_every_s": 30.0, "max_skew_s": 60.0,
              "downlink_timeout_s": 5.0},
    "profile": {"dog_name": "Bruno", "location": "Kitchen", "zone_label": "feeding area"},
    "auth": {
        "enabled": False,
        "dashboard_pin": "",
        "ingest_token": "",
        "api_token": "",
    },
}


def _merge(base: dict, over: Mapping) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, Mapping) and isinstance(out.get(k), dict) else v
    return out


def _truthy(v: str | None) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: str | Path = "config.yaml", env: Mapping[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    p = Path(path)
    raw = (yaml.safe_load(p.read_text()) if p.exists() else None) or {}
    cfg = dict(raw)
    cfg["web"] = _merge(WEB_DEFAULTS, raw.get("web") or {})
    cfg["web"]["demo_mode"] = _truthy(env.get("DEMO_MODE"))
    if env.get("WEB_PIPELINE"):
        cfg["web"]["pipeline"] = env["WEB_PIPELINE"].strip().lower()
    if env.get("NOTIFY_MODE"):
        cfg["web"]["notify"]["mode"] = env["NOTIFY_MODE"]
    if cfg["web"]["demo_mode"]:
        cfg["web"]["notify"]["mode"] = "dashboard_only"
    auth = cfg["web"].setdefault("auth", {})
    if env.get("BTB_AUTH_ENABLED") is not None:
        auth["enabled"] = _truthy(env.get("BTB_AUTH_ENABLED"))
    if env.get("BTB_DASHBOARD_PIN"):
        auth["dashboard_pin"] = env["BTB_DASHBOARD_PIN"].strip()
        auth["enabled"] = True
    if env.get("BTB_INGEST_TOKEN"):
        auth["ingest_token"] = env["BTB_INGEST_TOKEN"].strip()
    if env.get("BTB_API_TOKEN"):
        auth["api_token"] = env["BTB_API_TOKEN"].strip()
    return cfg


def is_demo(cfg: dict) -> bool:
    return bool(cfg["web"].get("demo_mode"))


@dataclass
class LLMSettings:
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    vision: bool = True
    timeout_s: float = 8.0
    json_mode: bool = True
    temperature: float = 0.2
    max_tokens: int = 200
    image_max_side: int = 512
    extra_headers: dict[str, str] = field(default_factory=dict)
    demo: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model) and not self.demo

    @classmethod
    def from_config(cls, cfg: dict, env: Mapping[str, str] | None = None) -> "LLMSettings":
        env = os.environ if env is None else env
        llm = cfg["web"]["llm"]
        provider = env.get("LLM_PROVIDER", "").strip().lower()
        headers = (
            {"HTTP-Referer": llm["openrouter_referer"], "X-Title": llm["openrouter_title"]}
            if provider == "openrouter" else {}
        )
        return cls(
            provider=provider, base_url=env.get("LLM_BASE_URL", ""), api_key=env.get("LLM_API_KEY", ""),
            model=env.get("LLM_MODEL", ""), vision=env.get("LLM_VISION", "1") != "0",
            timeout_s=float(llm["timeout_s"]), json_mode=bool(llm["json_mode"]),
            temperature=float(llm["temperature"]), max_tokens=int(llm["max_tokens"]),
            image_max_side=int(llm["image_max_side"]), extra_headers=headers, demo=is_demo(cfg),
        )
