# Web Backend Core Implementation Plan (Person B — Plan 1 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A running FastAPI backend that turns Pipeline events (mock today, Person A's real one later) into fused EmotionStates, LLM interpretations and notifications, and pushes all of it to dashboard clients over WebSocket. Everything is tested without a camera, a GPU or network access.

**Architecture:** Ports and adapters. `backend/contracts.py` and `backend/pipeline.py` are the only shared modules. The fusion logic (`state.py`, `llm_parse.py`, `llm_triggers.py`) is pure: time comes in as an argument, no I/O. The adapters (`MockPipeline`, the OpenAI-compatible client, Telegram) sit behind small interfaces. `backend/web/runtime.py` does all the wiring, and `backend/main.py` only holds the HTTP/WS routes.

**Tech Stack:** Python 3.11-compatible code (the local venv here is 3.12), FastAPI, uvicorn, pydantic v2, openai SDK (AsyncOpenAI), httpx, PyYAML, opencv-python-headless, pytest, pytest-asyncio.

**Spec:** `CLAUDE.md` (source of truth) + `PROMPTS-WEB.md` Steps 0–6. This plan implements them; where it interprets an ambiguity, it says so under "Decisions".

## Global Constraints

- Web owns: `backend/main.py`, `backend/fusion/llm_interpreter.py`, `backend/fusion/state.py`, `backend/notify/`, `backend/demo/`, `frontend/`, `config.yaml` (web section only), env setup. New web-only modules also live in `backend/web/` and `backend/fusion/{prompts,llm_parse,llm_triggers}.py`.
- **Never edit** Person A's files: `backend/sources.py`, `backend/vision/`, `backend/audio/`, `backend/fusion/rules.py`, `data/`, `requirements-data.txt`.
- Shared files (`backend/contracts.py`, `backend/pipeline.py`) may only gain what CLAUDE.md already specifies. Commit them separately and tell Person A.
- Never import `backend.vision`, `backend.audio` or `backend.fusion.rules` from web code. `backend.pipeline.Pipeline` is imported lazily, and only when `web.pipeline: real`.
- No provider or model name appears in code. They come only from `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_VISION` and `LLM_API_KEY`.
- LLM: 8 s timeout, ≥ 3 s between calls, off the event loop's critical path, and a log line on every call (provider, model, latency, tokens, outcome).
- Emotion labels come only from `contracts.EMOTIONS`.
- Tests never touch the network. Heavy deps (torch/TF/DLC) are never needed for the web tests.
- Commands run from the repo root with `.venv/bin/python`.

## Decisions (interpretations of the spec, flag to user if wrong)

1. **Notifications.** A persisted state change notifies unless the emotion is in `quiet` (default `[unknown]`), subject to a per-emotion cooldown. Emotions in `always_notify` (`fearful`, `aggressive`, `disinterested`) also **re-notify while the state persists**, each time their cooldown expires. That's my reading of "always notify … subject to cooldown".
2. **Audio trigger.** An AudioEvent triggers the LLM only if `score > 0.6` **and** its label is a dog sound (not `silence` or `other`).
3. **Trigger priority when several are pending:** `treat` > `audio` > `rules_change` > `heartbeat`. While a call is in flight, new triggers overwrite the pending one ("latest wins"), and it fires once the call ends and the minimum interval has passed.
4. **First state.** The first computed state is adopted immediately and never notifies. After that, a change must persist for `persist_s`.
5. **Rules reason text.** `RulesLabel` has no `reason`, so `state.py` builds one: `"Rules: excited 0.82, happy 0.40"` (top two scores).
6. **`DEMO_MODE=1` in this plan.** It forces `notify.mode=dashboard_only` and disables the LLM. Plan 4 adds the cached-LLM demo replay.

## File Structure

| File | Responsibility |
|---|---|
| `requirements-web.txt` | Pinned web deps (Task 1) |
| `.env.example`, `.gitignore` | Env template; ignore `.env` (Task 1) |
| `Makefile` | `dev-backend`, `dev-frontend`, `dev`, `demo`, `test-web` (Task 1) |
| `config.yaml` | + `web:` section (Task 1) |
| `backend/web/__init__.py` | package |
| `backend/web/settings.py` | load config.yaml + web defaults + env overrides; `LLMSettings` (Task 1) |
| `backend/contracts.py` | + `EmotionState`, `LLMResult` (Task 2, shared) |
| `backend/pipeline.py` | + `mark_treat`, `ingest_frame`, `ingest_audio`, `status` stubs (Task 2, shared) |
| `backend/demo/__init__.py`, `backend/demo/mock_pipeline.py` | scripted 90 s MockPipeline (Task 3) |
| `backend/fusion/state.py` | `combine()` final label + `FusionState` machine, cooldowns, timeline (Task 4) |
| `backend/fusion/llm_parse.py` | defensive reply parsing (Task 5) |
| `backend/fusion/llm_triggers.py` | trigger policy / rate limit (Task 5) |
| `backend/fusion/prompts.py` | system prompt + user-content builder (Task 6) |
| `backend/fusion/llm_interpreter.py` | AsyncOpenAI client, timeout, logging (Task 6) |
| `scripts/llm_smoke_test.py` | one real call to the configured provider (Task 6) |
| `backend/notify/{__init__,base,dashboard,telegram}.py` | Notifier port + adapters (Task 7) |
| `scripts/telegram_test.py` | send one test photo (Task 7) |
| `backend/web/hub.py` | WS fan-out with drop-oldest per client + history ring (Task 8) |
| `backend/web/event_log.py` | `out/session-<ts>.jsonl` writer (Task 8) |
| `backend/web/runtime.py` | wiring: pipeline callbacks → state/LLM/notifier/hub (Task 9) |
| `backend/main.py` | `create_app()`: /health /status /ws /video /treat /events (Task 10) |
| `tests/web/…` | one test file per unit |

---

### Task 1: Bootstrap — deps, config `web:` section, settings loader

**Files:**
- Create: `requirements-web.txt`, `.env.example`, `Makefile`, `backend/web/__init__.py`, `backend/web/settings.py`, `tests/web/__init__.py`, `tests/web/test_settings.py`
- Modify: `config.yaml` (append `web:` section only), `.gitignore` (add `.env`), `pytest.ini` (asyncio mode)

**Interfaces — Produces:**
- `load_config(path: str | Path = "config.yaml", env: Mapping[str,str] | None = None) -> dict` — full config; `cfg["web"]` always fully populated with defaults.
- `LLMSettings` dataclass: `provider, base_url, api_key, model, vision: bool, timeout_s, json_mode, temperature, max_tokens, image_max_side, extra_headers: dict`; property `enabled -> bool`; classmethod `from_config(cfg: dict, env: Mapping[str,str] | None = None)`.
- `is_demo(cfg) -> bool`.

- [ ] **Step 1: venv + deps**

`requirements-web.txt`:
```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.13.5
openai==1.59.7
httpx==0.28.1
python-dotenv==1.0.1
pyyaml==6.0.3
numpy==2.4.6
opencv-python-headless==4.11.0.86
pytest==9.1.1
pytest-asyncio==0.25.2
```
Run: `uv pip install --python .venv/bin/python -r requirements-web.txt`
Expected: installs cleanly. If a pin doesn't resolve, bump to the nearest available version, update the file and note it in the commit message. `pydantic`, `pyyaml` and `numpy` must stay identical to `requirements-data.txt`.

`pytest.ini` becomes:
```ini
[pytest]
testpaths = tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

- [ ] **Step 2: config `web:` section** (append to `config.yaml`, below `data:`, don't touch `data:`)

```yaml

# ---------------------------------------------------------------- Person B (Web)
web:
  pipeline: mock              # mock | real
  log_dir: out
  ws:
    frame_fps: 8              # max frame messages/s per client
    client_queue: 64          # per-client buffer; oldest dropped when full
    history: 2000             # non-frame events kept for GET /events
  video:
    fps: 8                    # MJPEG rate for GET /video
  state:
    persist_s: 3.0            # new emotion must hold this long to become the state
    no_dog_unknown_s: 2.0
    llm_override_conf: 0.7    # LLM wins a disagreement only at/above this
    llm_stale_s: 12.0         # ignore LLM results older than this
    cooldown_s: 60.0          # default per-emotion notification cooldown
    cooldowns: {}             # per-emotion override, e.g. {excited: 120}
    always_notify: [fearful, aggressive, disinterested]
    quiet: [unknown]
    tick_hz: 4
    live_hz: 1
  llm:
    min_interval_s: 3.0
    heartbeat_s: 10.0
    audio_trigger_score: 0.6
    timeout_s: 8.0
    context_s: 3.0            # seconds of features sent
    max_feature_samples: 6
    image_max_side: 512
    json_mode: true
    temperature: 0.2
    max_tokens: 200
    openrouter_referer: http://localhost:3000
    openrouter_title: Behind The Barks
  notify:
    mode: dashboard_only      # telegram | dashboard_only
  profile:                    # shown in the UI header and Telegram captions (ui/screens)
    dog_name: Bruno
    location: Kitchen
    zone_label: feeding area
```

- [ ] **Step 3: `.env.example`, `.gitignore`, `Makefile`**

`.env.example`:
```
LLM_PROVIDER=groq
LLM_API_KEY=
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=
LLM_VISION=1
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
NOTIFY_MODE=dashboard_only
DEMO_MODE=0
```
Append to `.gitignore`: `.env`

`Makefile` (recipes use TABs):
```make
PY ?= .venv/bin/python
.PHONY: dev-backend dev-frontend dev demo test-web
dev-backend:
	$(PY) -m uvicorn backend.main:app --reload --port 8000
dev-frontend:
	cd frontend && npm run dev
dev:
	$(MAKE) -j2 dev-backend dev-frontend
demo:
	DEMO_MODE=1 $(PY) -m uvicorn backend.main:app --port 8000
test-web:
	$(PY) -m pytest -q tests/web
