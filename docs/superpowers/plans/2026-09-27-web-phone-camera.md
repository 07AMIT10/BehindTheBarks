# Phone Camera Page and /ingest Implementation Plan (Person B — Plan 3 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The phone becomes the live camera and microphone. A mobile-first `/camera` page captures camera + mic in the phone's browser and streams them over a new WebSocket `/ingest` to the backend, which stamps receipt time and feeds the normal `pipeline.ingest_*` path. The dashboard shows phone state live (device label, camera-blocked notice, disconnect recovery), and a script plus a runbook section let us test and demo the whole path without guesswork.

**Architecture:** Same ports-and-adapters shape as Plan 1. The only new backend logic is a small testable unit (`backend/web/ingest.py`: hello parsing, message dispatch constants, per-phone fps/status) plus thin phone state on `Runtime`; `backend/main.py` stays routing-only. All phone-buying frontend logic that can be pure lives in `frontend/lib/ingest.ts` with Vitest tests; the `/camera` page is client-only (`next/dynamic` `ssr: false`, exactly like Plan 2's `DashboardClient`) because it touches `getUserMedia`, `AudioWorklet` and Wake Lock. `MockPipeline` already accepts `ingest_*` and serves phone frames from `latest_frame_jpeg()`, so no mock changes are needed.

**Tech Stack:** Python 3.11-compatible code (dev box runs 3.12), FastAPI WebSocket, `websockets.sync.client` (already present via `uvicorn[standard]`), numpy + opencv for the test script; Next.js 16.3.6 App Router, React 19, TypeScript 5, Tailwind v4, Vitest 5. No new runtime dependencies on either side.

**Spec:** `CLAUDE.md` ("Phone camera (primary live source)", "The handoff interface"), `PROMPTS-WEB.md` Step 8b, `docs/superpowers/specs/2026-09-27-ui-screens.md` + `ui/screens/05-camera-setup.html`, `06-camera-streaming-portrait.html`, `07-camera-streaming-landscape.html`, `08-camera-permission-denied.html`, `10-system-states.html` (section "Camera or microphone blocked"). **When a string or geometry is unclear, open the screen file and copy it verbatim (curly apostrophes included).**

## Global Constraints

- Web owns: `backend/main.py`, `backend/web/`, `backend/demo/`, `frontend/`, `scripts/ingest_client.py`, `config.yaml` (nothing needed there), `Makefile`, `docs/DEMO_RUNBOOK.md`, env setup. New web-only modules live in `backend/web/ingest.py`, `frontend/lib/ingest.ts`, `frontend/components/Camera*.tsx`, `frontend/app/camera/`, `frontend/public/pcm-worklet.js`.
- **Never edit** Person A's files (`backend/sources.py`, `backend/vision/`, `backend/audio/`, `backend/fusion/rules.py`, `data/`). `scripts/fake_phone.py` and `backend/sources.py` are reference only (they define what the server side must accept). Tell Person A about the two new optional hello fields; the `CLAUDE.md` contract note goes in the same commit as the route (Task 3), per the "change a contract only … update this file in the same commit" rule.
- Never import `backend.vision`, `backend.audio` or `backend.fusion.rules` from web code.
- The ingest path never blocks the event loop: `pipeline.ingest_*` are non-blocking by contract; the handler does no decoding, no resampling, no disk I/O. `ts = time.time()` is stamped at server receipt.
- Emotion labels only from `contracts.EMOTIONS` (nothing new here, just don't add any).
- Tests never touch the network. Backend tests use FastAPI `TestClient.websocket_connect` with `send_text`/`send_bytes` against `MockPipeline`.
- Commands run from the repo root with `.venv/bin/python`; frontend commands run inside `frontend/` (`npm run test`, `npm run typecheck`, `npm run lint`, `npm run build`).
- Copy strings are taken verbatim from the UI spec (curly apostrophes `’`, middle dot `·` included). Emotion colours only via CSS vars (camera page is dark-only but still uses tokens, never hard-coded hex except the fixed overlay colours `#7FE3E8` / `#FF5A4E` / `#1B1D1F` / `#F4F4F2` / `#17181A` copied from screens 05–08).
- Every interactive element is at least 44 px tall (`h-11`) except the compact pills the design shows smaller.
- Backend URL on the camera page: `?backend=` query param > `NEXT_PUBLIC_BACKEND_URL` > page host on port 8000 — i.e. reuse `lib/config.ts` `backendBase()`, then append `/ingest`.

## Decisions (interpretations of the spec, flag to user if wrong)

1. **Close codes.** A second phone is rejected with code **4409** (`ANOTHER_PHONE_STREAMING`), a missing/invalid hello with **4400**. Both are in the application range (4000–4999); no standard code fits "one phone at a time".
2. **Optional hello fields.** `facing: "back" | "front"` (default `"back"`) and `camera: bool` (default `true`; `false` = mic-only after the user denied the camera). Documented in `CLAUDE.md` in the Task 3 commit.
3. **Received fps** is `(n-1)/span` over a 5 s sliding window of server-receipt timestamps (verified: 8 frames at 8 fps reads exactly `8.0`).
4. **Status broadcast cadence.** The hub gets `{"phone": {...}}` on connect, on disconnect, and every ~2 s while connected — driven from `Runtime.step()` (the existing 4 Hz tick loop), not a new background task. The Plan 2 reducer merges partial status envelopes, so dashboards update live with no reducer change.
5. **Connection quality** is a pure function of socket backpressure + recent reconnects: 4/Excellent (idle socket, no reconnects), 3/Good (some bytes queued), 2/Fair (≥64 KB queued or ≥1 reconnect), 1/Poor (≥512 KB queued or ≥3 reconnects).
6. **Sent-fps pill** shows measured frames actually handed to the socket (post-`toBlob`), not the requested `?fps=`.
7. **Mic-only phones** send `camera: false`, stream audio only, and read as `{device} · mic only` in the StatusBar.
8. **`scripts/ingest_client.py` needs no new dependency**: `websockets` ships with `uvicorn[standard]` (verified present in `.venv`), plus numpy/opencv which are already pinned.
9. **Dark theme on `/camera`** is forced by adding `.dark` to `<html>` while the page is mounted (restored on leave); the dog's name is fetched from `/status` profile with a `"Bruno"` fallback.
10. **"How to fix"** is a small inline explainer inside `SystemNotice` (no new route); **"Use a demo clip"** calls the existing `setMode("demo")` and stays disabled until `/status` lists `"demo"` in `modes` (Plan 4 adds it).

## File Structure

| File | Responsibility |
|---|---|
| `backend/web/ingest.py` | `PhoneHello`, `parse_hello`, `IngestSession` (fps window, status dict), close-code constants (Task 1) |
| `tests/web/test_ingest.py` | Unit tests: hello parsing + session fps/status (Task 1) |
| `backend/web/runtime.py` | + phone state (`phone_connected`/`phone_disconnected`), `"phone"` in `status()`, ~2 s broadcast in `step()` (Task 2) |
| `tests/web/test_runtime.py` | Update expected status key set (`+ "phone"`), append broadcast test (Task 2) |
| `backend/main.py` | + `/ingest` WebSocket route, routing only (Task 3) |
| `tests/web/test_ingest_route.py` | Route tests: frames/audio reach MockPipeline, 4409, 4400, disconnect broadcast (Task 3) |
| `CLAUDE.md` | Contract note: optional `facing`/`camera` + close codes (Task 3, same commit) |
| `scripts/ingest_client.py` | WS test client: synthetic or video-file frames + sine audio (Task 4) |
| `frontend/lib/ingest.ts` | Pure phone logic: hello builder, kind-byte framing, float→int16, RMS→bars, quality, downscale, fps param, backoff, clock (Task 5) |
| `frontend/lib/ingest.test.ts` | Vitest tests for the above (Task 5) |
| `frontend/public/pcm-worklet.js` | Tiny AudioWorklet: mono Float32 ~100 ms chunks (Task 6) |
| `frontend/components/CameraClient.tsx` | Setup → streaming → blocked UI + capture/stream/reconnect logic (Task 6) |
| `frontend/components/CameraPage.tsx` | Client-only wrapper (`next/dynamic`, `ssr: false`) (Task 6) |
| `frontend/app/camera/page.tsx` | Server shell + metadata (Task 6) |
| `frontend/lib/types.ts` | `Status.phone` gains `camera/width/height/sample_rate/last_frame_age_s` (Task 7) |
| `frontend/components/SystemNotice.tsx` | Camera-blocked banner + inline fix + demo-clip button (Task 7) |
| `frontend/components/StatusBar.tsx` | Mic-only device label (Task 7) |
| `frontend/components/Dashboard.tsx` | Pass `demoAvailable`/`onMode` into `SystemNotice` (Task 7) |
| `Makefile` | `tunnel-frontend` / `tunnel-backend` (Task 8) |
| `docs/DEMO_RUNBOOK.md` | New file; "Phone camera" section (Task 8) |

---

### Task 1: Ingest unit — hello parsing, session fps, status dict

**Files:**
- Create: `backend/web/ingest.py`, `tests/web/test_ingest.py`

**Interfaces — Produces:**
- `PhoneHello` dataclass: `width, height, fps, sample_rate: int`; `device: str = "phone"`; `facing: str = "back"` (`"back" | "front"`); `camera: bool = True`.
- `parse_hello(raw: str) -> PhoneHello` — raises `ValueError` on non-JSON, wrong `type`, bad `facing`/`camera`/`device`, or non-integer/out-of-range `width` (1–8192), `height` (1–8192), `fps` (1–60), `sample_rate` (8000–192000).
- `IngestSession(hello, clock=time.time)` with `note_frame(ts)`, `note_audio()`, `received_fps(now=None) -> float` (`(n-1)/span` over the last 5 s; `0.0`/`1.0` for < 2 samples), `status(now=None) -> dict` (`connected, device, facing, camera, fps, width, height, sample_rate, last_frame_age_s`), counters `frames_received`/`audio_received`, `last_frame_ts`.
- Constants: `FRAME_KIND = 0x01`, `AUDIO_KIND = 0x02`, `HELLO_TIMEOUT_S = 10.0`, `STATUS_EVERY_S = 2.0`, `FPS_WINDOW_S = 5.0`, `CLOSE_IN_USE = 4409`, `CLOSE_BAD_HELLO = 4400`.

- [ ] **Step 1: failing test** `tests/web/test_ingest.py`
```python
import json

import pytest

from backend.web.ingest import IngestSession, PhoneHello, parse_hello

HELLO = {"type": "hello", "width": 640, "height": 480, "fps": 8, "sample_rate": 48000,
         "device": "Pixel 7", "facing": "back"}


def test_parse_hello_defaults():
    h = parse_hello(json.dumps({"type": "hello", "width": 640, "height": 480, "fps": 8,
                                "sample_rate": 44100}))
    assert h == PhoneHello(width=640, height=480, fps=8, sample_rate=44100, device="phone",
                           facing="back", camera=True)


def test_parse_hello_full_and_mic_only():
    h = parse_hello(json.dumps({**HELLO, "facing": "front", "camera": False}))
    assert (h.device, h.facing, h.camera) == ("Pixel 7", "front", False)


@pytest.mark.parametrize("raw", [
    "not json",
    json.dumps({"type": "bye"}),
    json.dumps({**HELLO, "facing": "side"}),
    json.dumps({**HELLO, "camera": "yes"}),
    json.dumps({**HELLO, "fps": 0}),
    json.dumps({**HELLO, "sample_rate": 7000}),
    json.dumps({**HELLO, "width": 1.5}),
    json.dumps({**HELLO, "device": "  "}),
])
def test_parse_hello_rejects(raw):
    with pytest.raises(ValueError):
        parse_hello(raw)


def test_session_fps_sliding_window():
    s = IngestSession(PhoneHello(640, 480, 8, 48000), clock=lambda: 1000.0)
    assert s.received_fps(1000.0) == 0.0
    for i in range(8):
        s.note_frame(100.0 + i * 0.125)  # 8 fps for 1 s
    assert s.received_fps(101.0) == pytest.approx(8.0)
    assert s.received_fps(200.0) == 0.0  # window expired
    assert s.status(101.0)["last_frame_age_s"] == pytest.approx(0.125)
```

- [ ] **Step 2: run, expect FAIL** — `.venv/bin/python -m pytest tests/web/test_ingest.py -q` → `ModuleNotFoundError: backend.web.ingest`.

- [ ] **Step 3: implement** `backend/web/ingest.py`:
```python
"""Phone ingest session: the testable core of the /ingest WebSocket.

main.py's /ingest handler is routing only (accept, hello, loop, close). Everything
decidable lives here: parsing the hello, dispatching binary messages, measuring the
received frame rate over a sliding window, and building the phone status dict that
Runtime.status() reports and the hub broadcasts.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("ingest")

FRAME_KIND = 0x01  # + JPEG bytes -> pipeline.ingest_frame
AUDIO_KIND = 0x02  # + mono Int16-LE PCM -> pipeline.ingest_audio
HELLO_TIMEOUT_S = 10.0
STATUS_EVERY_S = 2.0
FPS_WINDOW_S = 5.0
CLOSE_IN_USE = 4409  # a second phone tried to connect while one is streaming
CLOSE_BAD_HELLO = 4400  # first message was not a valid hello


@dataclass
class PhoneHello:
    width: int
    height: int
    fps: int
    sample_rate: int
    device: str = "phone"
    facing: str = "back"  # "back" | "front"
    camera: bool = True  # False = mic-only (camera permission was denied on the phone)


def _int(msg: dict, key: str, lo: int, hi: int) -> int:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
        raise ValueError(f"hello.{key} must be an integer in [{lo}, {hi}], got {v!r}")
    return int(v)


def parse_hello(raw: str) -> PhoneHello:
    """Parse and validate the first /ingest text message. Raises ValueError."""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"hello is not JSON: {e}") from e
    if not isinstance(msg, dict) or msg.get("type") != "hello":
        raise ValueError('first message must be {"type": "hello", ...}')
    facing = msg.get("facing", "back")
    if facing not in ("back", "front"):
        raise ValueError(f'hello.facing must be "back" or "front", got {facing!r}')
    camera = msg.get("camera", True)
    if not isinstance(camera, bool):
        raise ValueError(f"hello.camera must be a boolean, got {camera!r}")
    device = msg.get("device", "phone")
    if not isinstance(device, str) or not device.strip():
        raise ValueError(f"hello.device must be a non-empty string, got {device!r}")
    return PhoneHello(
        width=_int(msg, "width", 1, 8192),
        height=_int(msg, "height", 1, 8192),
        fps=_int(msg, "fps", 1, 60),
        sample_rate=_int(msg, "sample_rate", 8000, 192000),
        device=device.strip()[:120],
        facing=facing,
        camera=camera,
    )


class IngestSession:
    """State for one connected phone. `clock` is injectable so tests control time."""

    def __init__(self, hello: PhoneHello, clock=time.time) -> None:
        self.hello = hello
        self._clock = clock
        self._frames: deque[float] = deque(maxlen=64)
        self.last_frame_ts: float | None = None
        self.frames_received = 0
        self.audio_received = 0

    def note_frame(self, ts: float) -> None:
        self.last_frame_ts = ts
        self._frames.append(ts)
        self.frames_received += 1

    def note_audio(self) -> None:
        self.audio_received += 1

    def received_fps(self, now: float | None = None) -> float:
        now = self._clock() if now is None else now
        cutoff = now - FPS_WINDOW_S
        while self._frames and self._frames[0] < cutoff:
            self._frames.popleft()
        n = len(self._frames)
        if n < 2:
            return float(n)
        span = self._frames[-1] - self._frames[0]
        return round((n - 1) / span, 1) if span > 0 else float(n)

    def status(self, now: float | None = None) -> dict:
        now = self._clock() if now is None else now
        age = None if self.last_frame_ts is None else max(0.0, now - self.last_frame_ts)
        h = self.hello
        return {
            "connected": True,
            "device": h.device,
            "facing": h.facing,
            "camera": h.camera,
            "fps": self.received_fps(now),
            "width": h.width,
            "height": h.height,
            "sample_rate": h.sample_rate,
            "last_frame_age_s": age,
        }
```

- [ ] **Step 4: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_ingest.py -q` → 11 passed.

- [ ] **Step 5: commit**
```bash
git add backend/web/ingest.py tests/web/test_ingest.py
git commit -m "Web phone 1: ingest session unit (hello parse, fps window, status dict)"
```

---

### Task 2: Runtime phone state — status key plus ~2 s broadcast

**Files:**
- Modify: `backend/web/runtime.py` (5 small edits below), `tests/web/test_runtime.py` (key set + appended test)

**Interfaces:**
- Consumes: `PhoneHello`, `IngestSession`, `STATUS_EVERY_S` from `backend.web.ingest`.
- Produces: `Runtime.phone: IngestSession | None`; `Runtime.phone_connected(hello, now=None) -> IngestSession` (creates the session, broadcasts `{"phone": ...}` immediately); `Runtime.phone_disconnected(now=None) -> None` (clears, broadcasts `{"phone": None}`); `Runtime.status()` gains `"phone"` (dict or `None`); `Runtime.step()` broadcasts `{"phone": ...}` at most every ~2 s while connected.

- [ ] **Step 1: failing test.** First update the exact key-set assertion in `tests/web/test_runtime.py` (`test_start_stop_with_real_loop`):
  - old: `assert set(st) == {"pipeline", "pipeline_status", "demo_mode", "llm", "notify_mode", "clients", "fps", "profile"}`
  - new: `assert set(st) == {"pipeline", "pipeline_status", "demo_mode", "llm", "notify_mode", "clients", "fps", "profile", "phone"}`

  Then append this test at the end of `tests/web/test_runtime.py`:
```python
async def test_phone_status_and_2s_broadcast(tmp_path):
    from backend.web.ingest import PhoneHello
    llm = FakeLLM()
    llm.enabled = False
    rt, mp, hub, q, clock = make(tmp_path, llm)
    rt.phone_connected(PhoneHello(640, 480, 8, 48000), now=T0)
    drain(q)
    rt.step(T0 + 1.0)
    msgs = drain(q)
    assert msgs and all(m["type"] != "status" for m in msgs)  # emotion heartbeat only: quiet
    rt.step(T0 + 2.1)
    env = next(m for m in drain(q) if m["type"] == "status")
    assert env["data"]["phone"]["connected"] is True
    st = rt.status()
    assert st["phone"]["device"] == "phone"
    rt.phone_disconnected(now=T0 + 3.0)
    assert rt.status()["phone"] is None
```

- [ ] **Step 2: run, expect FAIL** — `.venv/bin/python -m pytest tests/web/test_runtime.py -q` → `AttributeError: 'Runtime' object has no attribute 'phone_connected'` (and the key-set assertion fails).

- [ ] **Step 3: implement.** Five edits in `backend/web/runtime.py`:
  1. After `from backend.web.hub import Hub` add:
     ```python
     from backend.web.ingest import STATUS_EVERY_S, IngestSession, PhoneHello
     ```
  2. After `self._stopped = False` in `__init__` add:
     ```python
             self.phone: IngestSession | None = None
             self._last_phone_broadcast = -1e18
     ```
  3. At the top of `step()`, after `t = self.state.tick(now)`, add:
     ```python
             if self.phone is not None and now - self._last_phone_broadcast >= STATUS_EVERY_S - 1e-9:
                 self._last_phone_broadcast = now
                 self.hub.publish("status", {"phone": self.phone.status(now)})
     ```
  4. Before the `# -- read models ---` section header add:
     ```python
         # -- phone ingest -------------------------------------------------------------------------
         def phone_connected(self, hello: PhoneHello, now: float | None = None) -> IngestSession:
             now = self.clock() if now is None else now
             self.phone = IngestSession(hello, clock=self.clock)
             self._last_phone_broadcast = now
             self.hub.publish("status", {"phone": self.phone.status(now)})
             return self.phone

         def phone_disconnected(self, now: float | None = None) -> None:
             now = self.clock() if now is None else now
             self.phone = None
             self.hub.publish("status", {"phone": None})

     ```
  5. In `status()`, after `"profile": self.cfg["web"]["profile"],` add:
     ```python
                 "phone": self.phone.status() if self.phone is not None else None,
     ```

- [ ] **Step 4: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_runtime.py tests/web/test_ingest.py -q` → all pass.

- [ ] **Step 5: commit**
```bash
git add backend/web/runtime.py tests/web/test_runtime.py
git commit -m "Web phone 2: Runtime phone state, status phone key, 2 s broadcast"
```

---

### Task 3: `/ingest` route in main.py plus the CLAUDE.md contract note

**Files:**
- Modify: `backend/main.py` (route only), `CLAUDE.md` (contract note, same commit)
- Create: `tests/web/test_ingest_route.py`

**Interfaces:**
- Consumes: `parse_hello`, `FRAME_KIND`, `AUDIO_KIND`, `HELLO_TIMEOUT_S`, `CLOSE_IN_USE`, `CLOSE_BAD_HELLO` from `backend.web.ingest`; `Runtime.phone_connected/phone_disconnected`; `pipeline.ingest_frame/ingest_audio`.
- Produces: `WS /ingest`. Behaviour: accept → reject with close 4409 if `rt.phone` is set → read first text message (10 s timeout, else close 4400) → parse hello (invalid → close 4400 with reason) → loop binary messages: `0x01` + JPEG → `pipeline.ingest_frame(payload, time.time())`; `0x02` + PCM → `pipeline.ingest_audio(payload, hello.sample_rate, time.time())`; unknown kind → warning, ignored; text after the hello → ignored. Any dispatch exception is logged, never kills the session. On disconnect, `phone_disconnected()` (which broadcasts `{"phone": None}`).

- [ ] **Step 1: failing test** `tests/web/test_ingest_route.py`
```python
import json

import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest import CLOSE_BAD_HELLO, CLOSE_IN_USE
from backend.web.settings import load_config

HELLO = {"type": "hello", "width": 640, "height": 480, "fps": 8, "sample_rate": 48000,
         "device": "Pixel 7", "facing": "back"}


class NoLLM:
    enabled = False
    last_call = None


def make_client(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(),
                     notifier=DashboardOnlyNotifier())
    return app


def test_ingest_frames_and_audio_reach_pipeline(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as c, c.websocket_connect("/ingest") as ws:
        ws.send_text(json.dumps(HELLO))
        ws.send_bytes(b"\x01\xff\xd8fakejpeg")
        ws.send_bytes(b"\x02" + b"\x00\x01" * 800)
        mp = app.state.rt.pipeline
        assert mp._phone_jpeg == b"\xff\xd8fakejpeg"
        assert app.state.rt.phone is not None
        assert app.state.rt.phone.audio_received == 1
        st = c.get("/status").json()
        assert st["phone"]["connected"] is True
        assert st["phone"]["device"] == "Pixel 7" and st["phone"]["facing"] == "back"
        assert st["phone"]["camera"] is True
        assert set(st["phone"]) == {"connected", "device", "facing", "camera", "fps", "width",
                                    "height", "sample_rate", "last_frame_age_s"}


def test_second_phone_rejected_with_4409(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as c, c.websocket_connect("/ingest") as ws1:
        ws1.send_text(json.dumps(HELLO))
        with c.websocket_connect("/ingest") as ws2:
            with pytest.raises(WebSocketDisconnect) as e:
                ws2.receive_text()
            assert e.value.code == CLOSE_IN_USE == 4409


def test_bad_hello_closed_with_4400(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as c, c.websocket_connect("/ingest") as ws:
        ws.send_text(json.dumps({"type": "bye"}))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == CLOSE_BAD_HELLO == 4400
    assert app.state.rt.phone is None


def test_disconnect_clears_phone_and_broadcasts(tmp_path):
    app = make_client(tmp_path)
    with TestClient(app) as c, c.websocket_connect("/ws") as dash:
        assert dash.receive_json()["type"] == "status"
        with c.websocket_connect("/ingest"):
            pass  # connects without hello, then drops: never counts as a phone
        assert app.state.rt.phone is None
        with c.websocket_connect("/ingest") as ws:
            ws.send_text(json.dumps(HELLO))
            assert app.state.rt.phone is not None
        assert app.state.rt.phone is None
        assert c.get("/status").json()["phone"] is None
        phones = [m["data"]["phone"] for m in app.state.hub.history()
                  if m["type"] == "status" and "phone" in m["data"]]
        assert phones and phones[0]["connected"] is True and phones[-1] is None
```

- [ ] **Step 2: run, expect FAIL** — `.venv/bin/python -m pytest tests/web/test_ingest_route.py -q` → `WebSocketDisconnect` / `404` on `/ingest` (no such route).

- [ ] **Step 3: implement.** Edits in `backend/main.py`:
  1. Add `import time` after `import logging`; after the `logging.basicConfig(...)` line add `log = logging.getLogger("main")`.
  2. After `from backend.web.hub import Hub` add:
     ```python
     from backend.web.ingest import AUDIO_KIND, CLOSE_BAD_HELLO, CLOSE_IN_USE, FRAME_KIND, HELLO_TIMEOUT_S, parse_hello
     ```
  3. After the `/ws` handler (before `return app`) add:
     ```python
         @app.websocket("/ingest")
         async def ingest(sock: WebSocket) -> None:
             """Phone camera/mic input. First text message = JSON hello, then binary
             kind-byte messages (0x01 JPEG, 0x02 Int16-LE PCM). One phone at a time."""
             await sock.accept()
             rt = app.state.rt
             if rt.phone is not None:
                 await sock.close(code=CLOSE_IN_USE, reason="another phone is already streaming")
                 return
             try:
                 raw = await asyncio.wait_for(sock.receive_text(), timeout=HELLO_TIMEOUT_S)
             except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError):
                 try:
                     await sock.close(code=CLOSE_BAD_HELLO, reason="no hello received")
                 except RuntimeError:
                     pass
                 return
             try:
                 hello = parse_hello(raw)
             except ValueError as e:
                 await sock.close(code=CLOSE_BAD_HELLO, reason=f"bad hello: {e}")
                 return
             session = rt.phone_connected(hello)
             try:
                 while True:
                     msg = await sock.receive()
                     data = msg.get("bytes")
                     if not data:
                         continue  # text after the hello is ignored
                     now = time.time()
                     kind, payload = data[0], data[1:]
                     try:
                         if kind == FRAME_KIND:
                             rt.pipeline.ingest_frame(payload, now)
                             session.note_frame(now)
                         elif kind == AUDIO_KIND:
                             rt.pipeline.ingest_audio(payload, hello.sample_rate, now)
                             session.note_audio()
                         else:
                             log.warning("ingest: unknown kind 0x%02x (%d bytes ignored)", kind, len(payload))
                     except Exception:  # noqa: BLE001 - a bad chunk must not kill the phone session
                         log.exception("ingest: dispatch failed")
             except (WebSocketDisconnect, RuntimeError):
                 pass
             finally:
                 rt.phone_disconnected()
     ```

- [ ] **Step 4: contract note in `CLAUDE.md`** (same commit). In the "Phone camera (primary live source)" section, replace the Transport bullet's hello lines:
  - old:
    ```
    - **Transport:** WebSocket `/ingest` on the backend (Person B). One phone at a time.
      - First message, JSON text: `{"type": "hello", "width", "height", "fps", "sample_rate", "device"}`
    ```
  - new:
    ```
    - **Transport:** WebSocket `/ingest` on the backend (Person B). One phone at a time (a second
      connection is closed with code 4409; a missing/invalid hello with 4400).
      - First message, JSON text: `{"type": "hello", "width", "height", "fps", "sample_rate", "device"}`
      - Optional hello fields: `"facing"` (`"back"` | `"front"`, default `"back"`) and `"camera"`
        (bool, default true; false = mic-only: the phone sends audio chunks but no frames).
    ```

- [ ] **Step 5: run, expect PASS** — `.venv/bin/python -m pytest tests/web -q` → all pass (including the new route tests).

- [ ] **Step 6: commit (route + contract note together, tell Person A)**
```bash
git add backend/main.py tests/web/test_ingest_route.py CLAUDE.md
git commit -m "Web phone 3: /ingest WebSocket route; hello gains optional facing/camera per CLAUDE.md"
```
Message to Person A: "The browser source now has a real feeder: `/ingest` calls `pipeline.ingest_frame/ingest_audio` with server-receipt timestamps. The hello adds optional `facing` and `camera` fields (defaults keep your side unchanged)."

---

### Task 4: `scripts/ingest_client.py` — phone simulator over the real socket

**Files:**
- Create: `scripts/ingest_client.py`

**Interfaces:**
- Consumes: none from the repo (stdlib + `websockets.sync.client` + numpy + cv2, all already in the web venv — `websockets` ships with `uvicorn[standard]`; do NOT add a dependency).
- Produces: CLI `python scripts/ingest_client.py --url ws://localhost:8000/ingest [--video clip.mp4] [--fps 8] [--duration 10] [--device NAME] [--facing back|front] [--width 640] [--height 480] [--sample-rate 48000]`. Sends the JSON hello, then `0x01` JPEG frames (~640 px long side, quality 70; synthetic moving-circle frame or video-file frames) at `--fps` plus `0x02` 440 Hz sine-wave Int16-LE chunks every 100 ms. Prints `sent N frames and M audio chunks in T s`.

- [ ] **Step 1: implement** `scripts/ingest_client.py`:
```python
"""Pretend to be the phone camera over the real /ingest WebSocket.

Streams synthetic (or video-file) JPEG frames plus a sine-wave mic signal, so the
whole phone -> backend -> dashboard path can be tested without a phone:

    python scripts/ingest_client.py --url ws://localhost:8000/ingest --duration 10
    python scripts/ingest_client.py --video clip.mp4 --fps 8 --duration 30

Needs only the web venv (websockets comes with uvicorn[standard], plus numpy and
opencv for the frames). Frames are ~640 px on the long side, JPEG quality 70;
audio is mono Int16-LE at --sample-rate in ~100 ms chunks, like the /camera page.
"""

from __future__ import annotations

import argparse
import json
import math
import time

import cv2
import numpy as np
from websockets.sync.client import connect


def synth_frame(w: int, h: int, t: float) -> np.ndarray:
    img = np.full((h, w, 3), 34, np.uint8)
    cx = int(w / 2 + (w / 3) * math.sin(t * 0.9))
    cy = int(h / 2 + (h / 4) * math.cos(t * 0.6))
    cv2.circle(img, (cx, cy), min(w, h) // 8, (240, 240, 240), -1)
    cv2.putText(img, f"ingest-client t={t:.1f}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (200, 200, 200), 2)
    return img


def encode_jpeg(frame: np.ndarray, long_side: int = 640, quality: int = 70) -> bytes:
    h, w = frame.shape[:2]
    if max(h, w) > long_side:
        s = long_side / max(h, w)
        frame = cv2.resize(frame, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


def sine_chunk(sample_rate: int, n: int, freq: float, phase: float) -> tuple[bytes, float]:
    t = (np.arange(n) + phase) / sample_rate
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    return pcm.tobytes(), phase + n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Stream test frames+audio to /ingest like a phone.")
    ap.add_argument("--url", default="ws://localhost:8000/ingest")
    ap.add_argument("--video", help="video file to send instead of synthetic frames")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--sample-rate", type=int, default=48000)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--device", default="ingest-client")
    ap.add_argument("--facing", default="back", choices=["back", "front"])
    args = ap.parse_args(argv)

    hello = {"type": "hello", "width": args.width, "height": args.height, "fps": args.fps,
             "sample_rate": args.sample_rate, "device": args.device, "facing": args.facing}
    cap = cv2.VideoCapture(args.video) if args.video else None
    if cap is not None and not cap.isOpened():
        raise SystemExit(f"could not open {args.video}")

    chunk_n = args.sample_rate // 10  # ~100 ms
    period = 1.0 / args.fps
    t0 = time.monotonic()
    frames = chunks = 0
    phase = 0.0
    with connect(args.url, max_size=16_000_000) as ws:
        ws.send(json.dumps(hello))
        next_frame = next_chunk = t0
        while (now := time.monotonic()) - t0 < args.duration:
            if now >= next_frame:
                if cap is not None:
                    ok, frame = cap.read()
                    if not ok:
                        break
                else:
                    frame = synth_frame(args.width, args.height, now - t0)
                ws.send(b"\x01" + encode_jpeg(frame))
                frames += 1
                next_frame += period
            if now >= next_chunk:
                pcm, phase = sine_chunk(args.sample_rate, chunk_n, 440.0, phase)
                ws.send(b"\x02" + pcm)
                chunks += 1
                next_chunk += 0.1
            time.sleep(max(0.0, min(next_frame, next_chunk) - time.monotonic()))
    if cap is not None:
        cap.release()
    print(f"sent {frames} frames and {chunks} audio chunks in {time.monotonic() - t0:.1f} s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: run against the dev backend.** In terminal A: `make dev-backend`. In terminal B:
```bash
.venv/bin/python scripts/ingest_client.py --duration 6
curl -s localhost:8000/status | .venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['phone'])"
```
Expected: `sent ~48 frames and ~60 audio chunks in 6.0 s`, and while the client is still running the status shows `{'connected': True, 'device': 'ingest-client', ..., 'fps': 8.0, ...}`. Open http://localhost:3000 and confirm the video panel shows the synthetic moving-circle frame and the StatusBar reads `ingest-client · back camera`. (Query `/status` while the client runs; after it exits, `"phone"` returns to `None`.)

- [ ] **Step 3: commit**
```bash
git add scripts/ingest_client.py
git commit -m "Web phone 4: ingest_client simulator (synthetic/file frames + sine audio)"
```

---

### Task 5: Pure phone logic — `frontend/lib/ingest.ts` plus Vitest tests

**Files:**
- Create: `frontend/lib/ingest.ts`, `frontend/lib/ingest.test.ts`

**Interfaces — Produces** (all pure, no DOM):
- `Facing = "back" | "front"`; `HelloArgs { width, height, fps, sampleRate, device, facing, camera }`.
- `buildHello(a: HelloArgs): string` — exact JSON the backend parses (`type`, `width`, `height`, `fps`, `sample_rate`, `device`, `facing`, `camera`).
- `frameMessage(jpeg: Uint8Array): Uint8Array` (`0x01` prefix), `audioMessage(pcm: Uint8Array): Uint8Array` (`0x02` prefix); constants `FRAME_KIND`, `AUDIO_KIND`, `SEND_BUFFER_LIMIT = 512*1024`, `BUFFER_GOOD_BELOW = 64*1024`, `BUFFER_FAIR_BELOW = 512*1024`, `MIN_FPS = 1`, `MAX_FPS = 30`.
- `floatToInt16LE(samples: Float32Array): Uint8Array` (clamped, rounded, little-endian).
- `rms(samples: Float32Array): number`; `rmsToBars(level, n): number` (full scale at 0.3 RMS).
- `connectionQuality(bufferedAmount, reconnects): { label: "Excellent"|"Good"|"Fair"|"Poor"; bars: 1|2|3|4 }` — Poor at ≥3 reconnects or ≥512 KB queued; Fair at ≥1 reconnect or ≥64 KB; Good when anything is queued; Excellent when idle and fresh.
- `downscaleSize(srcW, srcH, longSide = 640): { w, h }` (long side → 640, portrait allowed, never upscale).
- `parseFpsParam(search: string, def = 8): number` (clamped 1–30; missing/blank/invalid → `def`).
- `backoffMs(attempt: number): number` (500 ms doubling, capped at 5 s).
- `clockLabel(elapsedS: number): string` (`mm:ss`, e.g. 724 → `"12:04"`).

- [ ] **Step 1: failing test** `frontend/lib/ingest.test.ts`
```ts
import { describe, expect, it } from "vitest";
import {
  AUDIO_KIND,
  backoffMs,
  buildHello,
  clockLabel,
  connectionQuality,
  downscaleSize,
  floatToInt16LE,
  FRAME_KIND,
  frameMessage,
  audioMessage,
  parseFpsParam,
  rms,
  rmsToBars,
} from "./ingest";

describe("ingest", () => {
  it("hello matches the backend contract", () => {
    expect(
      buildHello({ width: 640, height: 480, fps: 8, sampleRate: 48000, device: "Pixel 7", facing: "back", camera: true }),
    ).toBe(
      '{"type":"hello","width":640,"height":480,"fps":8,"sample_rate":48000,"device":"Pixel 7","facing":"back","camera":true}',
    );
  });

  it("frames the kind byte", () => {
    expect(frameMessage(new Uint8Array([1, 2]))).toEqual(new Uint8Array([FRAME_KIND, 1, 2]));
    expect(audioMessage(new Uint8Array([3]))).toEqual(new Uint8Array([AUDIO_KIND, 3]));
  });

  it("float32 -> int16 LE with clamping", () => {
    const out = floatToInt16LE(new Float32Array([0, 0.5, -0.5, 1, -1, 2, -2]));
    const view = new DataView(out.buffer);
    expect([...Array(7)].map((_, i) => view.getInt16(i * 2, true))).toEqual([
      0, 16384, -16383, 32767, -32767, 32767, -32767, // -0.5 rounds to -16383 (toward +inf)
    ]);
  });

  it("rms and bars", () => {
    expect(rms(new Float32Array([]))).toBe(0);
    expect(rms(new Float32Array([1, -1]))).toBeCloseTo(1);
    expect(rmsToBars(0, 14)).toBe(0);
    expect(rmsToBars(0.15, 14)).toBe(7);
    expect(rmsToBars(9, 10)).toBe(10);
  });

  it("quality from backpressure and reconnects", () => {
    expect(connectionQuality(0, 0)).toEqual({ label: "Excellent", bars: 4 });
    expect(connectionQuality(1000, 0)).toEqual({ label: "Good", bars: 3 });
    expect(connectionQuality(100_000, 0)).toEqual({ label: "Fair", bars: 2 });
    expect(connectionQuality(0, 1)).toEqual({ label: "Fair", bars: 2 });
    expect(connectionQuality(0, 3)).toEqual({ label: "Poor", bars: 1 });
    expect(connectionQuality(2_000_000, 0)).toEqual({ label: "Poor", bars: 1 });
  });

  it("downscale keeps the long side at 640, never upscales", () => {
    expect(downscaleSize(1280, 720)).toEqual({ w: 640, h: 360 });
    expect(downscaleSize(720, 1280)).toEqual({ w: 360, h: 640 });
    expect(downscaleSize(320, 200)).toEqual({ w: 320, h: 200 });
  });

  it("fps param", () => {
    expect(parseFpsParam("?fps=4")).toBe(4);
    expect(parseFpsParam("?fps=99")).toBe(30);
    expect(parseFpsParam("?fps=banana")).toBe(8);
    expect(parseFpsParam("")).toBe(8);
  });

  it("backoff and clock", () => {
    expect([0, 1, 2, 3, 10].map(backoffMs)).toEqual([500, 1000, 2000, 4000, 5000]);
    expect(clockLabel(724)).toBe("12:04");
  });
});
```
Run: `cd frontend && npm run test` → FAIL (cannot find `./ingest`).

- [ ] **Step 2: implement** `frontend/lib/ingest.ts`
```ts
export type Facing = "back" | "front";

export type HelloArgs = {
  width: number;
  height: number;
  fps: number;
  sampleRate: number;
  device: string;
  facing: Facing;
  camera: boolean;
};

export const FRAME_KIND = 0x01;
export const AUDIO_KIND = 0x02;
/** Skip a video frame when this much is still queued on the socket. */
export const SEND_BUFFER_LIMIT = 512 * 1024;
/** Connection-quality thresholds on the socket's queued bytes. */
export const BUFFER_GOOD_BELOW = 64 * 1024;
export const BUFFER_FAIR_BELOW = 512 * 1024;
export const MAX_FPS = 30;
export const MIN_FPS = 1;

/** JSON hello: the first /ingest text message. Field names match backend/web/ingest.py. */
export function buildHello(a: HelloArgs): string {
  return JSON.stringify({
    type: "hello",
    width: a.width,
    height: a.height,
    fps: a.fps,
    sample_rate: a.sampleRate,
    device: a.device,
    facing: a.facing,
    camera: a.camera,
  });
}

function withKind(kind: number, payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(payload.length + 1);
  out[0] = kind;
  out.set(payload, 1);
  return out;
}

/** 0x01 + JPEG bytes. */
export function frameMessage(jpeg: Uint8Array): Uint8Array {
  return withKind(FRAME_KIND, jpeg);
}

/** 0x02 + Int16-LE PCM bytes. */
export function audioMessage(pcm: Uint8Array): Uint8Array {
  return withKind(AUDIO_KIND, pcm);
}

/** Mono Float32 [-1, 1] -> Int16 little-endian bytes for the 0x02 message. */
export function floatToInt16LE(samples: Float32Array): Uint8Array {
  const out = new Uint8Array(samples.length * 2);
  const view = new DataView(out.buffer);
  for (let i = 0; i < samples.length; i++) {
    const c = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, Math.round(c * 32767), true);
  }
  return out;
}

/** RMS level of mono Float32 samples, 0..~1. */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

/** RMS -> lit mic bars (0..n). Full scale at 0.3 RMS, so normal speech lights ~half. */
export function rmsToBars(level: number, n: number): number {
  return Math.max(0, Math.min(n, Math.round(Math.min(1, level / 0.3) * n)));
}

export type Quality = { label: "Excellent" | "Good" | "Fair" | "Poor"; bars: 1 | 2 | 3 | 4 };

/**
 * Connection quality from socket backpressure + recent reconnects.
 * Pure so the pill and its test can't drift apart.
 */
export function connectionQuality(bufferedAmount: number, reconnects: number): Quality {
  if (reconnects >= 3 || bufferedAmount >= BUFFER_FAIR_BELOW) return { label: "Poor", bars: 1 };
  if (reconnects >= 1 || bufferedAmount >= BUFFER_GOOD_BELOW) return { label: "Fair", bars: 2 };
  if (bufferedAmount > 0) return { label: "Good", bars: 3 };
  return { label: "Excellent", bars: 4 };
}

/** Scale (srcW, srcH) so the long side is `longSide` (portrait allowed); never upscale. */
export function downscaleSize(srcW: number, srcH: number, longSide = 640): { w: number; h: number } {
  const s = Math.min(1, longSide / Math.max(srcW, srcH));
  return { w: Math.max(1, Math.round(srcW * s)), h: Math.max(1, Math.round(srcH * s)) };
}

/** ?fps= query param, clamped; falls back to `def` when missing or invalid. */
export function parseFpsParam(search: string, def = 8): number {
  const raw = new URLSearchParams(search).get("fps");
  if (raw === null || raw.trim() === "") return def;
  const v = Number(raw);
  if (!Number.isFinite(v)) return def;
  return Math.max(MIN_FPS, Math.min(MAX_FPS, Math.round(v)));
}

/** Reconnect backoff for attempt n (0-based): 500 ms, 1 s, 2 s … capped at 5 s. */
export function backoffMs(attempt: number): number {
  return Math.min(5000, 500 * 2 ** Math.max(0, attempt));
}

/** mm:ss streaming clock. */
export function clockLabel(elapsedS: number): string {
  const m = Math.floor(elapsedS / 60);
  const s = Math.floor(elapsedS % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
```
Note: the explicit `null`/blank check in `parseFpsParam` is load-bearing — `Number(null)` is `0`, which would otherwise clamp a missing param to 1 fps.

- [ ] **Step 3: run, expect PASS** — `cd frontend && npm run test` → 8 passed; `npm run typecheck && npm run lint` clean.

- [ ] **Step 4: commit**
```bash
git add frontend/lib/ingest.ts frontend/lib/ingest.test.ts
git commit -m "Web phone 5: pure ingest helpers (hello, framing, pcm, quality) with tests"
```

---

### Task 6: `/camera` page — worklet, streaming component, route

**Files:**
- Create: `frontend/public/pcm-worklet.js`, `frontend/components/CameraClient.tsx`, `frontend/components/CameraPage.tsx`, `frontend/app/camera/page.tsx`

**Interfaces:**
- Consumes: `backendBase()` from `lib/config.ts`; everything from `lib/ingest.ts` (Task 5).
- Produces: route `/camera` (server shell → `CameraPage` → client-only `CameraClient`, the Plan 2 `DashboardClient` pattern — `ssr: false` is not allowed directly in a Server Component). Phases `setup → streaming`, plus `blocked` on camera denial. Setup screen 05 (Allow access, Back Recommended / Front Selfie, preview + dashed `22%/12%/30%/14%` Feeding zone guide, Start streaming 56 px, "Paired with dashboard"). Streaming screens 06/07 (Streaming pill + clock, quality pill, sent-fps pill, 14/10-bar mic meter, "Keep this screen on · plugged in", portrait 64 px "Stop streaming" / landscape round 88 px "Stop"). Blocked screen 08 (Try again / Continue with microphone only → streams audio with `camera: false`). Behaviour: canvas capture at `?fps=` (default 8) downscaled to 640 long side, JPEG 0.7, skip when `bufferedAmount > 512 KB`; AudioWorklet mono Float32 at native rate → Int16 LE ~100 ms chunks; `sample_rate` in hello = `AudioContext.sampleRate`; Wake Lock with `visibilitychange` re-acquire; backoff reconnect resending the hello; `?device=` names the phone (default `"Phone"`); dog name from `/status` profile (fallback `"Bruno"`).

- [ ] **Step 1: worklet** `frontend/public/pcm-worklet.js` (plain JS, keep tiny):
```js
/* AudioWorklet processor: forwards mono Float32 chunks of ~100 ms to the page.
   Loaded via audioContext.audioWorklet.addModule("/pcm-worklet.js").
   Kept tiny on purpose: resampling/conversion happen on the main thread. */
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.target = Math.round(sampleRate * 0.1);
    this.buf = new Float32Array(this.target);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs && inputs[0] && inputs[0][0];
    if (ch) {
      let i = 0;
      while (i < ch.length) {
        const room = this.target - this.n;
        const take = Math.min(room, ch.length - i);
        this.buf.set(ch.subarray(i, i + take), this.n);
        this.n += take;
        i += take;
        if (this.n >= this.target) {
          const out = this.buf.slice();
          this.port.postMessage(out, [out.buffer]);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
```

- [ ] **Step 2: component** `frontend/components/CameraClient.tsx` (full file — no placeholders):
```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { backendBase } from "@/lib/config";
import {
  audioMessage,
  backoffMs,
  buildHello,
  clockLabel,
  connectionQuality,
  downscaleSize,
  floatToInt16LE,
  frameMessage,
  parseFpsParam,
  rms,
  rmsToBars,
  SEND_BUFFER_LIMIT,
  type Facing,
} from "@/lib/ingest";

type Phase = "setup" | "streaming" | "blocked";

const GUIDE = { left: "22%", right: "12%", top: "30%", bottom: "14%" };

function useLandscape(): boolean {
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(orientation: landscape)");
    const on = () => setWide(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

export default function CameraClient() {
  const landscape = useLandscape();
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const stopRef = useRef(false);
  const sentAtRef = useRef<number[]>([]);
  const reconnectsRef = useRef(0);

  const [phase, setPhase] = useState<Phase>("setup");
  const [granted, setGranted] = useState(false);
  const [micOnly, setMicOnly] = useState(false);
  const [facing, setFacing] = useState<Facing>("back");
  const [dog, setDog] = useState("Bruno");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [sentFps, setSentFps] = useState(0);
  const [bars, setBars] = useState(0);
  const [quality, setQuality] = useState(connectionQuality(0, 0));
  const [attempt, setAttempt] = useState(0);

  // The camera UI is dark only: force .dark while this page is mounted, restore on leave.
  useEffect(() => {
    const el = document.documentElement;
    const had = el.classList.contains("dark");
    el.classList.add("dark");
    fetch(`${backendBase().http}/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (typeof s?.profile?.dog_name === "string") setDog(s.profile.dog_name);
      })
      .catch(() => undefined);
    return () => {
      if (!had) el.classList.remove("dark");
    };
  }, []);

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  useEffect(() => () => stopTracks(), [stopTracks]);

  /** Ask the browser for camera+mic (video=true) or mic only. Returns true when granted. */
  const requestAccess = useCallback(
    async (video: boolean, face: Facing): Promise<boolean> => {
      setError(null);
      try {
        const stream = await navigator.mediaDevices.getUserMedia(
          video
            ? {
                video: {
                  facingMode: { ideal: face === "front" ? "user" : "environment" },
                  width: { ideal: 1280 },
                  height: { ideal: 720 },
                },
                audio: true,
              }
            : { audio: true },
        );
        stopTracks();
        streamRef.current = stream;
        setGranted(true);
        setMicOnly(!video);
        return true;
      } catch (e) {
        if (video) {
          const name = e instanceof DOMException ? e.name : "";
          if (name === "NotAllowedError" || name === "SecurityError") setPhase("blocked");
          else setError("Could not open the camera. Check it is not used by another app, then try again.");
        } else {
          setError("Could not open the microphone. Check the browser permission, then try again.");
        }
        return false;
      }
    },
    [stopTracks],
  );

  // Keep the single <video> element fed from the current stream (setup preview and streaming share it).
  useEffect(() => {
    const v = videoRef.current;
    const s = streamRef.current;
    if (v && s && v.srcObject !== s) {
      v.srcObject = s;
      void v.play().catch(() => undefined);
    }
  });

  const connectRef = useRef<(hello: string, fps: number, camera: boolean) => void>(() => undefined);

  const connect = useCallback((hello: string, fps: number, camera: boolean) => {
    const ws = new WebSocket(`${backendBase().ws}/ingest`);
    wsRef.current = ws;
    ws.binaryType = "arraybuffer";
    let timers: number[] = [];
    let stopVideo: (() => void) | undefined;
    ws.onopen = () => {
      ws.send(hello);
      setAttempt(0);
      const t0 = Date.now();
      setElapsed(0);
      timers = [
        window.setInterval(() => setElapsed((Date.now() - t0) / 1000), 1000),
        window.setInterval(() => {
          const now = performance.now();
          sentAtRef.current = sentAtRef.current.filter((t) => now - t < 2000);
          setSentFps(sentAtRef.current.length / 2);
          setQuality(connectionQuality(ws.bufferedAmount, reconnectsRef.current));
        }, 500),
      ];
      if (camera) {
        const video = videoRef.current;
        const canvas = document.createElement("canvas");
        const id = window.setInterval(() => {
          if (ws.readyState !== WebSocket.OPEN || !video || video.readyState < 2) return;
          if (ws.bufferedAmount > SEND_BUFFER_LIMIT) return; // drop, never queue
          const { w, h } = downscaleSize(video.videoWidth || 640, video.videoHeight || 480);
          canvas.width = w;
          canvas.height = h;
          canvas.getContext("2d")?.drawImage(video, 0, 0, w, h);
          canvas.toBlob(
            (blob) => {
              if (!blob || ws.readyState !== WebSocket.OPEN) return;
              void blob.arrayBuffer().then((buf) => {
                if (ws.readyState !== WebSocket.OPEN) return;
                ws.send(frameMessage(new Uint8Array(buf)));
                const now = performance.now();
                sentAtRef.current = [...sentAtRef.current.filter((t) => now - t < 2000), now];
              });
            },
            "image/jpeg",
            0.7,
          );
        }, 1000 / fps);
        stopVideo = () => window.clearInterval(id);
      }
    };
    ws.onclose = () => {
      timers.forEach((t) => window.clearInterval(t));
      stopVideo?.();
      if (stopRef.current) return; // the user pressed Stop
      reconnectsRef.current += 1;
      const n = reconnectsRef.current;
      setAttempt(n);
      window.setTimeout(() => {
        if (!stopRef.current) connectRef.current(hello, fps, camera);
      }, backoffMs(n - 1));
    };
    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const startStreaming = useCallback(
    async (face: Facing, audioOnly: boolean) => {
      const params = new URLSearchParams(window.location.search);
      const fps = parseFpsParam(params.toString());
      const device = params.get("device") || "Phone";
      stopRef.current = false;
      reconnectsRef.current = 0;
      sentAtRef.current = [];
      const video = videoRef.current;
      // Open the probe context first so the hello carries the mic's native sample rate.
      let sampleRate = 48000;
      if (streamRef.current) {
        const probe = new window.AudioContext();
        sampleRate = probe.sampleRate;
        await probe.close();
      }
      const hello = buildHello({
        width: video?.videoWidth || 640,
        height: video?.videoHeight || 480,
        fps,
        sampleRate,
        device,
        facing: face,
        camera: !audioOnly,
      });
      setPhase("streaming");
      connect(hello, fps, !audioOnly);
      const stream = streamRef.current;
      if (stream) {
        try {
          const ctx = new window.AudioContext({ sampleRate });
          audioCtxRef.current = ctx;
          await ctx.audioWorklet.addModule("/pcm-worklet.js");
          const node = new AudioWorkletNode(ctx, "pcm-capture");
          node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
            const samples = ev.data;
            setBars(rmsToBars(rms(samples), window.matchMedia("(orientation: landscape)").matches ? 10 : 14));
            const ws = wsRef.current;
            if (ws && ws.readyState === WebSocket.OPEN) ws.send(audioMessage(floatToInt16LE(samples)));
          };
          ctx.createMediaStreamSource(stream).connect(node);
        } catch {
          setError("Microphone streaming failed. Video continues without sound.");
        }
      }
      try {
        await navigator.wakeLock?.request("screen");
      } catch {
        /* unsupported or denied: streaming still works, the screen may sleep */
      }
    },
    [connect],
  );

  const stopStreaming = useCallback(() => {
    stopRef.current = true;
    wsRef.current?.close();
    wsRef.current = null;
    void audioCtxRef.current?.close();
    audioCtxRef.current = null;
    setPhase("setup");
    setElapsed(0);
    setSentFps(0);
    setBars(0);
    setAttempt(0);
  }, []);

  useEffect(() => () => stopStreaming(), [stopStreaming]);

  // Re-acquire the screen wake lock when the tab becomes visible again while streaming.
  useEffect(() => {
    if (phase !== "streaming") return;
    const onVis = () => {
      if (document.visibilityState === "visible") void navigator.wakeLock?.request("screen").catch(() => undefined);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [phase]);

  if (phase === "blocked") {
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col gap-6 bg-bg px-5 pb-7 pt-13 text-text">
        <div className="micro text-muted">Camera mode</div>
        <div className="flex flex-1 flex-col justify-center gap-5">
          <h1 className="text-title font-bold">Camera access is blocked</h1>
          <p className="text-body text-muted">
            Claude Pet needs the camera to see {dog}’s posture and face. Your browser blocked it for this site.
          </p>
          <ol className="flex list-none flex-col gap-3 rounded-xl border border-border bg-surface p-4 text-body">
            <li>1. Tap the site settings icon next to the address.</li>
            <li>2. Set Camera and Microphone to Allow.</li>
            <li>3. Come back and tap Try again.</li>
          </ol>
        </div>
        <div className="flex flex-col gap-2.5">
          <button type="button" onClick={() => void requestAccess(true, facing)}
            className="h-14 rounded-xl bg-accent text-[17px] font-bold text-accent-fg">
            Try again
          </button>
          <button type="button"
            onClick={() => void requestAccess(false, facing).then((ok) => {
              if (ok) void startStreaming(facing, true);
            })}
            className="flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-xl border border-border px-2 py-2 text-[15px] font-semibold">
            Continue with microphone only
            <span className="text-[12px] font-medium text-muted">Emotion from sounds only · lower confidence</span>
          </button>
        </div>
      </main>
    );
  }

  if (phase === "streaming") {
    const barCount = landscape ? 10 : 14;
    return (
      <main className="relative h-dvh w-full overflow-hidden bg-[#1B1D1F] text-white">
        <video ref={videoRef} muted playsInline autoPlay className="absolute inset-0 h-full w-full object-cover" />
        <div className="pointer-events-none absolute border-2 border-dashed"
          style={{ ...GUIDE, borderColor: "#7FE3E8", borderRadius: 16 }} />
        <div className={`absolute flex flex-col gap-2 ${landscape ? "left-5 top-5 items-start" : "inset-x-4 top-14"}`}>
          <div className="flex items-center gap-2">
            <div className="flex h-9 items-center gap-2 rounded-full bg-black/70 px-3 text-[13px] font-bold">
              <span className="h-2 w-2 rounded-full" style={{ background: "#FF5A4E" }} />
              Streaming
              <span className="font-mono font-normal text-[#D6D8DB]">{clockLabel(elapsed)}</span>
            </div>
            {!landscape && <div className="grow" />}
            {!landscape && (
              <div className="flex h-9 items-center gap-2 rounded-full bg-black/70 px-3 text-[12px] text-[#D6D8DB]"
                aria-label={`Connection ${quality.label}`}>
                <span className="flex items-end gap-[3px]" aria-hidden="true">
                  {[0, 1, 2, 3].map((i) => (
                    <span key={i} style={{
                      width: 3, height: 4 + i * 3.5, borderRadius: 1,
                      background: i < quality.bars ? "#7FE3E8" : "#4A4F55",
                    }} />
                  ))}
                </span>
                {quality.label}
              </div>
            )}
            {!landscape && (
              <div className="flex h-9 items-center rounded-full bg-black/70 px-3 font-mono text-[12px] text-[#D6D8DB]">
                {sentFps} fps
              </div>
            )}
          </div>
          <div className="flex h-9 items-center gap-2.5 self-start rounded-full bg-black/70 px-3" aria-label="Microphone level">
            <span className="text-[12px] text-[#D6D8DB]">Mic</span>
            <span className="flex h-4 items-center gap-[3px]" aria-hidden="true">
              {Array.from({ length: barCount }).map((_, i) => (
                <span key={i} style={{
                  width: 3, height: Math.max(3, Math.round(((i + 1) / barCount) * 16)), borderRadius: 2,
                  background: i < bars ? "#7FE3E8" : "#4A4F55",
                }} />
              ))}
            </span>
          </div>
          {landscape && (
            <div className="flex h-[30px] items-center rounded-full bg-black/70 px-2.5 text-[12px] text-[#D6D8DB]"
              aria-label={`Connection ${quality.label}`}>
              {quality.label}
            </div>
          )}
          {landscape && (
            <div className="flex h-[30px] items-center rounded-full bg-black/70 px-2.5 font-mono text-[12px] text-[#D6D8DB]">
              {sentFps} fps
            </div>
          )}
        </div>
        {attempt > 0 && (
          <div role="status" className="absolute inset-x-4 top-40 flex justify-center">
            <div className="rounded-full bg-black/70 px-3 py-1 text-[12px]">Reconnecting · attempt {attempt}</div>
          </div>
        )}
        <div className={`absolute flex flex-col items-center gap-3.5 ${landscape ? "bottom-5 left-1/2 -translate-x-1/2" : "inset-x-4 bottom-8"}`}>
          <div className="flex h-[34px] items-center rounded-full bg-black/70 px-3.5 text-[13px] text-[#D6D8DB]">
            Keep this screen on · plugged in
          </div>
          {!landscape && (
            <button type="button" onClick={stopStreaming}
              className="flex h-16 w-full items-center justify-center gap-3 rounded-2xl bg-[#F4F4F2] text-[18px] font-bold text-[#17181A]">
              Stop streaming
            </button>
          )}
        </div>
        {landscape && (
          <button type="button" onClick={stopStreaming} aria-label="Stop streaming"
            className="absolute top-1/2 right-5 flex h-[88px] w-[88px] -translate-y-1/2 flex-col items-center justify-center gap-1.5 rounded-full bg-[#F4F4F2] text-[13px] font-bold text-[#17181A]">
            Stop
          </button>
        )}
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col gap-5 bg-bg px-5 pb-7 pt-13 text-text">
      <div className="micro text-muted">Camera mode</div>
      <div className="flex flex-col gap-2">
        <h1 className="text-title font-bold">Use this phone as {dog}’s camera</h1>
        <p className="text-body text-muted">Mount it near the food bowl and keep it plugged in. Your dashboard shows the feed.</p>
      </div>
      <ol className="flex list-none flex-col gap-3 p-0">
        <li className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
          <div className="flex items-start gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[13px] font-bold text-accent-ink">1</span>
            <div className="flex flex-col gap-1">
              <div className="text-[16px] font-semibold">Allow camera and microphone</div>
              <div className="text-small leading-snug text-muted">Used to read {dog}’s posture, face and sounds. Your browser will ask once.</div>
            </div>
          </div>
          <button type="button" onClick={() => void requestAccess(true, facing)} disabled={granted}
            className="flex h-11 items-center justify-center gap-2 rounded-md border border-accent bg-transparent text-[15px] font-semibold text-accent-ink disabled:opacity-50">
            {granted ? "Access granted" : "Allow access"}
          </button>
        </li>
        <li className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[13px] font-bold text-accent-ink">2</span>
            <div className="text-[16px] font-semibold">Choose camera</div>
          </div>
          <div role="radiogroup" aria-label="Camera" className="grid grid-cols-2 gap-1.5 rounded-xl bg-surface-2 p-1">
            {(["back", "front"] as const).map((c) => {
              const on = facing === c;
              return (
                <button key={c} type="button" role="radio" aria-checked={on}
                  onClick={() => {
                    setFacing(c);
                    if (granted && !micOnly) void requestAccess(true, c);
                  }}
                  className={`flex h-11 flex-col items-center justify-center rounded-lg text-[14px] font-semibold ${on ? "bg-surface text-text shadow-sm" : "text-muted"}`}>
                  {c === "back" ? "Back" : "Front"}
                  <span className="text-[11px] font-medium opacity-75">{c === "back" ? "Recommended" : "Selfie"}</span>
                </button>
              );
            })}
          </div>
        </li>
        <li className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[13px] font-bold text-accent-ink">3</span>
            <div className="text-[16px] font-semibold">Point at the food bowl</div>
          </div>
          <div className="relative h-[150px] overflow-hidden rounded-lg bg-[#1B1D1F]">
            <video ref={videoRef} muted playsInline autoPlay
              className={`absolute inset-0 h-full w-full object-cover ${granted && !micOnly ? "" : "invisible"}`} />
            {(!granted || micOnly) && <div className="absolute inset-0 bg-[#2B2E31]" />}
            <div className="pointer-events-none absolute border-2 border-dashed"
              style={{ ...GUIDE, borderColor: "#7FE3E8", borderRadius: 12 }} />
            <div className="pointer-events-none absolute text-[11px] font-bold"
              style={{ left: GUIDE.left, top: GUIDE.top, transform: "translateY(-120%)", color: "#BFF2F4" }}>
              Feeding zone
            </div>
          </div>
          <div className="text-small leading-snug text-muted">Fit the bowl and about a metre around it inside the dashed zone. Waist height works best.</div>
        </li>
      </ol>
      {error && <div role="alert" className="rounded-md border border-border bg-surface-2 px-4 py-2.5 text-small">{error}</div>}
      <div className="grow" />
      <button type="button" onClick={() => void startStreaming(facing, micOnly)} disabled={!granted}
        className="h-14 rounded-xl bg-accent text-[17px] font-bold text-accent-fg disabled:opacity-50">
        Start streaming
      </button>
      <div className="text-center text-[12px] text-muted">Paired with dashboard</div>
    </main>
  );
}
```
Two non-obvious points in this file: the reconnect calls `connectRef.current` (not `connect` directly) because `connect` is a `useCallback` and the repo's `react-hooks/immutability` lint errors on the recursive reference; and the single `<video>` element is shared between the setup preview and the streaming view (the `srcObject` lives on the `MediaStream`, so the effect re-attaches it after each phase switch).

- [ ] **Step 3: wrapper + route.** `frontend/components/CameraPage.tsx`:
```tsx
"use client";

import dynamic from "next/dynamic";

const CameraClient = dynamic(() => import("@/components/CameraClient"), {
  ssr: false,
  loading: () => <div className="p-8 text-small text-muted">Loading camera…</div>,
});

export default function CameraPage() {
  return <CameraClient />;
}
```
(`ssr: false` must live in this Client Component — it is not allowed directly in the Server page below; this mirrors `DashboardClient`.) `frontend/app/camera/page.tsx`:
```tsx
import type { Metadata } from "next";
import CameraPage from "@/components/CameraPage";

export const metadata: Metadata = {
  title: "Camera · Claude Pet",
  description: "Use this phone as your dog's camera and microphone",
};

export default function Page() {
  return <CameraPage />;
}
```

- [ ] **Step 4: verify** — inside `frontend/`: `npm run test && npm run typecheck && npm run lint && npm run build`. Expected: all green; the build lists routes `/` and `/camera`.
Manual (desktop Chrome is enough for layout): `cd frontend && npm run dev`, open http://localhost:3000/camera. Deny the camera permission → the blocked screen appears verbatim; click "Continue with microphone only" after allowing the mic → streams audio. Rotate DevTools to landscape while streaming → the round 88 px Stop button appears and the mic meter shrinks to 10 bars.

- [ ] **Step 5: commit**
```bash
git add frontend/public/pcm-worklet.js frontend/components/CameraClient.tsx frontend/components/CameraPage.tsx frontend/app/camera/page.tsx
git commit -m "Web phone 6: /camera page (setup, streaming, blocked) with worklet audio"
```

---

### Task 7: Dashboard integration — camera-blocked notice, mic-only label

**Files:**
- Modify: `frontend/lib/types.ts`, `frontend/components/SystemNotice.tsx`, `frontend/components/StatusBar.tsx`, `frontend/components/Dashboard.tsx`

**Interfaces:**
- Consumes: `Status.phone` from `/status` + `status` envelopes (merged by the existing Plan 2 reducer — no reducer change); `setMode` from `useBackend` (already passed to `StatusBar`).
- Produces: when `status.phone.camera === false`, a banner reading "Camera blocked on the camera phone" + "Open Claude Pet on that phone and allow camera access, or play a demo clip instead." with "How to fix" (inline explainer toggle) and "Use a demo clip" (calls `onMode("demo")`; disabled with the "Demo mode arrives with the demo clips" tooltip until Plan 4). `deviceLabel` reads `{device} · mic only` for mic-only phones. Mid-session disconnects need no new code: `pipeline_status.state === "stalled"` already renders "Reconnecting to camera" (verify by reading `SystemNotice.tsx` — the branch is above the new one, so stalled wins when both are true).

- [ ] **Step 1: `types.ts`.** Replace the `phone` line:
  - old: `phone?: { connected: boolean; device: string | null; facing: string | null; fps: number } | null; // Plan 3`
  - new:
```ts
  phone?: {
    connected: boolean;
    device: string | null;
    facing: string | null;
    camera?: boolean | null;
    fps: number;
    width?: number | null;
    height?: number | null;
    sample_rate?: number | null;
    last_frame_age_s?: number | null;
  } | null; // Plan 3 (/ingest hello + server-measured fps)
```

- [ ] **Step 2: `SystemNotice.tsx`.** Add `import { useState } from "react";`, extend `Props` with `demoAvailable?: boolean; onMode?: (mode: "live" | "demo") => void;`, and insert the camera-blocked branch between the `stalled` branch and the `llm.offline` branch:
```tsx
  if (status?.phone?.camera === false) {
    return (
      <Banner>
        <span className="font-bold text-text">Camera blocked on the camera phone</span>
        <span>Open Claude Pet on that phone and allow camera access, or play a demo clip instead.</span>
        <span className="flex gap-2">
          <button type="button" onClick={() => setFixOpen((v) => !v)} aria-expanded={fixOpen}
            className="flex h-11 items-center rounded-md border border-border bg-surface px-4 text-small font-semibold">
            How to fix
          </button>
          <button type="button" onClick={() => onMode?.("demo")} disabled={!demoAvailable || !onMode}
            title={!demoAvailable ? "Demo mode arrives with the demo clips" : undefined}
            className="flex h-11 items-center rounded-md border border-border bg-surface px-4 text-small font-semibold disabled:opacity-50">
            Use a demo clip
          </button>
        </span>
        {fixOpen && (
          <span className="basis-full text-small">
            On the camera phone: open /camera, tap the site settings icon next to the address, set Camera
            and Microphone to Allow, then tap Try again — or continue with microphone only.
          </span>
        )}
      </Banner>
    );
  }
```
with `const [fixOpen, setFixOpen] = useState(false);` at the top of the component. Copy strings are verbatim from `10-system-states.html` ("Camera or microphone blocked").

- [ ] **Step 3: `StatusBar.tsx`.** Extend `deviceLabel`:
  - old: `if (s?.phone?.connected) return \`${s.phone.device ?? "Phone"} · ${s.phone.facing ?? "back"} camera\`;`
  - new:
```tsx
  if (s?.phone?.connected) {
    if (s.phone.camera === false) return `${s.phone.device ?? "Phone"} · mic only`;
    return `${s.phone.device ?? "Phone"} · ${s.phone.facing ?? "back"} camera`;
  }
```

- [ ] **Step 4: `Dashboard.tsx`.** Pass the new props:
  - old:
```tsx
        <SystemNotice connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} lastFrameTs={state.frame?.ts ?? null} />
```
  - new:
```tsx
        <SystemNotice connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} lastFrameTs={state.frame?.ts ?? null}
          demoAvailable={Boolean(state.status?.modes?.includes("demo"))} onMode={(m) => void setMode(m)} />
```

- [ ] **Step 5: verify** — inside `frontend/`: `npm run test && npm run typecheck && npm run lint` (existing 46+ tests still pass; the store's partial-status merge test already covers the `phone` merge path).
Manual: with the backend running, `python scripts/ingest_client.py --duration 60` in one terminal and the dashboard open in another; kill the client mid-run → `phone: null` arrives, the device label falls back to "Mock camera", and (with Person A's real pipeline) `state: stalled` shows the existing "Reconnecting to camera" banner. To preview the blocked banner without a phone, temporarily evaluate `status.phone = { connected: true, camera: false, ... }` in React DevTools or serve a stubbed `/status`.

- [ ] **Step 6: commit**
```bash
git add frontend/lib/types.ts frontend/components/SystemNotice.tsx frontend/components/StatusBar.tsx frontend/components/Dashboard.tsx
git commit -m "Web phone 7: dashboard camera-blocked notice and mic-only label"
```

---

### Task 8: HTTPS tunnels and the demo runbook

**Files:**
- Modify: `Makefile` (append targets, extend `.PHONY`)
- Create: `docs/DEMO_RUNBOOK.md`

**Interfaces — Produces:**
- `make tunnel-frontend` → `cloudflared tunnel --url http://localhost:3000`; `make tunnel-backend` → `cloudflared tunnel --url http://localhost:8000`. (Browsers need HTTPS for `getUserMedia` except on localhost; Plan 2 already sets `allowedDevOrigins` for `*.trycloudflare.com`, so no `next.config.ts` change.)
- `docs/DEMO_RUNBOOK.md` with a "Phone camera" section: start order, how to open `/camera?backend=wss://…`, troubleshooting. (Plan 4 will append the offline-demo section.)

- [ ] **Step 1: Makefile.** Change `.PHONY: dev-backend dev-frontend dev demo test-web test-frontend` to `.PHONY: dev-backend dev-frontend dev demo test-web test-frontend tunnel-frontend tunnel-backend` and append (TAB-indented):
```make
tunnel-frontend:
	cloudflared tunnel --url http://localhost:3000
tunnel-backend:
	cloudflared tunnel --url http://localhost:8000
```

- [ ] **Step 2: runbook** `docs/DEMO_RUNBOOK.md`:
```markdown
# Demo runbook — Claude Pet

## Phone camera

Start order (three terminals, repo root):

1. `make dev-backend` — wait for `Uvicorn running on http://127.0.0.1:8000`.
2. `make dev-frontend` (or `cd frontend && npm run build && npm run start` if the venue Wi-Fi is flaky —
   the production build does not need the dev server).
3. `make tunnel-backend` and `make tunnel-frontend` — each prints a `https://<name>.trycloudflare.com`
   URL. Copy both.

On the camera phone (must be the same Wi-Fi, or any network — the tunnel is public):

1. Open the frontend tunnel URL with the backend attached, e.g.
   `https://<frontend>.trycloudflare.com/camera?backend=wss://<backend>.trycloudflare.com`.
   `?backend=` wins over `NEXT_PUBLIC_BACKEND_URL`, which wins over `page-host:8000`.
2. Tap **Allow access**, pick **Back (Recommended)**, fit the bowl in the dashed
   **Feeding zone**, tap **Start streaming**.
3. On the laptop dashboard, the StatusBar should read `<device> · back camera` with a live fps,
   and `/video` shows the phone's feed.

### Troubleshooting

| Symptom | Fix |
|---|---|
| Phone shows the blocked screen | On the phone: site settings → Camera/Microphone → Allow → **Try again**; or **Continue with microphone only** (dashboard shows `· mic only`) |
| `backend: offline` / reconnect loop on `/camera` | The backend tunnel URL is wrong or `make dev-backend` is down; re-copy the `wss://` URL into `?backend=` |
| Dashboard shows "Reconnecting to camera" | The phone dropped off Wi-Fi: keep it on, plugged in, near the router; it reconnects by itself, no restart needed |
| Anything else fails 5 min before showtime | Kill the tunnels; open `http://laptop:3000` + `?backend=ws://laptop:8000` on the same Wi-Fi, or fall back to `scripts/fake_phone.py` / `scripts/ingest_client.py` |
| Camera works on localhost without tunnels | Expected: browsers allow camera/mic on `localhost` over HTTP; HTTPS (tunnels) is only needed on a real phone |
```

- [ ] **Step 3: verify** — `make -n tunnel-frontend tunnel-backend` prints the two `cloudflared` commands (do not run them here); re-read the file for typos in the URLs and copy strings.

- [ ] **Step 4: commit**
```bash
git add Makefile docs/DEMO_RUNBOOK.md
git commit -m "Web phone 8: cloudflared tunnel targets and phone-camera runbook"
```

---

### Task 9: End-to-end check — simulator plus the real-phone test

**Files:** none (verification only — commit nothing).

**Interfaces:**
- Consumes: Tasks 1–8 complete, `scripts/ingest_client.py`, the runbook, one real phone.

- [ ] **Step 1: simulator soak.** Terminal A: `make dev-backend`. Terminal B: `cd frontend && npm run dev`. Terminal C:
```bash
.venv/bin/python scripts/ingest_client.py --duration 300
```
Expected: prints `sent ~2400 frames and ~3000 audio chunks in 300.0 s`; throughout, `curl -s localhost:8000/status` shows `"phone"` connected with `fps` ≈ 8.0; the laptop dashboard shows the synthetic feed, `ingest-client · back camera`, and a 2 s-cadence `status` envelope with `phone` on `/ws` (check DevTools → WS frames). No backend errors in terminal A, memory flat.

- [ ] **Step 2: real-phone test** (PROMPTS-WEB Step 8b acceptance): two tunnels up, open `/camera?backend=wss://…` on the phone (iPhone Safari and Android Chrome if both are available). Stream for 5 minutes: the phone's video appears on the laptop dashboard. Then kill the phone page and reopen it: the dashboard must recover — StatusBar device label, `/video`, and emotion readings resume — without restarting anything. Also test **Continue with microphone only**: the dashboard shows the camera-blocked banner with working **How to fix** explainer and a disabled **Use a demo clip** button.

- [ ] **Step 3: report.** Write the results (phone models + browsers, fps observed, any reconnects, failures) as a comment on this plan or a short `docs/superpowers/notes/phone-test-<date>.md`. If anything fails, file it against the task that owns it (Tasks 1–8) — do not bundle fixes into this task.