```

- [ ] **Step 4: failing test** `tests/web/test_settings.py`
```python
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
```

- [ ] **Step 5: run, expect FAIL** — `.venv/bin/python -m pytest tests/web/test_settings.py -q` → `ModuleNotFoundError: backend.web`

- [ ] **Step 6: implement** `backend/web/__init__.py` (empty docstring) and `backend/web/settings.py`:
```python
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
    "profile": {"dog_name": "Bruno", "location": "Kitchen", "zone_label": "feeding area"},
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
    if env.get("NOTIFY_MODE"):
        cfg["web"]["notify"]["mode"] = env["NOTIFY_MODE"]
    if cfg["web"]["demo_mode"]:
        cfg["web"]["notify"]["mode"] = "dashboard_only"
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
```

- [ ] **Step 7: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_settings.py -q` → 5 passed. Also `.venv/bin/python -m pytest -q tests/data` → still 97 passed (Person A's suite unaffected).

- [ ] **Step 8: commit**
```bash
git add requirements-web.txt .env.example .gitignore Makefile pytest.ini config.yaml backend/web tests/web
git commit -m "Web step 0: web deps, config web section, settings loader"
```
(The Next.js scaffold and the "backend: connected" check belong to Plan 2, Task 1.)

---

### Task 2: Shared contracts — `EmotionState`, `LLMResult`, full `Pipeline` interface

**Files:**
- Modify: `backend/contracts.py` (append two models), `backend/pipeline.py` (add four methods)
- Test: `tests/web/test_contracts_web.py`

**Interfaces — Produces:**
- `EmotionState(ts: float, emotion: Emotion, confidence: float[0,1], source: Literal["rules","llm","fused"], reason: str, snapshot: str | None = None)`
- `LLMResult(ts, emotion: Emotion, confidence[0,1], reason: str, provider: str, model: str, latency_ms: float, trigger: str)`
- `Pipeline.mark_treat(ts)`, `.ingest_frame(jpeg, ts)`, `.ingest_audio(pcm16, sample_rate, ts)`, `.status() -> dict` (all `NotImplementedError` stubs; Person A implements them)
- `PIPELINE_METHODS: tuple[str, ...]` in `backend/pipeline.py` — the public method names; used by the MockPipeline conformance test

- [ ] **Step 1: failing test** `tests/web/test_contracts_web.py`
```python
import inspect

import pytest
from pydantic import ValidationError

from backend.contracts import EmotionState, LLMResult
from backend.pipeline import PIPELINE_METHODS, Pipeline

EXAMPLE = {"ts": 1727340001.0, "emotion": "excited", "confidence": 0.78, "source": "fused",
           "reason": "High, fast tail wag and open mouth as the treat drops; two short yips.",
           "snapshot": "base64 or URL"}


def test_emotion_state_matches_claude_md_example():
    s = EmotionState.model_validate(EXAMPLE)
    assert EmotionState.model_validate_json(s.to_json()) == s


@pytest.mark.parametrize("bad", [{"emotion": "sad"}, {"confidence": 1.2}, {"source": "gpt"}, {"extra": 1}])
def test_emotion_state_rejects_bad(bad):
    with pytest.raises(ValidationError):
        EmotionState.model_validate({**EXAMPLE, **bad})


def test_llm_result_roundtrip():
    r = LLMResult(ts=1.0, emotion="happy", confidence=0.9, reason="Loose mouth.", provider="groq",
                  model="m", latency_ms=812.5, trigger="treat")
    assert LLMResult.model_validate_json(r.to_json()) == r


def test_pipeline_interface_matches_claude_md():
    assert PIPELINE_METHODS == ("run", "latest_frame_jpeg", "mark_treat", "ingest_frame",
                                "ingest_audio", "status", "stop")
    for name in PIPELINE_METHODS:
        assert callable(getattr(Pipeline, name))
    assert list(inspect.signature(Pipeline.ingest_audio).parameters) == ["self", "pcm16", "sample_rate", "ts"]
```

- [ ] **Step 2: run, expect FAIL** — ImportError on `EmotionState`.

- [ ] **Step 3: implement.** Append to `backend/contracts.py`:
```python


class EmotionState(_Contract):
    """Fusion -> dashboard / notifier (owned by Web). `snapshot` is a base64 JPEG or URL, or None."""

    ts: float
    emotion: Emotion
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["rules", "llm", "fused"]
    reason: str
    snapshot: str | None = None


class LLMResult(_Contract):
    """One parsed LLM interpretation (Web-internal, broadcast to the dashboard as type "llm")."""

    ts: float
    emotion: Emotion
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    provider: str
    model: str
    latency_ms: float
    trigger: str
```
Also edit the module docstring's last line to: `EmotionState and LLMResult (fusion -> dashboard) are owned by Web.`

In `backend/pipeline.py` add, after the `RulesCallback` alias:
```python
PIPELINE_METHODS: tuple[str, ...] = (
    "run", "latest_frame_jpeg", "mark_treat", "ingest_frame", "ingest_audio", "status", "stop",
)
```
and insert before `stop` (signatures and docs copied from CLAUDE.md "The handoff interface"):
```python
    def mark_treat(self, ts: float) -> None:
        """Treat button: set the rules engine's treat_event_recent for data.rules.treat_window_s."""
        raise NotImplementedError

    def ingest_frame(self, jpeg: bytes, ts: float) -> None:
        """Phone camera frame (browser source). Non-blocking, never raises, drops oldest when behind;
        a no-op with one warning unless the source type is `browser`."""
        raise NotImplementedError

    def ingest_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None:
        """Phone mic chunk: mono Int16 LE PCM. Same rules as ingest_frame."""
        raise NotImplementedError

    def status(self) -> dict:
        """{"source", "state": "running"|"stalled"|"stopped", "fps", "last_frame_age_s", "audio_ok"}."""
        raise NotImplementedError
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_contracts_web.py tests/data/test_contracts.py -q`.

- [ ] **Step 5: commit (shared files — separate commit, tell Person A)**
```bash
git add backend/contracts.py backend/pipeline.py tests/web/test_contracts_web.py
git commit -m "Shared: EmotionState + LLMResult contracts; Pipeline gains mark_treat/ingest_*/status per CLAUDE.md"
```
Message to Person A: "Added the 4 Pipeline stubs CLAUDE.md already lists + Web-owned EmotionState/LLMResult. No change to your models."

---

### Task 3: MockPipeline (scripted 90 s loop)

**Files:**
- Create: `backend/demo/__init__.py`, `backend/demo/mock_pipeline.py`
- Test: `tests/web/test_mock_pipeline.py`

**Interfaces:**
- Consumes: `FrameEvent`, `Features`, `AudioEvent`, `RulesLabel`, `EMOTIONS`, `PIPELINE_METHODS`; `backend.vision.keypoint_map.SKELETON` is **not** imported (Web must not import vision). The mock keeps its own local `_SKELETON`, which uses the same canonical names.
- Produces: `MockPipeline(config: dict, clock: Callable[[], float] = time.time)` with every `PIPELINE_METHODS` method plus `step(now) -> tuple[FrameEvent, list[AudioEvent], RulesLabel]` and `phase_at(now) -> str`. Module constants `PHASES: list[tuple[str, float]]` (name, duration) and `LOOP_S = 90.0`.

Script (sums to 90 s): `relaxed 12, excited 12, happy 18, disinterested 14, anxious 10, absent 6, relaxed 18`. The `absent` phase has `dog_detected=False`, which exercises the no-dog→unknown path. `mark_treat(ts)` re-anchors the script so that `ts` lands at the start of `excited`.

- [ ] **Step 1: failing test** `tests/web/test_mock_pipeline.py`
```python
import asyncio
import inspect

import cv2
import numpy as np

from backend.contracts import AudioEvent, FrameEvent, RulesLabel
from backend.demo.mock_pipeline import LOOP_S, PHASES, MockPipeline
from backend.pipeline import PIPELINE_METHODS, Pipeline

T0 = 1_000_000.0


def _mp():
    return MockPipeline({}, clock=lambda: T0)


def test_same_interface_as_pipeline():
    for name in PIPELINE_METHODS:
        mine, theirs = inspect.signature(getattr(MockPipeline, name)), inspect.signature(getattr(Pipeline, name))
        assert list(mine.parameters) == list(theirs.parameters), name


def test_script_covers_90s_in_order():
    assert sum(d for _, d in PHASES) == LOOP_S == 90.0
    mp = _mp()
    seen = []
    for t in np.arange(0, LOOP_S, 0.5):
        p = mp.phase_at(T0 + t)
        if not seen or seen[-1] != p:
            seen.append(p)
    assert seen == ["relaxed", "excited", "happy", "disinterested", "anxious", "absent", "relaxed"]
    assert mp.phase_at(T0 + LOOP_S + 1) == "relaxed"  # loops


def test_step_produces_valid_contracts_matching_phase():
    mp = _mp()
    frame, audio, rules = mp.step(T0 + 15)  # excited
    assert isinstance(frame, FrameEvent) and isinstance(rules, RulesLabel)
    assert frame.dog_detected and rules.emotion == "excited"
    assert all(isinstance(a, AudioEvent) for a in audio)
    FrameEvent.model_validate_json(frame.to_json())
    absent, _, r2 = mp.step(T0 + 68)  # absent phase: 66-72 s
    assert not absent.dog_detected and r2.emotion == "unknown"


def test_excited_phase_has_yips_and_fast_wag():
    mp = _mp()
    labels, wags = [], []
    for t in np.arange(12.0, 24.0, 0.125):
        f, a, _ = mp.step(T0 + t)
        labels += [e.label for e in a]
        if f.features.tail_wag_hz is not None:
            wags.append(f.features.tail_wag_hz)
    assert "yip" in labels and np.median(wags) > 3.0


def test_occasional_null_features_but_not_all():
    mp = _mp()
    feats = [mp.step(T0 + t)[0].features for t in np.arange(0, 12, 0.125)]
    nulls = sum(f.mouth_open is None for f in feats)
    assert 0 < nulls < len(feats) / 2


def test_mark_treat_jumps_to_excited():
    mp = _mp()
    assert mp.phase_at(T0 + 50) == "disinterested"
    mp.mark_treat(T0 + 50)
    assert mp.phase_at(T0 + 50.1) == "excited"


def test_latest_frame_is_jpeg_and_ingested_frame_wins():
    mp = _mp()
    assert mp.latest_frame_jpeg() is None
    mp.step(T0 + 1)
    img = cv2.imdecode(np.frombuffer(mp.latest_frame_jpeg(), np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[2] == 3
    ok, phone = cv2.imencode(".jpg", np.zeros((640, 360, 3), np.uint8))
    mp.ingest_frame(phone.tobytes(), T0 + 1.1)
    mp.step(T0 + 1.2)
    assert mp.latest_frame_jpeg() == phone.tobytes()
    assert mp.status()["source"] == "browser"


def test_status_and_ingest_never_raise():
    mp = _mp()
    mp.ingest_audio(b"\x00\x01" * 800, 48000, T0)
    mp.ingest_frame(b"not a jpeg", T0)
    s = mp.status()
    assert set(s) == {"source", "state", "fps", "last_frame_age_s", "audio_ok"}


async def test_run_calls_callbacks_then_stops():
    mp = MockPipeline({"data": {"fps": 20}})
    frames, rules = [], []
    task = asyncio.create_task(mp.run(frames.append, lambda a: None, rules.append))
    await asyncio.sleep(0.3)
    mp.stop()
    await asyncio.wait_for(task, 1.0)
    assert len(frames) >= 3 and len(rules) == len(frames)
    assert mp.status()["state"] == "stopped"
```

- [ ] **Step 2: run, expect FAIL** (module missing).

- [ ] **Step 3: implement** `backend/demo/__init__.py` (docstring only) and `backend/demo/mock_pipeline.py`:
```python
"""MockPipeline: same interface as backend.pipeline.Pipeline, scripted fake data (Web builds against it).

90 s loop: relaxed -> excited (treat) -> happy (eating) -> disinterested -> anxious -> absent -> relaxed.
Deterministic for a given clock (seeded RNG per step time), so tests can assert on it.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable

import cv2
import numpy as np

from backend.contracts import EMOTIONS, AudioEvent, Features, FrameEvent, RulesLabel

PHASES: list[tuple[str, float]] = [
    ("relaxed", 12.0), ("excited", 12.0), ("happy", 18.0), ("disinterested", 14.0),
    ("anxious", 10.0), ("absent", 6.0), ("relaxed", 18.0),
]
LOOP_S = sum(d for _, d in PHASES)
_EXCITED_START = 12.0
W, H = 640, 480

# per phase: tail_height, wag_hz, ear, mouth_open, body_lowering, motion, in_zone, (audio label, every s)
_PROFILE: dict[str, dict[str, Any]] = {
    "relaxed":       dict(tail=0.0, wag=0.5, ear="neutral", mouth=0.3, low=0.05, motion=0.08, zone=True, sound=None),
    "excited":       dict(tail=0.7, wag=4.5, ear="up", mouth=0.7, low=0.0, motion=0.85, zone=True, sound=("yip", 1.5)),
    "happy":         dict(tail=0.3, wag=2.0, ear="neutral", mouth=0.55, low=0.2, motion=0.35, zone=True, sound=None),
    "disinterested": dict(tail=-0.1, wag=0.0, ear="neutral", mouth=0.1, low=0.1, motion=0.03, zone=True, sound=None),
    "anxious":       dict(tail=-0.45, wag=0.3, ear="back", mouth=0.2, low=0.35, motion=0.7, zone=False, sound=("whimper", 2.0)),
}
_SKELETON = (("nose", "withers"), ("withers", "hip"), ("hip", "tail_base"), ("tail_base", "tail_tip"),
             ("withers", "left_front_paw"), ("withers", "right_front_paw"),
             ("hip", "left_back_paw"), ("hip", "right_back_paw"),
             ("nose", "left_ear_tip"), ("nose", "right_ear_tip"))


class MockPipeline:
    def __init__(self, config: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._fps = float((config.get("data") or {}).get("fps", 8))
        self._t0 = clock()
        self._running = False
        self._halt = False
        self._jpeg: bytes | None = None
        self._phone_jpeg: bytes | None = None
        self._phone_ts: float | None = None
        self._phone_audio_ts: float | None = None
        self._last_frame_ts: float | None = None
        self._last_sound: dict[str, float] = {}

    # -- script -------------------------------------------------------------------------------
    def _script_t(self, now: float) -> float:
        return (now - self._t0) % LOOP_S

    def phase_at(self, now: float) -> str:
        t = self._script_t(now)
        for name, dur in PHASES:
            if t < dur:
                return name
            t -= dur
        return PHASES[-1][0]

    def step(self, now: float) -> tuple[FrameEvent, list[AudioEvent], RulesLabel]:
        phase = self.phase_at(now)
        rng = np.random.default_rng(int(now * 1000) & 0xFFFFFFFF)
        if phase == "absent":
            frame = FrameEvent(ts=now, source="mock", dog_detected=False)
            rules = RulesLabel(ts=now, emotion="unknown", confidence=1.0,
                               scores={e: (1.0 if e == "unknown" else 0.0) for e in EMOTIONS})
            audio: list[AudioEvent] = []
        else:
            p = _PROFILE[phase]
            frame = self._frame(now, p, rng)
            audio = self._audio(now, p, rng)
            rules = self._rules(now, phase, rng)
        self._last_frame_ts = now
        self._jpeg = self._phone_jpeg if self._phone_fresh(now) else self._render(frame)
        return frame, audio, rules

    def _frame(self, now: float, p: dict, rng) -> FrameEvent:
        jitter = lambda s: float(rng.normal(0, s))  # noqa: E731
        maybe = lambda v: None if rng.random() < 0.08 else v  # noqa: E731
        f = Features(
            tail_height=float(np.clip(p["tail"] + jitter(0.05), -1, 1)),
            tail_wag_hz=maybe(max(0.0, p["wag"] + jitter(0.2))),
            ear_position=maybe(p["ear"]),
            mouth_open=maybe(float(np.clip(p["mouth"] + jitter(0.05), 0, 1))),
            body_lowering=float(np.clip(p["low"] + jitter(0.03), 0, 1)),
            motion_energy=float(np.clip(p["motion"] + jitter(0.05), 0, 1)),
            in_feeding_zone=p["zone"],
        )
        cx = W / 2 + (120 * math.sin(now * 0.8) if p["motion"] > 0.5 else 10 * math.sin(now * 0.3))
        cy = H * 0.62 + p["low"] * 60
        wag = 25 * math.sin(2 * math.pi * p["wag"] * now)
        kp = {
            "nose": (cx - 120, cy - 60), "left_ear_tip": (cx - 105, cy - 95), "right_ear_tip": (cx - 95, cy - 92),
            "withers": (cx - 60, cy - 30), "hip": (cx + 60, cy - 30), "tail_base": (cx + 75, cy - 35),
            "tail_tip": (cx + 120, cy - 35 - 60 * p["tail"] + wag),
            "left_front_paw": (cx - 65, cy + 50), "right_front_paw": (cx - 50, cy + 52),
            "left_back_paw": (cx + 55, cy + 50), "right_back_paw": (cx + 70, cy + 52),
        }
        body = {k: (float(x), float(y), float(np.clip(0.85 + jitter(0.05), 0, 1))) for k, (x, y) in kp.items()}
        bbox = (cx - 140, cy - 110, cx + 140, cy + 60)
        return FrameEvent(ts=now, source="mock", dog_detected=True, bbox=bbox, bbox_conf=0.9,
                          body_keypoints=body, face_landmarks=None, features=f)

    def _audio(self, now: float, p: dict, rng) -> list[AudioEvent]:
        if not p["sound"]:
            return []
        label, every = p["sound"]
        last = self._last_sound.get(label)
        if last is not None and 0 <= now - last < every:
            return []
        self._last_sound[label] = now
        return [AudioEvent(ts=now, label=label, score=float(np.clip(0.8 + rng.normal(0, 0.05), 0, 1)))]

    def _rules(self, now: float, phase: str, rng) -> RulesLabel:
        scores = {e: float(np.clip(rng.uniform(0.05, 0.3), 0, 1)) for e in EMOTIONS}
        scores[phase] = float(np.clip(0.75 + rng.normal(0, 0.05), 0, 1))
        scores["unknown"] = 0.3
        runner = max(s for e, s in scores.items() if e != phase)
        return RulesLabel(ts=now, emotion=phase, confidence=float(np.clip(scores[phase] - 0.5 * runner, 0, 1)),
                          scores=scores)

    def _render(self, frame: FrameEvent) -> bytes:
        img = np.full((H, W, 3), 60, np.uint8)
        cv2.putText(img, "MOCK", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
        kp = frame.body_keypoints
        for a, b in _SKELETON:
            if kp.get(a) and kp.get(b):
                cv2.line(img, tuple(map(int, kp[a][:2])), tuple(map(int, kp[b][:2])), (240, 240, 240), 3)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else (self._jpeg or b"")

    def _phone_fresh(self, now: float) -> bool:
        return self._phone_jpeg is not None and self._phone_ts is not None and now - self._phone_ts <= 2.0

    # -- Pipeline interface -------------------------------------------------------------------
    async def run(self, on_frame_event, on_audio_event, on_rules_label) -> None:
        self._running, self._halt = True, False
        period = 1.0 / self._fps
        try:
            while not self._halt:
                frame, audio, rules = self.step(self._clock())
                on_frame_event(frame)
                for a in audio:
                    on_audio_event(a)
                on_rules_label(rules)
                await asyncio.sleep(period)
        finally:
            self._running = False

    def latest_frame_jpeg(self) -> bytes | None:
        return self._jpeg

    def mark_treat(self, ts: float) -> None:
        self._t0 = ts - _EXCITED_START

    def ingest_frame(self, jpeg: bytes, ts: float) -> None:
        if jpeg[:2] == b"\xff\xd8":  # JPEG SOI; anything else is ignored, never raised
            self._phone_jpeg, self._phone_ts = bytes(jpeg), ts

    def ingest_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None:
        self._phone_audio_ts = ts

    def status(self) -> dict:
        now = self._clock()
        age = None if self._last_frame_ts is None else max(0.0, now - self._last_frame_ts)
        state = "running" if self._running else ("stopped" if self._halt else "running")
        return {"source": "browser" if self._phone_jpeg else "mock", "state": state, "fps": self._fps,
                "last_frame_age_s": age,
                "audio_ok": self._phone_audio_ts is not None and now - self._phone_audio_ts <= 2.0}

    def stop(self) -> None:
        self._halt = True
```
- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_mock_pipeline.py -q`. If `test_excited_phase_has_yips_and_fast_wag` fails because of the `_audio` debounce across the phase boundary, check that `_last_sound` is keyed per label (it is).

- [ ] **Step 5: commit** `git add backend/demo tests/web/test_mock_pipeline.py && git commit -m "Web step 2: scripted MockPipeline with Pipeline-conformance test"`

---

### Task 4: Final label + state machine + notifications + timeline (`state.py`)

**Files:**
- Create: `backend/fusion/state.py`
- Test: `tests/web/test_state.py`

**Interfaces:**
- Consumes: `RulesLabel`, `LLMResult`, `EmotionState`, `FrameEvent` from `backend.contracts`.
- Produces:
  - `StateConfig` dataclass (fields = keys of `web.state` in config) with `StateConfig.from_config(cfg: dict)`.
  - `combine(rules: RulesLabel | None, llm: LLMResult | None, *, now: float, dog_absent_s: float, cfg: StateConfig) -> EmotionState` (pure).
  - `rules_reason(rules: RulesLabel) -> str`.
  - `Span` dataclass: `emotion: str, start: float, end: float, confidence: float, reason: str, source: str`.
  - `Tick` dataclass: `live: EmotionState, current: EmotionState, changed: EmotionState | None, notify: EmotionState | None`.
  - `FusionState(cfg: StateConfig)` with `on_frame(ev: FrameEvent)`, `on_rules(r: RulesLabel)`, `on_llm(r: LLMResult)`, `tick(now: float) -> Tick`, `timeline() -> list[Span]`.

Semantics: see "Decisions" 1, 4, 5 at the top. `no dog > no_dog_unknown_s` → `unknown`, conf 1.0, source `rules`. No data yet → `unknown`, conf 0.0, reason `"Waiting for data."`. An LLM result is ignored once `now - llm.ts > llm_stale_s`.

- [ ] **Step 1: failing test** `tests/web/test_state.py`
```python
import pytest

from backend.contracts import EMOTIONS, FrameEvent, LLMResult, RulesLabel
from backend.fusion.state import FusionState, StateConfig, combine, rules_reason

CFG = StateConfig()


def R(emotion, conf=0.6, ts=0.0, second="happy", second_score=0.3):
    scores = {e: 0.0 for e in EMOTIONS}
    scores[second] = second_score
    scores[emotion] = conf
    return RulesLabel(ts=ts, emotion=emotion, confidence=conf, scores=scores)


def L(emotion, conf=0.8, ts=0.0):
    return LLMResult(ts=ts, emotion=emotion, confidence=conf, reason="LLM says so.",
                     provider="p", model="m", latency_ms=100.0, trigger="heartbeat")


def dog(ts, seen=True):
    return FrameEvent(ts=ts, source="t", dog_detected=seen)


# ---- combine() -------------------------------------------------------------------------------
def test_agree_is_fused_with_mean_confidence():
    s = combine(R("happy", 0.6), L("happy", 0.8), now=1, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.source, s.reason) == ("happy", "fused", "LLM says so.")
    assert s.confidence == pytest.approx(0.7)


def test_disagree_llm_wins_at_threshold():
    s = combine(R("relaxed"), L("excited", 0.7), now=1, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.source, s.confidence) == ("excited", "llm", 0.7)


def test_disagree_rules_win_below_threshold():
    s = combine(R("relaxed", 0.55), L("excited", 0.69), now=1, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.source, s.confidence) == ("relaxed", "rules", 0.55)
    assert s.reason.startswith("Rules: relaxed 0.55")


def test_no_dog_beyond_threshold_is_unknown_even_if_llm_confident():
    s = combine(R("happy"), L("happy", 0.95), now=5, dog_absent_s=2.01, cfg=CFG)
    assert (s.emotion, s.confidence) == ("unknown", 1.0)


def test_nothing_yet_is_unknown_zero_conf():
    s = combine(None, None, now=0, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.confidence, s.reason) == ("unknown", 0.0, "Waiting for data.")


def test_llm_only():
    assert combine(None, L("anxious"), now=1, dog_absent_s=0, cfg=CFG).source == "llm"


def test_rules_reason_top_two():
    assert rules_reason(R("excited", 0.82, second="happy", second_score=0.4)) == "Rules: excited 0.82, happy 0.40"


# ---- FusionState -----------------------------------------------------------------------------
def run(fs, t0, t1, rules=None, llm=None, dt=0.25, seen=True):
    ticks, t = [], t0
    while t <= t1 + 1e-9:
        fs.on_frame(dog(t, seen))
        if rules:
            fs.on_rules(R(rules, ts=t))
        if llm:
            fs.on_llm(L(llm, ts=t))
        ticks.append(fs.tick(t))
        t = round(t + dt, 6)
    return ticks


def test_first_state_adopted_immediately_without_notify():
    fs = FusionState(CFG)
    t = run(fs, 0, 0, rules="relaxed")[0]
    assert t.changed.emotion == "relaxed" and t.notify is None


def test_flicker_does_not_change_state():
    fs = FusionState(CFG)
    run(fs, 0, 1, rules="relaxed")
    ticks = run(fs, 1.25, 3.5, rules="excited")  # 2.25 s < 3 s
    ticks += run(fs, 3.75, 6, rules="relaxed")
    assert all(t.changed is None for t in ticks)
    assert fs.tick(6.25).current.emotion == "relaxed"


def test_change_after_persisting_3s_notifies_once():
    fs = FusionState(CFG)
    run(fs, 0, 1, rules="relaxed")
    ticks = run(fs, 1.25, 5, rules="excited")
    changed = [t for t in ticks if t.changed]
    assert len(changed) == 1 and changed[0].changed.emotion == "excited"
    assert changed[0].current.ts == pytest.approx(4.25)  # 1.25 + 3.0
    assert [t.notify.emotion for t in ticks if t.notify] == ["excited"]


def test_cooldown_suppresses_repeat_then_allows_after_60s():
    fs = FusionState(CFG)
    run(fs, 0, 0, rules="relaxed")
    a = run(fs, 0.25, 4, rules="excited")
    run(fs, 4.25, 8, rules="relaxed")
    c = run(fs, 8.25, 12, rules="excited")  # within 60 s of first excited notify
    assert sum(t.notify is not None and t.notify.emotion == "excited" for t in a + c) == 1
    run(fs, 12.25, 70, rules="relaxed")
    d = run(fs, 70.25, 74, rules="excited")
    assert any(t.notify and t.notify.emotion == "excited" for t in d)


def test_always_notify_repeats_while_persisting_after_cooldown():
    fs = FusionState(StateConfig(cooldown_s=10.0))
    run(fs, 0, 0, rules="relaxed")
    ticks = run(fs, 0.25, 30, rules="fearful")
    assert len([t for t in ticks if t.notify]) == 3  # at ~3.25, ~13.25, ~23.25


def test_quiet_unknown_never_notifies():
    fs = FusionState(CFG)
    run(fs, 0, 0, rules="relaxed")
    ticks = run(fs, 0.25, 6, seen=False)
    assert any(t.changed and t.changed.emotion == "unknown" for t in ticks)
    assert all(t.notify is None for t in ticks)


def test_stale_llm_result_ignored():
    fs = FusionState(CFG)
    fs.on_frame(dog(0)); fs.on_rules(R("relaxed", ts=0)); fs.on_llm(L("excited", 0.9, ts=0))
    assert fs.tick(1).live.emotion == "excited"
    fs.on_frame(dog(13)); fs.on_rules(R("relaxed", ts=13))
    assert fs.tick(13).live.emotion == "relaxed"


def test_timeline_spans_are_contiguous():
    fs = FusionState(CFG)
    run(fs, 0, 1, rules="relaxed")
    run(fs, 1.25, 6, rules="excited")
    spans = fs.timeline()
    assert [s.emotion for s in spans] == ["relaxed", "excited"]
    assert spans[0].end == spans[1].start == pytest.approx(4.25)
    assert spans[1].end == pytest.approx(6.0)
```
- [ ] **Step 2: run, expect FAIL** (module missing).

- [ ] **Step 3: implement** `backend/fusion/state.py`:
```python
"""Final label + state machine (Web). Pure: no I/O, no clock; `now` is always passed in.

combine(): CLAUDE.md "Final label". FusionState: persistence (>= persist_s), per-emotion cooldowns,
always-notify emotions, session timeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

from backend.contracts import EmotionState, FrameEvent, LLMResult, RulesLabel


@dataclass
class StateConfig:
    persist_s: float = 3.0
    no_dog_unknown_s: float = 2.0
    llm_override_conf: float = 0.7
    llm_stale_s: float = 12.0
    cooldown_s: float = 60.0
    cooldowns: dict[str, float] = field(default_factory=dict)
    always_notify: tuple[str, ...] = ("fearful", "aggressive", "disinterested")
    quiet: tuple[str, ...] = ("unknown",)
    tick_hz: float = 4.0
    live_hz: float = 1.0

    @classmethod
    def from_config(cls, cfg: dict) -> "StateConfig":
        src = (cfg.get("web") or {}).get("state") or {}
        known = {f.name for f in fields(cls)}
        kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in src.items() if k in known}
        return cls(**kw)

    def cooldown_for(self, emotion: str) -> float:
        return float(self.cooldowns.get(emotion, self.cooldown_s))


def rules_reason(rules: RulesLabel) -> str:
    top = sorted(((s, e) for e, s in rules.scores.items() if e != "unknown"), reverse=True)[:2]
    head = f"{rules.emotion} {rules.scores.get(rules.emotion, rules.confidence):.2f}"
    rest = [f"{e} {s:.2f}" for s, e in top if e != rules.emotion][:1]
    return "Rules: " + ", ".join([head, *rest])


def combine(rules: RulesLabel | None, llm: LLMResult | None, *, now: float, dog_absent_s: float,
            cfg: StateConfig) -> EmotionState:
    if dog_absent_s > cfg.no_dog_unknown_s:
        return EmotionState(ts=now, emotion="unknown", confidence=1.0, source="rules",
                            reason=f"No dog visible for more than {cfg.no_dog_unknown_s:g} s.")
    if rules is None and llm is None:
        return EmotionState(ts=now, emotion="unknown", confidence=0.0, source="rules", reason="Waiting for data.")
    if llm is None:
        return EmotionState(ts=now, emotion=rules.emotion, confidence=rules.confidence, source="rules",
                            reason=rules_reason(rules))
    if rules is None or (llm.emotion != rules.emotion and llm.confidence >= cfg.llm_override_conf):
        return EmotionState(ts=now, emotion=llm.emotion, confidence=llm.confidence, source="llm", reason=llm.reason)
    if llm.emotion == rules.emotion:
        return EmotionState(ts=now, emotion=rules.emotion, confidence=(rules.confidence + llm.confidence) / 2,
                            source="fused", reason=llm.reason)
    return EmotionState(ts=now, emotion=rules.emotion, confidence=rules.confidence, source="rules",
                        reason=rules_reason(rules))


@dataclass
class Span:
    emotion: str
    start: float
    end: float
    confidence: float
    reason: str
    source: str


@dataclass
class Tick:
    live: EmotionState
    current: EmotionState
    changed: EmotionState | None
    notify: EmotionState | None


class FusionState:
    def __init__(self, cfg: StateConfig) -> None:
        self.cfg = cfg
        self._rules: RulesLabel | None = None
        self._llm: LLMResult | None = None
        self._last_dog_ts: float | None = None
        self._any_frame = False
        self._current: EmotionState | None = None
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._last_notified: dict[str, float] = {}
        self._spans: list[Span] = []

    def on_frame(self, ev: FrameEvent) -> None:
        self._any_frame = True
        if ev.dog_detected:
            self._last_dog_ts = ev.ts

    def on_rules(self, r: RulesLabel) -> None:
        self._rules = r

    def on_llm(self, r: LLMResult) -> None:
        self._llm = r

    def _dog_absent_s(self, now: float) -> float:
        if not self._any_frame:
            return 0.0  # no frames at all -> "Waiting for data.", not "no dog"
        return math.inf if self._last_dog_ts is None else now - self._last_dog_ts

    def tick(self, now: float) -> Tick:
        llm = self._llm if self._llm and now - self._llm.ts <= self.cfg.llm_stale_s else None
        live = combine(self._rules, llm, now=now, dog_absent_s=self._dog_absent_s(now), cfg=self.cfg)
        changed = None
        if self._current is None:
            self._adopt(live, now)
            return Tick(live, live, live, None)  # first state never notifies
        if live.emotion == self._current.emotion:
            self._candidate = None
            self._current = live.model_copy(update={"ts": self._current.ts})
            self._refresh_span(now, live)
        else:
            if live.emotion != self._candidate:
                self._candidate, self._candidate_since = live.emotion, now
            if now - self._candidate_since >= self.cfg.persist_s - 1e-9:
                changed = live
                self._adopt(live, now)
            else:
                self._spans[-1].end = now
        return Tick(live, self._current, changed, self._decide_notify(now, changed))

    def _adopt(self, s: EmotionState, now: float) -> None:
        if self._spans:
            self._spans[-1].end = now
        self._current, self._candidate = s, None
        self._spans.append(Span(s.emotion, now, now, s.confidence, s.reason, s.source))

    def _refresh_span(self, now: float, s: EmotionState) -> None:
        sp = self._spans[-1]
        sp.end, sp.confidence, sp.reason, sp.source = now, s.confidence, s.reason, s.source

    def _decide_notify(self, now: float, changed: EmotionState | None) -> EmotionState | None:
        cur = self._current
        if cur is None or cur.emotion in self.cfg.quiet:
            return None
        last = self._last_notified.get(cur.emotion)
        if last is not None and now - last < self.cfg.cooldown_for(cur.emotion):
            return None
        if changed is None and cur.emotion not in self.cfg.always_notify:
            return None
        if changed is None and last is None:
            return None  # always-notify repeat only after a first notification
        self._last_notified[cur.emotion] = now
        return cur.model_copy(update={"ts": now})

    def timeline(self) -> list[Span]:
        return [Span(**vars(s)) for s in self._spans]
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_state.py -q`. The expected always-notify times are 3.25, 13.25 and 23.25 (the first notification comes with the change, then one after each 10 s cooldown). If the count is off by one because of float drift, keep the `- 1e-9` tolerances. Don't loosen the test.

- [ ] **Step 5: commit** `git add backend/fusion/state.py tests/web/test_state.py && git commit -m "Web step 5: final-label combine, persistence state machine, cooldowns, timeline"`

---

### Task 5: LLM reply parsing + trigger policy (pure)

**Files:**
- Create: `backend/fusion/llm_parse.py`, `backend/fusion/llm_triggers.py`
- Test: `tests/web/test_llm_parse.py`, `tests/web/test_llm_triggers.py`

**Interfaces — Produces:**
- `ParsedReply(NamedTuple)`: `emotion: str, confidence: float, reason: str`
- `parse_llm_reply(text: str | None) -> ParsedReply | None`
- `TriggerPolicy(min_interval_s=3.0, heartbeat_s=10.0, audio_score=0.6)` with `note_treat()`, `note_audio(ev: AudioEvent)`, `note_rules(r: RulesLabel)`, `due(now: float, in_flight: bool) -> str | None` (returns `"treat" | "audio" | "rules_change" | "heartbeat"` and records the call), plus `TriggerPolicy.from_config(cfg)`.

- [ ] **Step 1: failing tests**

`tests/web/test_llm_parse.py`:
```python
import pytest

from backend.fusion.llm_parse import parse_llm_reply

GOOD = '{"emotion": "excited", "confidence": 0.8, "reason": "Fast tail wag."}'


@pytest.mark.parametrize("text", [
    GOOD,
    f"```json\n{GOOD}\n```",
    f"```\n{GOOD}\n```",
    f"Sure! Here you go:\n{GOOD}\nHope that helps.",
    GOOD.replace("excited", " Excited "),
])
def test_accepts_messy_but_valid(text):
    r = parse_llm_reply(text)
    assert r and (r.emotion, r.confidence, r.reason) == ("excited", 0.8, "Fast tail wag.")


@pytest.mark.parametrize("text", [
    None, "", "no json here", "{not json}", '{"emotion": "sad", "confidence": 0.9, "reason": "x"}',
    '{"emotion": "happy", "confidence": "high", "reason": "x"}', '{"emotion": "happy"}',
    '["happy", 0.9]', '{"emotion": "happy", "confidence": NaN, "reason": "x"}',
])
def test_rejects_invalid(text):
    assert parse_llm_reply(text) is None


@pytest.mark.parametrize("raw,expected", [(1.7, 1.0), (-0.2, 0.0), (85, 0.85), (100, 1.0), ("0.4", 0.4)])
def test_confidence_clamped_or_percent(raw, expected):
    r = parse_llm_reply(f'{{"emotion": "happy", "confidence": {raw!r}, "reason": "x"}}'.replace("'", '"'))
    assert r.confidence == pytest.approx(expected)


def test_reason_single_line_trimmed_and_defaulted():
    r = parse_llm_reply('{"emotion": "happy", "confidence": 0.5, "reason": "line one\\nline two"}')
    assert r.reason == "line one"
    assert parse_llm_reply('{"emotion": "happy", "confidence": 0.5}').reason == "No reason given."
    long = parse_llm_reply('{"emotion":"happy","confidence":0.5,"reason":"' + "a" * 500 + '"}')
    assert len(long.reason) == 240


def test_picks_first_object_when_two():
    r = parse_llm_reply('{"emotion":"happy","confidence":0.5,"reason":"a"} {"emotion":"sad"}')
    assert r.emotion == "happy"
```

`tests/web/test_llm_triggers.py`:
```python
from backend.contracts import EMOTIONS, AudioEvent, RulesLabel
from backend.fusion.llm_triggers import TriggerPolicy


def R(e, ts=0.0):
    return RulesLabel(ts=ts, emotion=e, confidence=0.5, scores={x: 0.1 for x in EMOTIONS})


def test_heartbeat_fires_first_then_every_10s():
    p = TriggerPolicy()
    assert p.due(0.0, False) == "heartbeat"
    assert p.due(5.0, False) is None
    assert p.due(10.0, False) == "heartbeat"


def test_min_interval_gates_everything():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_treat()
    assert p.due(2.9, False) is None
    assert p.due(3.0, False) == "treat"


def test_priority_treat_over_audio_over_rules():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_rules(R("happy")); p.note_rules(R("excited"))
    p.note_audio(AudioEvent(ts=1, label="bark", score=0.9))
    p.note_treat()
    assert p.due(3.0, False) == "treat"
    assert p.due(6.0, False) is None  # lower-priority triggers were overwritten, not queued


def test_audio_threshold_and_non_dog_labels():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_audio(AudioEvent(ts=1, label="bark", score=0.6))      # not > 0.6
    p.note_audio(AudioEvent(ts=1, label="silence", score=0.99))  # not a dog sound
    assert p.due(3.0, False) is None
    p.note_audio(AudioEvent(ts=2, label="whimper", score=0.61))
    assert p.due(3.1, False) == "audio"


def test_rules_change_only_on_change():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_rules(R("happy"))  # first label: no change event
    assert p.due(3.0, False) is None
    p.note_rules(R("happy"))
    assert p.due(4.0, False) is None
    p.note_rules(R("anxious"))
    assert p.due(5.0, False) == "rules_change"


def test_in_flight_defers_latest_wins():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_audio(AudioEvent(ts=1, label="bark", score=0.9))
    assert p.due(4.0, True) is None      # call in flight: nothing fires, trigger kept
    assert p.due(4.5, False) == "audio"  # fires once flight is over
```
- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement**

`backend/fusion/llm_parse.py`:
```python
"""Defensive parsing of the LLM reply. Never raises; returns None on anything unusable."""

from __future__ import annotations

import json
import math
import re
from typing import NamedTuple

from backend.contracts import EMOTIONS

_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)
MAX_REASON = 240


class ParsedReply(NamedTuple):
    emotion: str
    confidence: float
    reason: str


def _first_object(s: str):
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(s[i:])
                return obj
            except json.JSONDecodeError:
                continue
    return None


def parse_llm_reply(text: str | None) -> ParsedReply | None:
    if not text:
        return None
    obj = _first_object(_FENCE.sub("", text))
    if not isinstance(obj, dict):
        return None
    emotion = str(obj.get("emotion", "")).strip().lower()
    if emotion not in EMOTIONS:
        return None
    try:
        conf = float(obj.get("confidence"))
    except (TypeError, ValueError):
        return None
    if math.isnan(conf) or math.isinf(conf):
        return None
    if 1.0 < conf <= 100.0 and float(conf).is_integer():
        conf /= 100.0  # "85" meaning 85 %
    conf = min(1.0, max(0.0, conf))
    reason = str(obj.get("reason") or "").strip()
    reason = (reason.splitlines()[0].strip() if reason else "") or "No reason given."
    return ParsedReply(emotion, conf, reason[:MAX_REASON])
```
Note: `json` accepts `NaN`, so the `isnan` check is what rejects it. `1.7` is not an integer, so it clamps to 1.0. `85` becomes 0.85 and `100` becomes 1.0.

`backend/fusion/llm_triggers.py`:
```python
"""When to call the LLM (CLAUDE.md "LLM interpreter"). Pure; `now` passed in."""

from __future__ import annotations

import math

from backend.contracts import AudioEvent, RulesLabel

_PRIORITY = {"treat": 3, "audio": 2, "rules_change": 1}
_DOG_SOUNDS = {"bark", "yip", "growl", "whimper", "howl"}


class TriggerPolicy:
    def __init__(self, min_interval_s: float = 3.0, heartbeat_s: float = 10.0, audio_score: float = 0.6):
        self.min_interval_s, self.heartbeat_s, self.audio_score = min_interval_s, heartbeat_s, audio_score
        self._last_call = -math.inf
        self._pending: str | None = None
        self._last_rules: str | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> "TriggerPolicy":
        llm = cfg["web"]["llm"]
        return cls(llm["min_interval_s"], llm["heartbeat_s"], llm["audio_trigger_score"])

    def _set(self, reason: str) -> None:
        if self._pending is None or _PRIORITY[reason] >= _PRIORITY[self._pending]:
            self._pending = reason

    def note_treat(self) -> None:
        self._set("treat")

    def note_audio(self, ev: AudioEvent) -> None:
        if ev.label in _DOG_SOUNDS and ev.score > self.audio_score:
            self._set("audio")

    def note_rules(self, r: RulesLabel) -> None:
        if self._last_rules is not None and r.emotion != self._last_rules:
            self._set("rules_change")
        self._last_rules = r.emotion

    def due(self, now: float, in_flight: bool) -> str | None:
        if in_flight or now - self._last_call < self.min_interval_s - 1e-9:
            return None
        reason = self._pending or ("heartbeat" if now - self._last_call >= self.heartbeat_s - 1e-9 else None)
        if reason:
            self._last_call, self._pending = now, None
        return reason
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_llm_parse.py tests/web/test_llm_triggers.py -q`

- [ ] **Step 5: commit** `git add backend/fusion/llm_parse.py backend/fusion/llm_triggers.py tests/web/test_llm_*.py && git commit -m "Web step 4a: defensive LLM reply parsing and trigger policy"`

---

### Task 6: Prompts + LLM interpreter (AsyncOpenAI, timeout, logging) + smoke script

**Files:**
- Create: `backend/fusion/prompts.py`, `backend/fusion/llm_interpreter.py`, `scripts/llm_smoke_test.py`
- Test: `tests/web/test_prompts.py`, `tests/web/test_llm_interpreter.py`

**Interfaces:**
- Consumes: `LLMSettings` (Task 1), `parse_llm_reply` (Task 5), `Features`, `AudioEvent`, `RulesLabel`, `LLMResult`.
- Produces:
  - `SYSTEM_PROMPT: str`; `build_user_content(features: list[tuple[float, Features]], audio: list[AudioEvent], rules: RulesLabel | None, image_b64: str | None, now: float) -> str | list[dict]` (string when there's no image; otherwise a list of `text` + `image_url` parts).
  - `encode_frame(jpeg: bytes, max_side: int) -> str | None` — base64 JPEG, downscaled so the long side is ≤ `max_side`; returns None if decoding fails.
  - `LLMInterpreter(settings: LLMSettings, client=None)`: `enabled: bool`, `async interpret(*, trigger: str, now: float, features, audio, rules, jpeg: bytes | None) -> LLMResult | None`, `last_call: dict | None` (for /status).

- [ ] **Step 1: failing tests**

`tests/web/test_prompts.py`:
```python
import base64
import json

import cv2
import numpy as np

from backend.contracts import EMOTIONS, AudioEvent, Features, RulesLabel
from backend.fusion.prompts import SYSTEM_PROMPT, build_user_content, encode_frame


def test_system_prompt_lists_vocabulary_and_json_only():
    for e in EMOTIONS:
        assert e in SYSTEM_PROMPT
    assert "JSON" in SYSTEM_PROMPT and "unknown" in SYSTEM_PROMPT


def _inputs():
    feats = [(9.0, Features(tail_height=0.5, tail_wag_hz=3.2)), (10.0, Features(motion_energy=0.7))]
    audio = [AudioEvent(ts=9.5, label="yip", score=0.8)]
    rules = RulesLabel(ts=10.0, emotion="excited", confidence=0.7, scores={e: 0.1 for e in EMOTIONS})
    return feats, audio, rules


def test_text_only_when_no_image():
    feats, audio, rules = _inputs()
    c = build_user_content(feats, audio, rules, None, now=10.0)
    assert isinstance(c, str)
    payload = json.loads(c[c.index("{"):])
    assert payload["rules_label"] == {"emotion": "excited", "confidence": 0.7}
    assert payload["recent_sounds"][0]["label"] == "yip"
    assert payload["features"][0]["t_minus_s"] == 1.0
    assert "motion_energy" in payload["features"][1] and "ear_position" not in payload["features"][1]


def test_image_part_when_vision():
    feats, audio, rules = _inputs()
    c = build_user_content(feats, audio, rules, "QUJD", now=10.0)
    assert [p["type"] for p in c] == ["text", "image_url"]
    assert c[1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_encode_frame_downscales_long_side():
    ok, buf = cv2.imencode(".jpg", np.zeros((1280, 720, 3), np.uint8))
    out = base64.b64decode(encode_frame(buf.tobytes(), 512))
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert max(img.shape[:2]) == 512 and img.shape[0] > img.shape[1]  # portrait kept
    assert encode_frame(b"junk", 512) is None
```

`tests/web/test_llm_interpreter.py`:
```python
import asyncio
from types import SimpleNamespace

from backend.fusion.llm_interpreter import LLMInterpreter
from backend.web.settings import LLMSettings

S = LLMSettings(provider="groq", base_url="u", api_key="k", model="m", vision=False, timeout_s=0.2)


class FakeClient:
    def __init__(self, reply=None, delay=0.0, exc=None):
        self.reply, self.delay, self.exc, self.calls = reply, delay, exc, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        msg = SimpleNamespace(content=self.reply)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


async def _go(client, settings=S, jpeg=None):
    it = LLMInterpreter(settings, client=client)
    return it, await it.interpret(trigger="treat", now=100.0, features=[], audio=[], rules=None, jpeg=jpeg)


async def test_good_reply_becomes_llm_result():
    it, r = await _go(FakeClient('{"emotion":"happy","confidence":0.9,"reason":"Loose mouth."}'))
    assert (r.emotion, r.confidence, r.provider, r.model, r.trigger) == ("happy", 0.9, "groq", "m", "treat")
    assert it.last_call["outcome"] == "ok" and it.last_call["total_tokens"] == 15


async def test_request_shape_json_mode_and_no_model_hardcoding():
    c = FakeClient('{"emotion":"happy","confidence":0.9,"reason":"x"}')
    await _go(c)
    kw = c.calls[0]
    assert kw["model"] == "m" and kw["response_format"] == {"type": "json_object"}
    assert kw["messages"][0]["role"] == "system" and isinstance(kw["messages"][1]["content"], str)


async def test_timeout_returns_none_and_logs():
    it, r = await _go(FakeClient("{}", delay=1.0))
    assert r is None and it.last_call["outcome"] == "timeout"


async def test_bad_output_returns_none():
    it, r = await _go(FakeClient("I think the dog is sad"))
    assert r is None and it.last_call["outcome"] == "bad_output"


async def test_error_returns_none_and_json_mode_disabled_on_bad_request():
    class BadRequestError(Exception):
        pass
    it, r = await _go(FakeClient(exc=BadRequestError("response_format not supported")))
    assert r is None and it.last_call["outcome"] == "error"
    assert it.json_mode is False  # next call retries without JSON mode


async def test_disabled_without_settings():
    it = LLMInterpreter(LLMSettings(), client=FakeClient("{}"))
    assert not it.enabled
    assert await it.interpret(trigger="x", now=0, features=[], audio=[], rules=None, jpeg=None) is None
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement**

`backend/fusion/prompts.py`:
```python
"""Prompt text and request building for the LLM interpreter. Iterate on wording here only."""

from __future__ import annotations

import base64
import json

import cv2
import numpy as np

from backend.contracts import EMOTIONS, AudioEvent, Features, RulesLabel

SYSTEM_PROMPT = f"""You interpret a dog's emotional state for a pet-monitoring dashboard.
A static camera watches the dog's feeding area; a microphone hears it.
You receive: recent posture features from a vision model, recent dog sounds, the label from a
heuristic rules engine, and (sometimes) one camera frame.

Answer with ONLY a JSON object, no prose, no code fences:
{{"emotion": "<one of: {', '.join(EMOTIONS)}>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}}

Rules:
- "emotion" must be exactly one word from the list above.
- "reason" cites observable cues only: posture, tail, ears, mouth, body, movement, sounds. Never guess
  about the owner, the dog's history or anything not visible or audible.
- If the dog is not clearly visible, answer "unknown" with low confidence.
- Features may be null (not measured); do not treat null as evidence.
- Be calibrated: use confidence >= 0.7 only when the cues clearly agree."""


def encode_frame(jpeg: bytes, max_side: int) -> str | None:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) if jpeg else None
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None


def build_user_content(features: list[tuple[float, Features]], audio: list[AudioEvent],
                       rules: RulesLabel | None, image_b64: str | None, now: float) -> str | list[dict]:
    payload = {
        "features": [
            {"t_minus_s": round(now - ts, 2), **{k: v for k, v in f.model_dump().items() if v is not None}}
            for ts, f in features
        ],
        "recent_sounds": [{"t_minus_s": round(now - a.ts, 2), "label": a.label, "score": round(a.score, 2)}
                          for a in audio],
        "rules_label": None if rules is None else {"emotion": rules.emotion, "confidence": round(rules.confidence, 2)},
    }
    text = "Observations (tail_height -1 tucked..1 high; others 0..1):\n" + json.dumps(payload)
    if image_b64 is None:
        return text
    return [{"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
```

`backend/fusion/llm_interpreter.py`:
```python
"""Provider-agnostic LLM interpreter (Groq | OpenRouter | any OpenAI-compatible endpoint).

Provider/model/vision come only from LLMSettings (env). Returns None on timeout, error or bad output;
the caller then keeps the rules label. Every call is logged as one JSON line on logger "llm".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from backend.contracts import AudioEvent, Features, LLMResult, RulesLabel
from backend.fusion.llm_parse import parse_llm_reply
from backend.fusion.prompts import SYSTEM_PROMPT, build_user_content, encode_frame
from backend.web.settings import LLMSettings

log = logging.getLogger("llm")


class LLMInterpreter:
    def __init__(self, settings: LLMSettings, client: Any = None) -> None:
        self.s = settings
        self.json_mode = settings.json_mode
        self.last_call: dict | None = None
        self._client = client
        if self._client is None and settings.enabled:
            from openai import AsyncOpenAI  # imported lazily: tests and demo mode never need it

            self._client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key, max_retries=0,
                                       timeout=settings.timeout_s, default_headers=settings.extra_headers or None)

    @property
    def enabled(self) -> bool:
        return self.s.enabled and self._client is not None

    async def interpret(self, *, trigger: str, now: float, features: list[tuple[float, Features]],
                        audio: list[AudioEvent], rules: RulesLabel | None, jpeg: bytes | None) -> LLMResult | None:
        if not self.enabled:
            return None
        image = encode_frame(jpeg, self.s.image_max_side) if (self.s.vision and jpeg) else None
        kw: dict[str, Any] = {
            "model": self.s.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": build_user_content(features, audio, rules, image, now)}],
            "temperature": self.s.temperature,
            "max_tokens": self.s.max_tokens,
        }
        if self.json_mode:
            kw["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        outcome, usage, result = "ok", None, None
        try:
            resp = await asyncio.wait_for(self._client.chat.completions.create(**kw), timeout=self.s.timeout_s)
            usage = getattr(resp, "usage", None)
            parsed = parse_llm_reply(resp.choices[0].message.content)
            if parsed is None:
                outcome = "bad_output"
            else:
                result = LLMResult(ts=now, emotion=parsed.emotion, confidence=parsed.confidence,
                                   reason=parsed.reason, provider=self.s.provider, model=self.s.model,
                                   latency_ms=round((time.perf_counter() - t0) * 1000, 1), trigger=trigger)
        except asyncio.TimeoutError:
            outcome = "timeout"
        except Exception as exc:  # noqa: BLE001 - any provider failure falls back to rules
            outcome = "error"
            if self.json_mode and type(exc).__name__ == "BadRequestError":
                self.json_mode = False
            log.warning("llm error: %s: %s", type(exc).__name__, exc)
        self.last_call = {
            "ts": now, "provider": self.s.provider, "model": self.s.model, "trigger": trigger,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "outcome": outcome,
            "vision": image is not None,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        log.info(json.dumps(self.last_call))
        return result
```

`scripts/llm_smoke_test.py`:
```python
"""One real call to the configured provider. Usage (reads .env):
    .venv/bin/python scripts/llm_smoke_test.py [path/to/frame.jpg]
Run once with Groq and once with OpenRouter env settings; prints the parsed result and latency."""

import asyncio
import json
import sys
import time

from dotenv import load_dotenv

from backend.contracts import EMOTIONS, AudioEvent, Features, RulesLabel
from backend.fusion.llm_interpreter import LLMInterpreter
from backend.web.settings import LLMSettings, load_config


async def main() -> None:
    load_dotenv()
    s = LLMSettings.from_config(load_config())
    if not s.enabled:
        sys.exit("LLM not configured: set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL in .env (and DEMO_MODE=0)")
    jpeg = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else None
    now = time.time()
    feats = [(now - 1, Features(tail_height=0.6, tail_wag_hz=3.8, motion_energy=0.8, ear_position="up"))]
    rules = RulesLabel(ts=now, emotion="excited", confidence=0.6, scores={e: 0.1 for e in EMOTIONS})
    it = LLMInterpreter(s)
    r = await it.interpret(trigger="smoke", now=now, features=feats,
                           audio=[AudioEvent(ts=now - 0.5, label="yip", score=0.8)], rules=rules, jpeg=jpeg)
    print(json.dumps(it.last_call, indent=2))
    print("RESULT:", r.to_json() if r else None)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_prompts.py tests/web/test_llm_interpreter.py -q`. Then `grep -rniE "llama|gpt-|claude|gemini|qwen" backend/` should print nothing (no hard-coded models).

- [ ] **Step 5: manual (user, needs keys):** fill `.env` for Groq, run `.venv/bin/python scripts/llm_smoke_test.py`, then switch `.env` to OpenRouter and run it again. Record both latencies.

- [ ] **Step 6: commit** `git add backend/fusion/prompts.py backend/fusion/llm_interpreter.py scripts/llm_smoke_test.py tests/web/test_prompts.py tests/web/test_llm_interpreter.py && git commit -m "Web step 4b: provider-agnostic LLM interpreter, prompts, smoke test"`

---

### Task 7: Notifiers — port, dashboard-only, Telegram

**Files:**
- Create: `backend/notify/__init__.py`, `backend/notify/base.py`, `backend/notify/dashboard.py`, `backend/notify/telegram.py`, `scripts/telegram_test.py`
- Test: `tests/web/test_notify.py`

**Interfaces — Produces:**
- `NotifyResult` (dataclass): `status: Literal["sent","failed","dashboard_only"]`, `channel: str`, `detail: str = ""`.
- `Notifier` Protocol: `async send(state: EmotionState, jpeg: bytes | None) -> NotifyResult`.
- `DashboardOnlyNotifier()` → always `NotifyResult("dashboard_only", "dashboard", "would send to owner")`.
- `TelegramNotifier(token: str, chat_id: str, client: httpx.AsyncClient | None = None, timeout_s: float = 10.0)`.
- `caption_for(state: EmotionState, profile: dict | None = None) -> str` — format from `ui/screens/09-notifications.html`: line 1 `"{emoji} {dog} seems {emotion} · {pct}%"` for negative emotions (anxious, fearful, aggressive, disinterested), otherwise `"{emoji} {dog} is {emotion} · {pct}%"`; line 2 the reason; line 3 `"{location} · {zone} · {Source} reading · HH:MM"` where Source is `Fused` / `AI` (for llm) / `Rules`. `EMOJI: dict[str, str]`, `NEGATIVE: frozenset[str]`.
- `TelegramNotifier(..., profile: dict | None = None)`; `build_notifier` passes `cfg["web"]["profile"]`.
- `build_notifier(cfg: dict, env: Mapping[str,str] | None = None) -> Notifier` (falls back to dashboard-only if the mode isn't telegram or a token/chat id is missing).

- [ ] **Step 1: failing test** `tests/web/test_notify.py`
```python
import httpx

from backend.contracts import EmotionState
from backend.notify import build_notifier, caption_for
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.notify.telegram import TelegramNotifier
from backend.web.settings import load_config

STATE = EmotionState(ts=1727340001.0, emotion="fearful", confidence=0.81, source="fused",
                     reason="Tail tucked, body low, whimpering.")


PROFILE = {"dog_name": "Bruno", "location": "Kitchen", "zone_label": "feeding area"}


def test_caption_matches_design():
    lines = caption_for(STATE, PROFILE).splitlines()
    assert lines[0].endswith("Bruno seems fearful · 81%")
    assert lines[1] == "Tail tucked, body low, whimpering."
    assert lines[2].startswith("Kitchen · feeding area · Fused reading · ")
    happy = STATE.model_copy(update={"emotion": "happy", "source": "llm"})
    first, _, last = caption_for(happy, PROFILE).splitlines()
    assert first.endswith("Bruno is happy · 81%") and "AI reading" in last
    assert "Your dog" in caption_for(STATE)  # no profile


async def test_dashboard_only():
    r = await DashboardOnlyNotifier().send(STATE, None)
    assert (r.status, r.detail) == ("dashboard_only", "would send to owner")


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_telegram_send_photo_ok():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})
    r = await TelegramNotifier("T", "42", client=_client(handler)).send(STATE, b"\xff\xd8jpeg")
    assert r.status == "sent" and seen[0].url.path == "/botT/sendPhoto"
    assert b'name="chat_id"' in seen[0].content and b"42" in seen[0].content


async def test_telegram_text_when_no_photo():
    seen = []
    r = await TelegramNotifier("T", "42", client=_client(lambda q: seen.append(q) or httpx.Response(200, json={"ok": True}))).send(STATE, None)
    assert r.status == "sent" and seen[0].url.path == "/botT/sendMessage"


async def test_telegram_retries_once_then_fails_without_raising():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500, json={"ok": False, "description": "boom"})
    r = await TelegramNotifier("T", "42", client=_client(handler)).send(STATE, b"\xff\xd8")
    assert r.status == "failed" and len(calls) == 2


async def test_telegram_network_error_is_failed_result():
    def handler(req):
        raise httpx.ConnectError("offline")
    r = await TelegramNotifier("T", "42", client=_client(handler)).send(STATE, None)
    assert r.status == "failed" and "offline" in r.detail


def test_build_notifier_falls_back(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={"NOTIFY_MODE": "telegram"})
    assert isinstance(build_notifier(cfg, env={}), DashboardOnlyNotifier)  # no token
    assert isinstance(build_notifier(cfg, env={"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1"}), TelegramNotifier)
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement**

`backend/notify/base.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from backend.contracts import EmotionState

EMOJI = {"happy": "😊", "excited": "🤩", "relaxed": "😌", "anxious": "😟", "fearful": "😨",
         "aggressive": "😠", "disinterested": "😐", "unknown": "❔"}
NEGATIVE = frozenset({"anxious", "fearful", "aggressive", "disinterested"})
_SOURCE = {"fused": "Fused", "llm": "AI", "rules": "Rules"}


@dataclass
class NotifyResult:
    status: Literal["sent", "failed", "dashboard_only"]
    channel: str
    detail: str = ""


class Notifier(Protocol):
    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult: ...


def caption_for(state: EmotionState, profile: dict | None = None) -> str:
    p = profile or {}
    dog = p.get("dog_name") or "Your dog"
    verb = "seems" if state.emotion in NEGATIVE else "is"
    when = datetime.fromtimestamp(state.ts).strftime("%H:%M")
    place = " · ".join(x for x in (p.get("location"), p.get("zone_label")) if x)
    tail = f"{place} · " if place else ""
    return (f"{EMOJI.get(state.emotion, '')} {dog} {verb} {state.emotion} · {round(state.confidence * 100)}%\n"
            f"{state.reason}\n{tail}{_SOURCE[state.source]} reading · {when}")
```
`backend/notify/dashboard.py`:
```python
from backend.contracts import EmotionState
from backend.notify.base import NotifyResult


class DashboardOnlyNotifier:
    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult:
        return NotifyResult("dashboard_only", "dashboard", "would send to owner")
```
`backend/notify/telegram.py`:
```python
"""Telegram Bot API notifier. Never raises: failures come back as NotifyResult(status="failed")."""

from __future__ import annotations

import logging

import httpx

from backend.contracts import EmotionState
from backend.notify.base import NotifyResult, caption_for

log = logging.getLogger("notify")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None, timeout_s: float = 10.0,
                 profile: dict | None = None):
        self._profile = profile
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat = chat_id
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def _once(self, state: EmotionState, jpeg: bytes | None) -> httpx.Response:
        cap = caption_for(state, self._profile)
        if jpeg:
            return await self._client.post(f"{self._base}/sendPhoto", data={"chat_id": self._chat, "caption": cap},
                                           files={"photo": ("snapshot.jpg", jpeg, "image/jpeg")})
        return await self._client.post(f"{self._base}/sendMessage", data={"chat_id": self._chat, "text": cap})

    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult:
        detail = ""
        for _ in range(2):  # one retry
            try:
                r = await self._once(state, jpeg)
                if r.status_code == 200 and r.json().get("ok"):
                    return NotifyResult("sent", "telegram")
                detail = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
        log.warning("telegram failed: %s", detail)
        return NotifyResult("failed", "telegram", detail)
```
`backend/notify/__init__.py`:
```python
from __future__ import annotations

import os
from typing import Mapping

from backend.notify.base import EMOJI, NEGATIVE, Notifier, NotifyResult, caption_for
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.notify.telegram import TelegramNotifier

__all__ = ["EMOJI", "NEGATIVE", "Notifier", "NotifyResult", "caption_for", "build_notifier",
           "DashboardOnlyNotifier", "TelegramNotifier"]


def build_notifier(cfg: dict, env: Mapping[str, str] | None = None) -> Notifier:
    env = os.environ if env is None else env
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if cfg["web"]["notify"]["mode"] == "telegram" and token and chat:
        return TelegramNotifier(token, chat, profile=cfg["web"]["profile"])
    return DashboardOnlyNotifier()
```
`scripts/telegram_test.py`:
```python
"""Send one test photo to the owner's Telegram.

Setup: in Telegram, message @BotFather -> /newbot -> copy the token into TELEGRAM_BOT_TOKEN.
Send any message to your new bot, then open https://api.telegram.org/bot<TOKEN>/getUpdates and copy
result[0].message.chat.id into TELEGRAM_CHAT_ID. Then: .venv/bin/python scripts/telegram_test.py
"""

import asyncio
import os
import sys
import time

import cv2
import numpy as np
from dotenv import load_dotenv

from backend.contracts import EmotionState
from backend.notify.telegram import TelegramNotifier


async def main() -> None:
    load_dotenv()
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    img = np.full((240, 320, 3), (80, 160, 80), np.uint8)
    cv2.putText(img, "Behind The Barks test", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    jpeg = cv2.imencode(".jpg", img)[1].tobytes()
    state = EmotionState(ts=time.time(), emotion="happy", confidence=0.9, source="rules", reason="Test message.")
    print(await TelegramNotifier(token, chat).send(state, jpeg))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_notify.py -q`
- [ ] **Step 5: manual (user):** create the bot, fill `.env`, run `scripts/telegram_test.py`, check the photo arrives.
- [ ] **Step 6: commit** `git add backend/notify scripts/telegram_test.py tests/web/test_notify.py && git commit -m "Web step 6: notifier port, dashboard-only and Telegram adapters"`

---

### Task 8: WebSocket hub (drop-oldest fan-out, history ring) + session event log

**Files:**
- Create: `backend/web/hub.py`, `backend/web/event_log.py`
- Test: `tests/web/test_hub.py`

**Interfaces — Produces:**
- `EventLog(log_dir: str | Path, clock=time.time)`: `.path: Path`, `.write(msg: dict)`, `.close()`. File: `<log_dir>/session-YYYYmmdd-HHMMSS.jsonl`, one envelope per line, flushed on each write.
- `Hub(client_queue=64, history=2000, frame_fps=8.0, clock=time.time, log: EventLog | None = None)`:
  - `publish(type: str, data: BaseModel | dict, meta: dict | None = None) -> dict | None`. Envelope: `{"type", "seq", "ts", "data", "meta"?}`. Returns the envelope, or None if a frame was throttled.
  - `subscribe() -> asyncio.Queue`, `unsubscribe(q)`, `history(since: int = 0) -> list[dict]` (every type except `frame`), `client_count: int`, `close()`.
  - `MESSAGE_TYPES = ("frame","audio","rules","emotion","llm","notification","treat","status")`. Publishing any other type raises `ValueError` (it's a programming error).
  - Slow client: each client queue is bounded, and when it's full the **oldest** message is dropped. `publish` never blocks.
  - Frames: at most `frame_fps` per second across the hub. Throttled frames are neither logged nor sent.

- [ ] **Step 1: failing test** `tests/web/test_hub.py`
```python
import json

import pytest

from backend.contracts import AudioEvent
from backend.web.event_log import EventLog
from backend.web.hub import Hub


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def test_envelope_and_model_serialisation():
    hub = Hub(clock=Clock())
    env = hub.publish("audio", AudioEvent(ts=1.0, label="bark", score=0.9))
    assert env["type"] == "audio" and env["seq"] == 1 and env["data"]["label"] == "bark" and "meta" not in env
    assert hub.publish("treat", {"ts": 2.0}, meta={"by": "button"})["meta"] == {"by": "button"}


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        Hub().publish("nope", {})


def test_slow_client_drops_oldest_never_blocks():
    hub = Hub(client_queue=3, clock=Clock())
    q = hub.subscribe()
    for i in range(5):
        hub.publish("treat", {"i": i})
    got = [q.get_nowait()["data"]["i"] for _ in range(q.qsize())]
    assert got == [2, 3, 4]


def test_history_excludes_frames_and_filters_since():
    c = Clock()
    hub = Hub(clock=c)
    hub.publish("treat", {"i": 1})
    hub.publish("frame", {"x": 1})
    hub.publish("treat", {"i": 2})
    assert [m["data"]["i"] for m in hub.history()] == [1, 2]
    assert [m["data"]["i"] for m in hub.history(since=1)] == [2]


def test_frame_throttle():
    c = Clock()
    hub = Hub(frame_fps=4, clock=c)
    q = hub.subscribe()
    assert hub.publish("frame", {}) is not None
    c.t += 0.1
    assert hub.publish("frame", {}) is None  # < 0.25 s
    c.t += 0.2
    assert hub.publish("frame", {}) is not None
    assert q.qsize() == 2


def test_unsubscribe_and_count():
    hub = Hub()
    q = hub.subscribe()
    assert hub.client_count == 1
    hub.unsubscribe(q)
    hub.unsubscribe(q)  # idempotent
    assert hub.client_count == 0


def test_event_log_writes_jsonl(tmp_path):
    log = EventLog(tmp_path / "out", clock=lambda: 0.0)
    hub = Hub(log=log, clock=Clock())
    hub.publish("treat", {"i": 1})
    hub.publish("frame", {"x": 1})
    log.close()
    lines = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert [m["type"] for m in lines] == ["treat", "frame"]
    assert log.path.name.startswith("session-") and log.path.suffix == ".jsonl"
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement**

`backend/web/event_log.py`:
```python
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path


class EventLog:
    def __init__(self, log_dir: str | Path, clock=time.time) -> None:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(clock()).strftime("%Y%m%d-%H%M%S")
        self.path = d / f"session-{stamp}.jsonl"
        self._f = self.path.open("a", encoding="utf-8")

    def write(self, msg: dict) -> None:
        if self._f.closed:
            return
        self._f.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self._f.flush()

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()
```
`backend/web/hub.py`:
```python
"""Fan-out of envelopes to WebSocket clients. publish() is sync and never blocks: each client has a
bounded queue and a full queue drops its oldest message (a slow client loses data, not the server)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from pydantic import BaseModel

from backend.web.event_log import EventLog

MESSAGE_TYPES = ("frame", "audio", "rules", "emotion", "llm", "notification", "treat", "status")


class Hub:
    def __init__(self, client_queue: int = 64, history: int = 2000, frame_fps: float = 8.0,
                 clock=time.time, log: EventLog | None = None) -> None:
        self._qsize, self._clock, self._log = client_queue, clock, log
        self._clients: set[asyncio.Queue] = set()
        self._history: deque[dict] = deque(maxlen=history)
        self._seq = 0
        self._frame_period = 1.0 / frame_fps if frame_fps > 0 else 0.0
        self._last_frame = -1e18

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._qsize)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def publish(self, type: str, data: BaseModel | dict[str, Any], meta: dict | None = None) -> dict | None:
        if type not in MESSAGE_TYPES:
            raise ValueError(f"unknown message type {type!r}")
        now = self._clock()
        if type == "frame":
            if now - self._last_frame < self._frame_period - 1e-9:
                return None
            self._last_frame = now
        self._seq += 1
        env: dict[str, Any] = {"type": type, "seq": self._seq, "ts": now,
                               "data": data.model_dump(mode="json") if isinstance(data, BaseModel) else data}
        if meta:
            env["meta"] = meta
        if type != "frame":
            self._history.append(env)
        if self._log:
            self._log.write(env)
        for q in list(self._clients):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(env)
        return env

    def history(self, since: int = 0) -> list[dict]:
        return [m for m in self._history if m["seq"] > since]

    def close(self) -> None:
        if self._log:
            self._log.close()
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_hub.py -q`
- [ ] **Step 5: commit** `git add backend/web/hub.py backend/web/event_log.py tests/web/test_hub.py && git commit -m "Web step 3a: WS hub with drop-oldest fan-out, history ring, session JSONL log"`

---

### Task 9: Runtime — wires pipeline → state / LLM / notifier / hub

**Files:**
- Create: `backend/web/runtime.py`
- Test: `tests/web/test_runtime.py`

**Interfaces:**
- Consumes: the Pipeline interface (duck-typed), `FusionState`/`StateConfig` (Task 4), `TriggerPolicy` (Task 5), `LLMInterpreter` (Task 6; anything with `.enabled`, `async interpret(...)` and `.last_call`), `Notifier` (Task 7), `Hub` (Task 8).
- Produces: `Runtime(cfg, pipeline, interpreter, notifier, hub, clock=time.time)` with:
  - `async start()`, `async stop()` (calls `pipeline.stop()` and cancels its tasks; safe to call twice)
  - `on_frame(ev)`, `on_audio(ev)`, `on_rules(r)` — the callbacks handed to `pipeline.run`. They're thread-safe: when called off the loop thread they re-dispatch with `call_soon_threadsafe`.
  - `step(now) -> None` — one state/LLM tick (sync; the tick loop calls it at `tick_hz`)
  - `treat(now: float | None = None) -> float`
  - `status() -> dict` with keys `pipeline, pipeline_status, demo_mode, llm, notify_mode, clients, fps, profile`. `llm` = `{enabled, online, provider, model, vision, last_call}`, where `online` is False when the last call's outcome was not `ok` (drives the "AI offline · rules only" state in `ui/screens/10-system-states.html`).
  - `timeline() -> list[dict]`
  - `pending: set[asyncio.Task]` (spawned LLM/notify tasks, so tests can await them)

- [ ] **Step 1: failing test** `tests/web/test_runtime.py`
```python
import asyncio
import threading

from backend.contracts import LLMResult
from backend.demo.mock_pipeline import MockPipeline
from backend.notify.base import NotifyResult
from backend.web.hub import Hub
from backend.web.runtime import Runtime
from backend.web.settings import load_config

T0 = 1_000_000.0


class FakeLLM:
    enabled = True

    def __init__(self, emotion="excited", conf=0.9, fail=False):
        self.calls, self.emotion, self.conf, self.fail = [], emotion, conf, fail
        self.last_call = None

    async def interpret(self, *, trigger, now, features, audio, rules, jpeg):
        self.calls.append(dict(trigger=trigger, n_features=len(features), n_audio=len(audio), jpeg=jpeg is not None))
        self.last_call = {"outcome": "error" if self.fail else "ok"}
        if self.fail:
            return None
        return LLMResult(ts=now, emotion=self.emotion, confidence=self.conf, reason="LLM.", provider="p",
                         model="m", latency_ms=1.0, trigger=trigger)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, state, jpeg):
        self.sent.append(state)
        return NotifyResult("dashboard_only", "dashboard", "would send to owner")


def make(tmp_path, llm=None):
    cfg = load_config(tmp_path / "none.yaml", env={})
    clock = {"t": T0}
    mp = MockPipeline(cfg, clock=lambda: clock["t"])
    hub = Hub(clock=lambda: clock["t"], frame_fps=1000)
    q = hub.subscribe()
    rt = Runtime(cfg, mp, llm or FakeLLM(), FakeNotifier(), hub, clock=lambda: clock["t"])
    return rt, mp, hub, q, clock


def feed(rt, mp, t):
    f, a, r = mp.step(t)
    rt.on_frame(f)
    for x in a:
        rt.on_audio(x)
    rt.on_rules(r)


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def settle(rt):
    while rt.pending:
        await asyncio.gather(*list(rt.pending), return_exceptions=True)


async def test_callbacks_publish_frame_audio_rules(tmp_path):
    rt, mp, hub, q, _ = make(tmp_path)
    feed(rt, mp, T0 + 12.0)  # excited: yip on first step
    assert {m["type"] for m in drain(q)} >= {"frame", "audio", "rules"}


async def test_step_emits_emotion_and_heartbeat_llm(tmp_path):
    llm = FakeLLM(emotion="relaxed")
    rt, mp, hub, q, clock = make(tmp_path, llm)
    for i in range(8):
        clock["t"] = T0 + i * 0.25
        feed(rt, mp, clock["t"])
    rt.step(clock["t"])
    await settle(rt)
    msgs = drain(q)
    assert any(m["type"] == "emotion" for m in msgs)
    assert llm.calls[0]["trigger"] == "heartbeat" and llm.calls[0]["n_features"] > 0 and llm.calls[0]["jpeg"]
    assert any(m["type"] == "llm" and m["data"]["emotion"] == "relaxed" for m in msgs)


async def test_treat_jumps_mock_publishes_and_triggers_llm(tmp_path):
    llm = FakeLLM()
    rt, mp, hub, q, clock = make(tmp_path, llm)
    rt.step(T0)
    await settle(rt)  # heartbeat at T0
    clock["t"] = T0 + 50
    assert mp.phase_at(clock["t"]) == "disinterested"  # 42-56 s
    rt.treat()
    assert mp.phase_at(clock["t"] + 0.1) == "excited"
    rt.step(clock["t"])
    await settle(rt)
    assert [c["trigger"] for c in llm.calls] == ["heartbeat", "treat"]
    assert any(m["type"] == "treat" for m in drain(q))


async def test_llm_failure_is_published_not_raised(tmp_path):
    rt, mp, hub, q, clock = make(tmp_path, FakeLLM(fail=True))
    feed(rt, mp, T0)
    rt.step(T0)
    await settle(rt)
    llm_msgs = [m for m in drain(q) if m["type"] == "llm"]
    assert llm_msgs and llm_msgs[0]["meta"] == {"ok": False}


async def test_state_change_notifies_and_publishes(tmp_path):
    # LLM disagrees at low confidence, so the rules drive the change (Decision 4: first state never notifies)
    rt, mp, hub, q, clock = make(tmp_path, FakeLLM(emotion="relaxed", conf=0.5))
    for i in range(4):  # 1 s of relaxed -> first state
        clock["t"] = T0 + i * 0.25
        feed(rt, mp, clock["t"]); rt.step(clock["t"]); await settle(rt)
    for i in range(20):  # 5 s of excited -> change persists 3 s -> notify
        clock["t"] = T0 + 12.0 + i * 0.25
        feed(rt, mp, clock["t"]); rt.step(clock["t"]); await settle(rt)
    notes = [m for m in drain(q) if m["type"] == "notification"]
    assert notes and notes[0]["data"]["state"]["emotion"] == "excited"
    assert notes[0]["data"]["status"] == "dashboard_only"
    assert notes[0]["data"]["state"]["snapshot"].startswith("data:image/jpeg;base64,")
    assert rt.notifier.sent[0].emotion == "excited"


async def test_callbacks_from_other_thread_are_marshalled(tmp_path):
    rt, mp, hub, q, clock = make(tmp_path)
    rt.bind_loop(asyncio.get_running_loop())
    f, _, _ = mp.step(T0)
    th = threading.Thread(target=rt.on_frame, args=(f,))
    th.start(); th.join()
    assert q.empty()  # not handled on the foreign thread
    await asyncio.sleep(0)
    assert [m["type"] for m in drain(q)] == ["frame"]


async def test_start_stop_with_real_loop(tmp_path):
    rt, mp, hub, q, clock = make(tmp_path)
    mp._clock = __import__("time").time
    rt.clock = __import__("time").time
    await rt.start()
    await asyncio.sleep(0.3)
    await rt.stop()
    await rt.stop()
    assert mp.status()["state"] == "stopped"
    st = rt.status()
    assert set(st) == {"pipeline", "pipeline_status", "demo_mode", "llm", "notify_mode", "clients", "fps", "profile"}
    assert st["profile"]["dog_name"] == "Bruno" and st["llm"]["online"] is True
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement** `backend/web/runtime.py`:
```python
"""Runtime: the only place that wires Pipeline -> FusionState / TriggerPolicy / LLM / Notifier -> Hub.

Pipeline callbacks may arrive on the loop thread (the contract) or, defensively, from worker threads;
off-thread calls are re-dispatched onto the loop. Nothing here blocks: LLM and notifier calls run as
tasks, and a failing task is logged, never propagated into the tick loop.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Any

from backend.contracts import AudioEvent, EmotionState, Features, FrameEvent, RulesLabel
from backend.fusion.llm_triggers import TriggerPolicy
from backend.fusion.state import FusionState, StateConfig
from backend.web.hub import Hub
from backend.web.settings import is_demo

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, cfg: dict, pipeline: Any, interpreter: Any, notifier: Any, hub: Hub, clock=time.time):
        self.cfg, self.pipeline, self.interpreter, self.notifier, self.hub, self.clock = (
            cfg, pipeline, interpreter, notifier, hub, clock)
        self.state = FusionState(StateConfig.from_config(cfg))
        self.triggers = TriggerPolicy.from_config(cfg)
        llm = cfg["web"]["llm"]
        self._context_s, self._max_samples = float(llm["context_s"]), int(llm["max_feature_samples"])
        self._features: deque[tuple[float, Features]] = deque(maxlen=256)
        self._audio: deque[AudioEvent] = deque(maxlen=64)
        self._rules: RulesLabel | None = None
        self._frame_times: deque[float] = deque(maxlen=64)
        self._last_live = -1e18
        self._llm_task: asyncio.Task | None = None
        self.pending: set[asyncio.Task] = set()
        self._tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._stopped = False

    # -- lifecycle ----------------------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop, self._loop_thread = loop, threading.get_ident()

    async def start(self) -> None:
        self.bind_loop(asyncio.get_running_loop())
        self._tasks = [
            asyncio.create_task(self._run_pipeline(), name="pipeline"),
            asyncio.create_task(self._tick_loop(), name="tick"),
        ]

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            self.pipeline.stop()
        except Exception:  # noqa: BLE001
            log.exception("pipeline.stop failed")
        for t in [*self._tasks, *self.pending]:
            t.cancel()
        await asyncio.gather(*self._tasks, *self.pending, return_exceptions=True)

    async def _run_pipeline(self) -> None:
        try:
            await self.pipeline.run(self.on_frame, self.on_audio, self.on_rules)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - dashboard keeps running on whatever state it has
            log.exception("pipeline.run crashed")
            self.hub.publish("status", {"pipeline": "crashed"})

    async def _tick_loop(self) -> None:
        period = 1.0 / float(self.state.cfg.tick_hz)
        while True:
            try:
                self.step(self.clock())
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            await asyncio.sleep(period)

    # -- callbacks ----------------------------------------------------------------------------
    def _off_loop(self, fn, arg) -> bool:
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(fn, arg)
            return True
        return False

    def on_frame(self, ev: FrameEvent) -> None:
        if self._off_loop(self.on_frame, ev):
            return
        self._frame_times.append(ev.ts)
        self.state.on_frame(ev)
        if ev.dog_detected:
            self._features.append((ev.ts, ev.features))
        self.hub.publish("frame", ev)

    def on_audio(self, ev: AudioEvent) -> None:
        if self._off_loop(self.on_audio, ev):
            return
        self._audio.append(ev)
        self.triggers.note_audio(ev)
        self.hub.publish("audio", ev)

    def on_rules(self, r: RulesLabel) -> None:
        if self._off_loop(self.on_rules, r):
            return
        self._rules = r
        self.state.on_rules(r)
        self.triggers.note_rules(r)
        self.hub.publish("rules", r)

    # -- actions ------------------------------------------------------------------------------
    def treat(self, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        try:
            self.pipeline.mark_treat(now)
        except NotImplementedError:
            log.warning("pipeline has no mark_treat yet")
        self.triggers.note_treat()
        self.hub.publish("treat", {"ts": now})
        return now

    def step(self, now: float) -> None:
        t = self.state.tick(now)
        if t.changed is not None:
            self.hub.publish("emotion", t.changed, meta={"changed": True})
            self._last_live = now
        elif now - self._last_live >= 1.0 / float(self.state.cfg.live_hz) - 1e-9:
            self.hub.publish("emotion", t.current, meta={"changed": False})
            self._last_live = now
        if t.notify is not None:
            self._spawn(self._notify(t.notify))
        in_flight = self._llm_task is not None and not self._llm_task.done()
        reason = self.triggers.due(now, in_flight) if self.interpreter.enabled else None
        if reason:
            self._llm_task = self._spawn(self._run_llm(reason, now))

    # -- tasks --------------------------------------------------------------------------------
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.pending.add(task)

        def done(t: asyncio.Task) -> None:
            self.pending.discard(t)
            if not t.cancelled() and t.exception():
                log.error("background task failed", exc_info=t.exception())
        task.add_done_callback(done)
        return task

    def _context(self, now: float) -> tuple[list[tuple[float, Features]], list[AudioEvent]]:
        feats = [(ts, f) for ts, f in self._features if ts >= now - self._context_s]
        if len(feats) > self._max_samples:
            step = len(feats) / self._max_samples
            feats = [feats[int(i * step)] for i in range(self._max_samples - 1)] + [feats[-1]]
        audio = [a for a in self._audio if a.ts >= now - self._context_s]
        return feats, audio

    async def _run_llm(self, reason: str, now: float) -> None:
        feats, audio = self._context(now)
        jpeg = self._latest_jpeg()
        result = await self.interpreter.interpret(trigger=reason, now=now, features=feats, audio=audio,
                                                  rules=self._rules, jpeg=jpeg)
        if result is not None:
            self.state.on_llm(result)
            self.hub.publish("llm", result, meta={"ok": True})
        else:
            self.hub.publish("llm", dict(self.interpreter.last_call or {"trigger": reason}), meta={"ok": False})

    async def _notify(self, state: EmotionState) -> None:
        jpeg = self._latest_jpeg()
        snap = f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}" if jpeg else None
        state = state.model_copy(update={"snapshot": snap})
        res = await self.notifier.send(state, jpeg)
        self.hub.publish("notification", {"state": state.model_dump(mode="json"), "status": res.status,
                                          "channel": res.channel, "detail": res.detail})

    def _latest_jpeg(self) -> bytes | None:
        try:
            return self.pipeline.latest_frame_jpeg()
        except Exception:  # noqa: BLE001
            return None

    # -- read models --------------------------------------------------------------------------
    def status(self) -> dict:
        try:
            ps = self.pipeline.status()
        except Exception:  # noqa: BLE001 - includes NotImplementedError on the stub
            ps = None
        ft = list(self._frame_times)
        fps = round((len(ft) - 1) / (ft[-1] - ft[0]), 1) if len(ft) > 1 and ft[-1] > ft[0] else 0.0
        s = getattr(self.interpreter, "s", None)
        return {
            "pipeline": self.cfg["web"]["pipeline"],
            "pipeline_status": ps,
            "demo_mode": is_demo(self.cfg),
            "llm": {"enabled": bool(self.interpreter.enabled),
                    "online": (self.interpreter.last_call or {}).get("outcome", "ok") == "ok",
                    "provider": getattr(s, "provider", None), "model": getattr(s, "model", None),
                    "vision": getattr(s, "vision", None), "last_call": self.interpreter.last_call},
            "notify_mode": self.cfg["web"]["notify"]["mode"],
            "clients": self.hub.client_count,
            "fps": fps,
            "profile": self.cfg["web"]["profile"],
        }

    def timeline(self) -> list[dict]:
        return [asdict(s) for s in self.state.timeline()]
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_runtime.py -q`
- [ ] **Step 5: commit** `git add backend/web/runtime.py tests/web/test_runtime.py && git commit -m "Web step 3b: runtime wiring pipeline -> state/LLM/notifier -> hub"`

---

### Task 10: FastAPI app — `/health /status /events /treat /video /ws`

**Files:**
- Create: `backend/main.py`
- Test: `tests/web/test_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_app(cfg: dict | None = None, *, pipeline=None, interpreter=None, notifier=None) -> FastAPI`, `build_pipeline(cfg) -> Any`, module-level `app = create_app()`.
  - `GET /health` → `{"ok": true}`
  - `GET /status` → `Runtime.status()`
  - `GET /events?since=0` → `{"events": [...], "timeline": [...]}`
  - `POST /treat` → `{"ts": float}`
  - `GET /video?max_frames=0` → `multipart/x-mixed-replace; boundary=frame` at `web.video.fps`. `max_frames>0` ends the stream after N frames (used by tests).
  - `WS /ws` → first message `{"type":"status","data":status}` (sent directly, not published), then every hub envelope.
  - CORS: allow all origins (dashboard on :3000, cloudflared tunnels).

- [ ] **Step 1: failing test** `tests/web/test_main.py`
```python
from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def client(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["video"]["fps"] = 50
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return TestClient(app)


def test_health_and_status(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/health").json() == {"ok": True}
        s = c.get("/status").json()
        assert s["pipeline"] == "mock" and s["llm"]["enabled"] is False


def test_ws_receives_status_then_live_types_and_treat(tmp_path):
    with client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        c.post("/treat")  # jump to excited -> yips
        seen = set()
        for _ in range(400):
            seen.add(ws.receive_json()["type"])
            if {"frame", "rules", "emotion", "treat", "audio"} <= seen:
                break
        assert {"frame", "rules", "emotion", "treat", "audio"} <= seen


def test_treat_recorded_in_events(tmp_path):
    with client(tmp_path) as c:
        ts = c.post("/treat").json()["ts"]
        ev = c.get("/events").json()
        assert any(m["type"] == "treat" and m["data"]["ts"] == ts for m in ev["events"])
        assert isinstance(ev["timeline"], list)


def test_video_is_multipart_jpeg(tmp_path):
    with client(tmp_path) as c:
        r = c.get("/video?max_frames=2")
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert r.content.count(b"--frame") == 2 and b"\xff\xd8" in r.content


def test_session_log_written(tmp_path):
    with client(tmp_path) as c:
        c.post("/treat")
    logs = list((tmp_path / "out").glob("session-*.jsonl"))
    assert logs and '"type":"treat"' in logs[0].read_text()
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement** `backend/main.py`:
```python
"""FastAPI app (Person B). Routes only; all behaviour lives in backend/web/runtime.py.

    uvicorn backend.main:app --reload          # pipeline from config.yaml web.pipeline (mock|real)
    DEMO_MODE=1 uvicorn backend.main:app       # LLM off, notifications dashboard-only
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.fusion.llm_interpreter import LLMInterpreter
from backend.notify import build_notifier
from backend.web.event_log import EventLog
from backend.web.hub import Hub
from backend.web.runtime import Runtime
from backend.web.settings import LLMSettings, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def build_pipeline(cfg: dict) -> Any:
    if cfg["web"]["pipeline"] == "real":
        from backend.pipeline import Pipeline  # lazy: heavy deps only when asked for

        return Pipeline(cfg)
    from backend.demo.mock_pipeline import MockPipeline

    return MockPipeline(cfg)


def create_app(cfg: dict | None = None, *, pipeline: Any = None, interpreter: Any = None,
               notifier: Any = None) -> FastAPI:
    if cfg is None:
        load_dotenv()
        cfg = load_config()
    web = cfg["web"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub = Hub(client_queue=web["ws"]["client_queue"], history=web["ws"]["history"],
                  frame_fps=web["ws"]["frame_fps"], log=EventLog(web["log_dir"]))
        rt = Runtime(cfg, pipeline or build_pipeline(cfg),
                     interpreter or LLMInterpreter(LLMSettings.from_config(cfg)),
                     notifier or build_notifier(cfg), hub)
        app.state.hub, app.state.rt = hub, rt
        await rt.start()
        try:
            yield
        finally:
            await rt.stop()
            hub.close()

    app = FastAPI(title="Behind The Barks", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/status")
    async def status() -> dict:
        return app.state.rt.status()

    @app.get("/events")
    async def events(since: int = 0) -> dict:
        return {"events": app.state.hub.history(since), "timeline": app.state.rt.timeline()}

    @app.post("/treat")
    async def treat() -> dict:
        return {"ts": app.state.rt.treat()}

    @app.get("/video")
    async def video(max_frames: int = 0) -> StreamingResponse:
        period = 1.0 / float(web["video"]["fps"])

        async def gen():
            sent = 0
            while max_frames <= 0 or sent < max_frames:
                jpeg = app.state.rt.pipeline.latest_frame_jpeg()
                if jpeg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() \
                        + b"\r\n\r\n" + jpeg + b"\r\n"
                    sent += 1
                await asyncio.sleep(period)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        q = app.state.hub.subscribe()
        try:
            await sock.send_json({"type": "status", "seq": 0, "data": app.state.rt.status()})
            while True:
                await sock.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            app.state.hub.unsubscribe(q)

    return app


app = create_app()
```

- [ ] **Step 4: run, expect PASS** — `pytest tests/web/test_main.py -q`, then the full suite: `.venv/bin/python -m pytest -q` (tests/data + tests/web all green, heavy ones skipped).
- [ ] **Step 5: manual smoke:** `make dev-backend`, then `curl -s localhost:8000/status | python -m json.tool`, `curl -s -X POST localhost:8000/treat`, and open `http://localhost:8000/video` in a browser (stick figure moving). `tail -f out/session-*.jsonl` should show events streaming.
- [ ] **Step 6: update CLAUDE.md** (same commit): fill in the web lines of "Commands" (`make dev-backend`, `make demo`, `make test-web`) and add `backend/web/` (settings, hub, event_log, runtime) plus `fusion/{prompts,llm_parse,llm_triggers}.py` to the repo layout.
- [ ] **Step 7: commit** `git add backend/main.py tests/web/test_main.py CLAUDE.md && git commit -m "Web step 3c: FastAPI app with WS, MJPEG, treat, events, status"`

---

## After Plan 1: roadmap (each gets its own detailed plan once Plan 1 is green)

These depend on Plan 1's WS envelope and endpoints being stable, so they're written in detail only after it lands. Scope and order are fixed now.

**Design source for Plans 2–4:** `ui/Claude Pet.html` (the bundled design) is unpacked into `ui/screens/*.html`, and summarised in `docs/superpowers/specs/2026-09-27-ui-screens.md` (tokens, copy, per-screen data needs). The desktop dashboard is `ui/screens/00-dashboard-desktop.html`; Tailwind v4 tokens are in `11-design-tokens.html`.

**Plan 2 — Dashboard (PROMPTS-WEB Steps 0.2, 1-TS, 7, 8)**
1. Next.js + TypeScript + Tailwind scaffold in `frontend/`; `NEXT_PUBLIC_BACKEND_URL`; a "backend: connected" indicator from `/health` (this closes Step 0).
2. `scripts/gen_ts_types.py` → `frontend/lib/contracts.ts`, generated from the pydantic JSON schema, plus a test that fails when the generated file is stale.
3. `useBackendSocket` hook: auto-reconnect with backoff, typed envelopes, a history bootstrap from `/events`.
4. Layout: MJPEG video panel, emotion card (label, emoji, confidence bar, reason, source badge, "updated Xs ago"), Treat button, status bar, toast stack. One colour per emotion, shared across all components.
5. Canvas overlay (bbox + skeleton from frame messages, skipping null keypoints), timeline (spans + treat/notification markers), audio strip, collapsible feature panel, `POST /mode` live/fallback toggle.

**Plan 3 — Phone camera (Step 8b), the riskiest item**
1. `WS /ingest`: JSON hello, then binary `0x01`+JPEG and `0x02`+Int16 PCM, forwarded to `pipeline.ingest_*` with the server receipt time. One phone at a time (a second connection gets closed with a reason). A `status` broadcast carries connection state and received fps. Tested with the TestClient sending real binary frames.
2. `/camera` page: permission explainer → camera picker → feeding-zone guide → streaming view. Canvas → JPEG at ~640 px and quality 0.7, skipping a frame when `bufferedAmount` is high. AudioWorklet → Int16 in ~100 ms chunks. Wake Lock, reconnect, portrait and landscape.
3. Two cloudflared tunnels plus `docs/DEMO_RUNBOOK.md`. Acceptance: 5 minutes of streaming on a real phone, then kill and reopen the page; the dashboard recovers without a restart.

**Plan 4 — Demo mode (Step 9), plus Steps 10–11**
1. `scripts/precompute_demo.py`: replay Person A's `events/<clip>.jsonl` through `FusionState` + `LLMInterpreter` in accelerated time and write `backend/demo/cache/<clip>.timeline.json`. Runs while online; the output is committed.
2. The frontend plays the clip in a `<video>` and renders events keyed to `currentTime`, so seeking and pausing work. It needs zero network. Also a clip picker and a hotkey.
3. Integrate the real `Pipeline` (Step 10: mismatch report with an owner for each issue), provider benchmark, failure drills, a 20-minute soak test (Step 11).

**Blocked on Person A:** the real `Pipeline` implementation (their Step 9), the `events.jsonl` files and `data/fallback/manifest.json`. Until those arrive, everything runs on MockPipeline.

---

## Self-review (done while writing)

**Spec coverage (CLAUDE.md + PROMPTS-WEB Steps 0–6):**
- Step 0: deps, env, config web section, Makefile → Task 1. The Next.js "connected" check → Plan 2.1.
- Step 1: EmotionState and the full Pipeline interface → Task 2. TS types → Plan 2.2.
- Step 2: MockPipeline with the scripted loop, ~8 fps, null features, audio, rules, generated JPEG, `mark_treat` jump, `pipeline: mock|real` → Tasks 3 + 10 (`build_pipeline`).
- Step 3: lifespan start/stop, thread-safe callbacks, WS envelope types, frame throttle, drop-not-queue, `/video`, `/treat`, `/events`, JSONL log, `/health`, `/status` → Tasks 8–10.
- Step 4: env-only provider settings, OpenRouter headers, triggers, rate limit, latest-wins, 3 s context, 512 px image, `prompts.py`, JSON mode with defensive parsing, 8 s timeout, per-call log, smoke script → Tasks 1, 5, 6, 9.
- Step 5: combine rules, staleness, 3 s persistence, change + 1 Hz emotion broadcasts, cooldowns, always-notify, timeline → Tasks 4, 9.
- Step 6: sendPhoto with caption, retry once, never raises, `NOTIFY_MODE`, the "would send to owner" status, test script → Tasks 7, 9.
- Non-negotiables: offline demo (partly here via `DEMO_MODE` → LLM off and dashboard-only; the full version is Plan 4), fixed vocabulary (enforced by `Emotion` Literal + parser), no hard-coded model names (grep check in Task 6).

**Consistency checks:** `LLMSettings.from_config(cfg, env)` is used the same way in Tasks 1, 6 and 10. `Runtime(cfg, pipeline, interpreter, notifier, hub, clock)` matches in Tasks 9 and 10. The Hub types list matches the PROMPTS-WEB envelope list. `TriggerPolicy.due(now, in_flight)` matches Tasks 5 and 9. `FusionState.tick(now) -> Tick(live, current, changed, notify)` matches Tasks 4 and 9.

**Known risks and mitigations:**
- Version pins in `requirements-web.txt` may not all exist. Step 1 says to bump to the nearest version and record it.
- TestClient WS tests depend on real time (8 fps mock). They're bounded to 400 messages; if they're flaky, set `web.ws.frame_fps` higher in the test cfg.
- Person A's `pipeline.py` may change while we work. Task 2 is a small, separate commit, so rebase on conflict and keep their edits.
