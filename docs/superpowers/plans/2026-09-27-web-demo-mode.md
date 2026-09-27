# Demo Mode, Integration and Hardening Plan (Person B — Plan 4 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The offline fallback demo (PROMPTS-WEB Step 9), the real-pipeline integration checklist (Step 10) and provider comparison plus hardening (Step 11). After this plan, the laptop runs the full demo in airplane mode: clip playback with fused emotions, toasts and timeline, zero network.

**Architecture:** A clip is a directory with `clip.mp4` (H.264), `events.jsonl` (contract events at clip-relative seconds) and `meta.json`. `scripts/precompute_demo.py` replays each clip through the **same** `FusionState`, `TriggerPolicy` and `LLMInterpreter` the live path uses, and commits the resulting `backend/demo/cache/<id>.timeline.json`. The backend serves clips, events and timelines as static data plus a `POST /mode` switch; it never "runs" the demo. The frontend, in demo mode, plays the `<video>` itself and derives a `DemoView` (same shape as the live dashboard state) from `video.currentTime` through a pure module, so pause and seek stay in sync. Everything else (integration check, benchmarks, soak) is scripts plus docs.

**Tech Stack:** Python (OpenCV for frame extraction, `imageio-ffmpeg==0.6.0` for H.264 encoding — verified working, no system ffmpeg needed), Starlette `FileResponse` (Range/seek support verified present in starlette 1.7.0), Next.js 16 (existing dashboard), Vitest for `lib/demo.ts`.

**Spec:** PROMPTS-WEB Steps 9–11, `docs/superpowers/specs/2026-09-27-ui-screens.md` (demo banner + clip list copy), `ui/screens/10-system-states.html` ("Demo mode"). Plans 1–3 are implemented; this plan builds on `backend/demo/mock_pipeline.py`, `backend/fusion/state.py`, `backend/fusion/llm_triggers.py`, `backend/fusion/llm_interpreter.py`, `backend/web/runtime.py`, `backend/main.py`, `frontend/lib/store.ts` types, `frontend/lib/overlay.ts`, and the Plan 2 components (`EmotionCard`, `Timeline`, `SignalsPanel`, `ToastStack`, `VideoPanel`).

## Global Constraints

- Web owns: `backend/demo/` (except Person A's future `events.jsonl` drops), `scripts/`, `docs/DATA_HANDOFF_WEB.md`, `docs/DEMO_RUNBOOK.md` (append; Plan 3 owns the "Phone camera" section), `frontend/` demo files, `config.yaml` `web.demo` section, `CLAUDE.md` Commands. Never touch Person A's files or `data/` (except documenting what Person A must produce).
- The demo path must work with **zero network** apart from localhost: no fetch to CDNs, no fonts at runtime (built at build time), no LLM calls (precomputed). Test: airplane mode.
- `DEMO_MODE=1` starts the backend in demo mode. Committed demo artifacts (placeholder clip + timelines) must keep the repo lean: the placeholder clip is ~90 s of flat synthetic frames and must stay under 5 MB.
- All new Python scripts run from the repo root with `.venv/bin/python` and include the `sys.path.insert` header (like `scripts/fake_phone.py`).

## Decisions

1. **Clip layout:** `<dir>/clip.mp4 + events.jsonl + meta.json`. Timeline is served from `backend/demo/cache/<id>.timeline.json` (per CLAUDE.md), not from the clip dir.
2. **Manifest:** `web.demo.manifest` (default `data/fallback/manifest.json`, Person A's file). If it is missing, fall back to the web-owned `backend/demo/clips/manifest.json` (the placeholder). Manifest schema: `{"clips": [{"id", "name", "emotion", "dir"}]}`.
3. **events.jsonl line:** `{"t": <clip-relative seconds>, "type": "frame"|"audio"|"rules"|"treat", "data": {...}}`. For `frame`/`audio`/`rules`, `data` validates against the contract model and `data.ts` SHOULD equal `t` (validator warns, doesn't fail). `treat` lines have `data: {"ts": t}`.
4. **timeline.json:** `{"clip", "duration_s", "states": [{t, emotion, confidence, source, reason}] (changes only), "live": [{...}] (1 Hz samples), "llm": [{t, emotion, confidence, reason, provider, model, trigger}], "notifications": [{t, emotion, reason}], "treats": [t]}`.
5. **Demo playback is frontend-driven.** The backend does not advance the demo; `POST /mode` only flips `Runtime.mode` (broadcast so all clients sync) and `/status` reports `modes`/`mode`.
6. **Demo toasts:** notifications with `t` in `[now-6, now]` show as toasts; sticky negatives persist until dismissed (dismissal is session-local). The bell lists demo notifications as read.
7. **First-notification Telegram (opt-in):** `POST /demo/notify {clip, t}` sends once per clip per server run when `web.demo.telegram_first` is true and the notifier is a real Telegram one. Default false.
8. **Placeholder clip is committed** (mock-90s, H.264 + events + meta + precomputed timeline), justified: it makes the demo work out of the box and is small (flat frames compress to <1 MB; the task asserts the size).

## File Structure

| File | Responsibility |
|---|---|
| `docs/DATA_HANDOFF_WEB.md` | exact clip format Person A must produce + mismatch-report template (Task 1) |
| `scripts/validate_clip.py` | manifest + events.jsonl validator (Task 1) |
| `scripts/make_mock_clip.py` | renders MockPipeline 90 s → H.264 + events.jsonl + meta.json (Task 2) |
| `backend/demo/clips/manifest.json`, `backend/demo/clips/mock-90s/{clip.mp4,events.jsonl,meta.json}` | committed placeholder (Task 2) |
| `backend/demo/replay.py` | pure accelerated replay → timeline dict (Task 3) |
| `scripts/precompute_demo.py` | per-clip replay with frame extraction → `backend/demo/cache/<id>.timeline.json` (Task 3) |
| `backend/demo/clips.py` | `DemoClips` loader (manifest + fallback) (Task 4) |
| `backend/main.py` | `/demo/*` routes + `POST /mode` (Task 4) |
| `backend/web/runtime.py` | `mode`, `set_mode`, `demo_notified`, status `modes`/`mode` (Task 4) |
| `frontend/lib/demo.ts` | clip types + `buildDemoView` selector (Task 5) |
| `frontend/components/DemoBanner.tsx`, `ClipList.tsx`, `DemoPlayer.tsx` | demo UI (Task 6) |
| `frontend/components/Dashboard.tsx` | demo/live switching + `D` hotkey (Task 6) |
| `scripts/check_integration.py` | live-pipeline checklist (Task 7) |
| `scripts/llm_benchmark.py`, `scripts/soak.py` | provider comparison + soak (Task 8) |
| `docs/DEMO_RUNBOOK.md` | failure drills + offline demo procedure (Task 8) |

---

### Task 1: Clip contract — manifest, handoff doc, validator, config

**Files:**
- Create: `docs/DATA_HANDOFF_WEB.md`, `scripts/validate_clip.py`, `backend/demo/clips/manifest.json`
- Modify: `config.yaml` (+ `web.demo`), `backend/web/settings.py` (+ defaults)
- Test: `tests/web/test_validate_clip.py` (drives the script as a module)

**Interfaces — Produces:**
- `validate_clip(manifest_path: str | Path) -> list[str]` (returns error strings, empty = valid; also runnable as `__main__`, exit 0/1, prints errors).
- Config `web.demo`: `{manifest: "data/fallback/manifest.json", clips_dir: "backend/demo/clips", telegram_first: false}` with the same defaults in `WEB_DEFAULTS`.

- [ ] **Step 1: failing test** `tests/web/test_validate_clip.py`
```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from validate_clip import validate_clip


def _clip(tmp_path: Path, line: dict | str, meta: dict | None = None) -> Path:
    d = tmp_path / "clips" / "c1"
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"fake")
    body = line if isinstance(line, str) else json.dumps(line)
    (d / "events.jsonl").write_text(body + "\n")
    (d / "meta.json").write_text(json.dumps(meta or {"name": "C", "emotion": "happy", "duration_s": 1.0}))
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"clips": [{"id": "c1", "name": "C", "emotion": "happy", "dir": "c1"}]}))
    return man


def test_valid_clip(tmp_path):
    man = _clip(tmp_path, {"t": 0.5, "type": "audio", "data": {"ts": 0.5, "label": "bark", "score": 0.9}})
    assert validate_clip(man) == []


def test_bad_lines_reported(tmp_path):
    man = _clip(tmp_path, '{"t": 1, "type": "audio", "data": {"ts": 1, "label": "moo", "score": 2}}')
    errs = validate_clip(man)
    assert len(errs) == 1 and "c1" in errs[0] and "line 1" in errs[0]


def test_missing_files_and_manifest_problems(tmp_path):
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"clips": [{"id": "x", "name": "X", "emotion": "sad", "dir": "x"}]}))
    errs = validate_clip(man)
    assert any("manifest" in e and "emotion" in e for e in errs)
    assert any("clip.mp4" in e for e in errs)
```

- [ ] **Step 2: run, expect FAIL** — `.venv/bin/python -m pytest tests/web/test_validate_clip.py -q` → `ModuleNotFoundError: validate_clip`.

- [ ] **Step 3: implement** `scripts/validate_clip.py`:
```python
"""Validate a fallback-clip manifest and its events.jsonl files against the contracts.

    .venv/bin/python scripts/validate_clip.py [manifest.json]   # default: web.demo.manifest from config.yaml

Exit 0 when valid, 1 with one error per line otherwise. Used by Person A before handing over clips.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from backend.contracts import EMOTIONS, AudioEvent, FrameEvent, RulesLabel  # noqa: E402

BY_TYPE = {"frame": FrameEvent, "audio": AudioEvent, "rules": RulesLabel}


def _errs(manifest_path: Path) -> list[str]:
    errs: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return [f"manifest {manifest_path}: unreadable ({exc})"]
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        return [f"manifest {manifest_path}: 'clips' must be a non-empty list"]
    base = manifest_path.parent
    for i, c in enumerate(clips):
        where = f"manifest clip #{i}"
        if not isinstance(c, dict):
            errs.append(f"{where}: not an object")
            continue
        for key in ("id", "name", "emotion", "dir"):
            if key not in c:
                errs.append(f"{where}: missing {key!r}")
        if c.get("emotion") not in EMOTIONS:
            errs.append(f"{where}: emotion {c.get('emotion')!r} not in the fixed vocabulary")
        d = base / str(c.get("dir", ""))
        for f in ("clip.mp4", "events.jsonl", "meta.json"):
            if not (d / f).exists():
                errs.append(f"clip {c.get('id')}: missing {f}")
        ev = d / "events.jsonl"
        if ev.exists():
            for n, line in enumerate(ev.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    errs.append(f"clip {c.get('id')} line {n}: not JSON")
                    continue
                if not isinstance(obj, dict) or not isinstance(obj.get("t"), (int, float)):
                    errs.append(f"clip {c.get('id')} line {n}: needs numeric 't'")
                    continue
                t = obj["type"]
                if t == "treat":
                    continue
                model = BY_TYPE.get(t)
                if model is None:
                    errs.append(f"clip {c.get('id')} line {n}: type {t!r} must be frame|audio|rules|treat")
                    continue
                try:
                    model.model_validate(obj.get("data"))
                except ValidationError as exc:
                    errs.append(f"clip {c.get('id')} line {n}: {exc.errors()[0]['loc']}: {exc.errors()[0]['msg']}")
                else:
                    ts = (obj.get("data") or {}).get("ts")
                    if isinstance(ts, (int, float)) and abs(ts - obj["t"]) > 0.01:
                        errs.append(f"clip {c.get('id')} line {n}: data.ts should equal t (clip-relative)")
    return errs


def validate_clip(manifest_path: str | Path) -> list[str]:
    return _errs(Path(manifest_path))


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args:
        path = Path(args[0])
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend.web.settings import load_config

        path = Path(load_config()["web"]["demo"]["manifest"])
    errs = validate_clip(path)
    for e in errs:
        print(e)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`backend/demo/clips/manifest.json`:
```json
{"clips": [{"id": "mock-90s", "name": "Mock day", "emotion": "excited", "dir": "mock-90s"}]}
```

`docs/DATA_HANDOFF_WEB.md` (write the full file):
```markdown
# Clip handoff: Data → Web

For each fallback clip, Person A delivers a directory with three files. Paths are listed in the
manifest (`data/fallback/manifest.json`); until it exists, Web uses its own
`backend/demo/clips/manifest.json`.

## Clip directory

- `clip.mp4` — H.264, yuv420p, +faststart (browsers need this; OpenCV `mp4v` does NOT play in
  `<video>`). Any size; 640 px on the long side is plenty.
- `events.jsonl` — one JSON object per line: `{"t": <seconds from clip start>, "type": "frame" | "audio" | "rules" | "treat", "data": {...}}`.
  For `frame`/`audio`/`rules`, `data` is the contract model with `data.ts == t`. `treat` lines carry
  `data: {"ts": t}` at each treat-drop moment. Frames at ~8 Hz (every processed frame, including
  `dog_detected: false`); audio debounced as usual; rules once per frame.
- `meta.json` — `{"name": "Treat drop", "emotion": "excited", "duration_s": 42.0}` (emotion = the
  clip's dominant label, for the clip picker).

Produce it from a real run: `python scripts/fake_phone.py clip.mp4 --events out/<id>.jsonl`
(timestamped from 0), or dump any Pipeline run's callbacks in the same shape.
Then check it: `.venv/bin/python scripts/validate_clip.py data/fallback/manifest.json` (exit 0 = valid).

## Mismatch report template (Step 10)

When the real pipeline misbehaves, file it like this before fixing anything:

- Symptom: <what the dashboard shows>
- Raw evidence: <the offending events.jsonl lines or WS messages>
- Contracts check: <`validate_clip.py` / contracts.py result>
- Owner: <Data | Web> — <one line why>
- Web-side workaround (if any): <none | description>
```

Config: append to `config.yaml` under `web:` (after `notify:`):
```yaml
  demo:
    manifest: data/fallback/manifest.json   # Person A's file; falls back to backend/demo/clips/manifest.json
    clips_dir: backend/demo/clips           # web-owned placeholder clips
    telegram_first: false                   # also send the first demo notification via Telegram if configured
```
And in `backend/web/settings.py` `WEB_DEFAULTS`, add `"demo": {"manifest": "data/fallback/manifest.json", "clips_dir": "backend/demo/clips", "telegram_first": False},` after `"notify"`. Extend `tests/web/test_settings.py`? No — add one assertion to the existing defaults test is editing a committed test; instead assert in the new test file: append to `tests/web/test_validate_clip.py`:
```python
def test_demo_defaults(tmp_path):
    from backend.web.settings import load_config
    cfg = load_config(tmp_path / "none.yaml", env={})
    assert cfg["web"]["demo"] == {"manifest": "data/fallback/manifest.json", "clips_dir": "backend/demo/clips", "telegram_first": False}
```

- [ ] **Step 4: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_validate_clip.py -q` (5 passed).
- [ ] **Step 5: commit** `git add docs/DATA_HANDOFF_WEB.md scripts/validate_clip.py tests/web/test_validate_clip.py backend/demo/clips/manifest.json config.yaml backend/web/settings.py && git commit -m "Web step 9a: clip contract, handoff doc, validator, demo config"`

---

### Task 2: Placeholder clip generator (MockPipeline → H.264 + events.jsonl)

**Files:**
- Create: `scripts/make_mock_clip.py`
- Modify: `requirements-web.txt` (+ `imageio-ffmpeg==0.6.0`, verified: static ffmpeg 7.0.2 binary, encodes libx264 yuv420p +faststart)
- Generate (committed): `backend/demo/clips/mock-90s/{clip.mp4,events.jsonl,meta.json}`
- Test: `tests/web/test_make_mock_clip.py`

**Interfaces — Produces:** `scripts/make_mock_clip.py [--out DIR] [--duration S] [--fps N] [--width W] [--height H]`. Renders MockPipeline with a fake clock from 0.0, writes the three files, prints sizes. Treat marker at the relaxed→excited boundary.

- [ ] **Step 1: failing test** `tests/web/test_make_mock_clip.py`
```python
import json
import subprocess
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]


def test_generates_playable_clip_and_valid_events(tmp_path):
    out = tmp_path / "mock"
    r = subprocess.run([sys.executable, "scripts/make_mock_clip.py", "--out", str(out),
                        "--duration", "4", "--fps", "4", "--width", "320", "--height", "240"],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert (out / "clip.mp4").stat().st_size < 1_000_000
    cap = cv2.VideoCapture(str(out / "clip.mp4"))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 16
    ok, frame = cap.read()
    assert ok and frame.shape[:2] == (240, 320)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["duration_s"] == 4 and meta["emotion"] == "excited"
    sys.path.insert(0, str(ROOT / "scripts"))
    from validate_clip import validate_clip
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"clips": [{"id": "t", "name": "t", "emotion": "excited", "dir": "mock"}]}))
    (tmp_path / "mock").rename(tmp_path / "t") if False else None
    import shutil
    shutil.move(str(out), str(tmp_path / "t"))
    assert validate_clip(man) == []
```
(The odd `rename... if False` line is intentional dead code to keep mypy quiet about unused imports — no. Delete it: write the test with `shutil.move(str(out), str(tmp_path / "t"))` directly and a top-level `import shutil`. The implementer must write it cleanly:
```python
    shutil.move(str(out), str(tmp_path / "t"))
    assert validate_clip(man) == []
```
with `import shutil` at the top.)

- [ ] **Step 2: run, expect FAIL** — script missing → `returncode == 2`, assertion on `r.returncode == 0` fails.

- [ ] **Step 3: implement** `scripts/make_mock_clip.py`:
```python
"""Render MockPipeline into a committed placeholder demo clip (H.264 + events.jsonl + meta.json).

    .venv/bin/python scripts/make_mock_clip.py [--out backend/demo/clips/mock-90s] [--duration 90] [--fps 8]

Needs imageio-ffmpeg (static ffmpeg binary, no system install). The frames are flat synthetic
renderings, so 90 s stays well under 1 MB.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imageio_ffmpeg  # noqa: E402

from backend.demo.mock_pipeline import MockPipeline  # noqa: E402


def render(frame, w: int, h: int, phase: str) -> np.ndarray:
    img = np.full((h, w, 3), 60, np.uint8)
    sx, sy = w / 640.0, h / 480.0
    if frame.dog_detected and frame.bbox:
        x1, y1, x2, y2 = [int(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate(frame.bbox)]
        cv2.rectangle(img, (x1, y1), (x2, y2), (127, 227, 232), 2)
        for kp in (frame.body_keypoints or {}).values():
            if kp:
                cv2.circle(img, (int(kp[0] * sx), int(kp[1] * sy)), 4, (127, 227, 232), -1)
    cv2.putText(img, f"MOCK {phase}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
    return img


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backend/demo/clips/mock-90s")
    ap.add_argument("--duration", type=float, default=90.0)
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    clock = [0.0]
    mp = MockPipeline({"data": {"fps": a.fps}}, clock=lambda: clock[0])
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{a.width}x{a.height}", "-r", str(a.fps), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / "clip.mp4")],
        stdin=subprocess.PIPE)
    assert proc.stdin is not None
    n = int(a.duration * a.fps)
    treat_at = 12.0  # relaxed -> excited boundary in the mock script
    with (out / "events.jsonl").open("w") as f:
        for i in range(n):
            t = round(i / a.fps, 3)
            frame, audio, rules = mp.step(t)
            f.write(json.dumps({"t": t, "type": "frame", "data": frame.model_dump(mode="json")}) + "\n")
            for e in audio:
                f.write(json.dumps({"t": t, "type": "audio", "data": e.model_dump(mode="json")}) + "\n")
            f.write(json.dumps({"t": t, "type": "rules", "data": rules.model_dump(mode="json")}) + "\n")
            if abs(t - treat_at) < 0.5 / a.fps:
                f.write(json.dumps({"t": t, "type": "treat", "data": {"ts": t}}) + "\n")
            proc.stdin.write(np.ascontiguousarray(render(frame, a.width, a.height, mp.phase_at(t))).tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        return 1
    (out / "meta.json").write_text(json.dumps({"name": "Mock day", "emotion": "excited", "duration_s": a.duration}))
    size = (out / "clip.mp4").stat().st_size
    print(f"wrote {out}/clip.mp4 ({size} bytes), events.jsonl, meta.json in {time.process_time():.1f}s cpu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
Note: `frame.bbox` is in 640×480 mock coordinates, hence the scale factors. `mp.step(t)` also renders its own JPEG internally (unused here) — harmless.

- [ ] **Step 4: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_make_mock_clip.py -q` (1 passed).
- [ ] **Step 5: generate the committed clip** — `.venv/bin/python scripts/make_mock_clip.py` (90 s, ~720 frames; takes under a minute). Check: `ls -la backend/demo/clips/mock-90s/` (clip.mp4 must be < 5 MB), `.venv/bin/python scripts/validate_clip.py backend/demo/clips/manifest.json` → exit 0.
- [ ] **Step 6: commit** `git add scripts/make_mock_clip.py tests/web/test_make_mock_clip.py requirements-web.txt backend/demo/clips/ && git commit -m "Web step 9b: placeholder demo clip generator + committed mock-90s clip"`

---

### Task 3: Replay module + precompute script → committed timelines

**Files:**
- Create: `backend/demo/replay.py`, `scripts/precompute_demo.py`
- Generate (committed): `backend/demo/cache/mock-90s.timeline.json`
- Test: `tests/web/test_replay.py`

**Interfaces — Produces:**
- `replay_clip(events: list[dict], get_frame: Callable[[float], bytes | None], interpreter, rules_cfg: dict | None = None, tick_hz: float = 4.0, live_hz: float = 1.0) -> dict` — async. `events` are clip-relative `{"t","type","data"}` dicts (data as plain dicts). Returns the timeline.json dict (Decision 4). Uses `FusionState`, `TriggerPolicy`, `RulesEngine` (importing rules here is fine: this is an offline script, not the live web path).
- `scripts/precompute_demo.py [--manifest PATH] [--clip ID]`: for each clip with events.jsonl, extracts the clip frame at each LLM trigger with OpenCV and replays with the real `LLMInterpreter` (env config) or rules-only when unconfigured; writes `backend/demo/cache/<id>.timeline.json`.

- [ ] **Step 1: failing test** `tests/web/test_replay.py`
```python
from backend.contracts import LLMResult
from backend.demo.replay import replay_clip


class FakeLLM:
    enabled = True

    def __init__(self):
        self.calls = []
        self.last_call = None

    async def interpret(self, *, trigger, now, features, audio, rules, jpeg):
        self.calls.append(trigger)
        self.last_call = {"outcome": "ok"}
        return LLMResult(ts=now, emotion="excited", confidence=0.9, reason="Fast wag.", provider="p",
                         model="m", latency_ms=1.0, trigger=trigger)


def _events():
    from backend.demo.mock_pipeline import MockPipeline
    mp = MockPipeline({"data": {"fps": 8}}, clock=lambda: 0.0)
    out = []
    for i in range(96):  # 12 s: relaxed then excited at 12? no — relaxed 0-12; use 160 steps (20 s)
        pass
    return out


async def test_replay_produces_states_notifications_treats():
    from backend.demo.mock_pipeline import MockPipeline
    mp = MockPipeline({"data": {"fps": 8}}, clock=lambda: 0.0)
    evs = []
    for i in range(160):
        t = round(i / 8, 3)
        f, a, r = mp.step(t)
        evs.append({"t": t, "type": "frame", "data": f.model_dump(mode="json")})
        evs += [{"t": t, "type": "audio", "data": x.model_dump(mode="json")} for x in a]
        evs.append({"t": t, "type": "rules", "data": r.model_dump(mode="json")})
    evs.append({"t": 12.0, "type": "treat", "data": {"ts": 12.0}})
    tl = await replay_clip(evs, lambda t: b"\xff\xd8fake", FakeLLM())
    assert tl["duration_s"] == 19.875
    emotions = [s["emotion"] for s in tl["states"]]
    assert emotions[0] == "relaxed" and "excited" in emotions
    assert tl["treats"] == [12.0]
    assert tl["llm"] and all(x["trigger"] for x in tl["llm"])
    assert isinstance(tl["notifications"], list)
    live_ts = [s["t"] for s in tl["live"]]
    assert live_ts == sorted(live_ts) and live_ts[-1] <= 19.875
```
(Clean up before writing: delete the unused `_events` helper — it does nothing. The test builds events inline.)

- [ ] **Step 2: run, expect FAIL** — `ModuleNotFoundError: backend.demo.replay`.

- [ ] **Step 3: implement** `backend/demo/replay.py`:
```python
"""Accelerated offline replay of a clip's events through the live fusion stack.

Same classes as the live path (FusionState, TriggerPolicy, RulesEngine, LLMInterpreter), but with a
fake clock advanced in fixed ticks and no network/timing involved. Pure apart from the interpreter.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from backend.contracts import AudioEvent, FrameEvent, RulesLabel
from backend.fusion.llm_triggers import TriggerPolicy
from backend.fusion.rules import RulesEngine
from backend.fusion.state import FusionState, StateConfig


def _empty_cfg() -> dict:
    return {"web": {"state": {}, "llm": {"min_interval_s": 3.0, "heartbeat_s": 10.0, "audio_trigger_score": 0.6}}}


async def replay_clip(events: list[dict], get_frame: Callable[[float], bytes | None], interpreter: Any,
                      rules_cfg: dict | None = None, tick_hz: float = 4.0, live_hz: float = 1.0) -> dict:
    """events: clip-relative {"t","type","data"} dicts, any order. Returns the timeline.json dict."""
    evs = sorted(events, key=lambda e: e["t"])
    end = max([e["t"] for e in evs] + [0.0])
    rules = RulesEngine(rules_cfg or {})
    fs = FusionState(StateConfig())
    trig = TriggerPolicy.from_config(_empty_cfg())
    feats: list[tuple[float, Any]] = []
    audio: list[AudioEvent] = []
    cur_rules: RulesLabel | None = None
    states, live, llm_out, notes, treats = [], [], [], [], []
    last_live = -1e18

    async def run_llm(reason: str, now: float) -> None:
        res = await interpreter.interpret(trigger=reason, now=now, features=feats[-6:], audio=[a for a in audio if a.ts >= now - 3.0],
                                          rules=cur_rules, jpeg=get_frame(now))
        if res is not None:
            fs.on_llm(res)
            llm_out.append({"t": now, "emotion": res.emotion, "confidence": res.confidence, "reason": res.reason,
                            "provider": res.provider, "model": res.model, "trigger": res.trigger})

    i, t, dt = 0, 0.0, 1.0 / tick_hz
    while t <= end + 1e-9:
        while i < len(evs) and evs[i]["t"] <= t + 1e-9:
            e = evs[i]
            if e["type"] == "frame":
                f = FrameEvent.model_validate(e["data"])
                fs.on_frame(f)
                if f.dog_detected:
                    feats.append((f.ts, f.features))
                label = rules.update(f, [a for a in audio if a.ts > f.ts - 3.0])
                cur_rules = label
                fs.on_rules(label)
                trig.note_rules(label)
            elif e["type"] == "audio":
                a = AudioEvent.model_validate(e["data"])
                audio.append(a)
                trig.note_audio(a)
            elif e["type"] == "treat":
                trig.note_treat()
                treats.append(round(e["t"], 3))
            i += 1
        tick = fs.tick(t)
        if tick.changed is not None:
            states.append({"t": t, "emotion": tick.changed.emotion, "confidence": tick.changed.confidence,
                           "source": tick.changed.source, "reason": tick.changed.reason})
        elif t - last_live >= 1.0 / live_hz - 1e-9:
            c = tick.current
            live.append({"t": t, "emotion": c.emotion, "confidence": c.confidence, "source": c.source, "reason": c.reason})
            last_live = t
        if tick.notify is not None:
            notes.append({"t": t, "emotion": tick.notify.emotion, "reason": tick.notify.reason})
        if interpreter.enabled:
            reason = trig.due(t, False)
            if reason:
                await run_llm(reason, t)  # offline: inline, so the result feeds the next tick
        t = round(t + dt, 6)
    if not states and live:
        first = live[0]  # first state adopted immediately: mirror it as a change at t=0
        states.append({"t": 0.0, **{k: first[k] for k in ("emotion", "confidence", "source", "reason")}})
    return {"duration_s": end, "states": states, "live": live, "llm": llm_out,
            "notifications": notes, "treats": sorted(treats)}
```

`scripts/precompute_demo.py`:
```python
"""Replay each manifest clip through the live fusion stack and commit the timelines.

    .venv/bin/python scripts/precompute_demo.py [--manifest PATH] [--clip ID]

Run while online with .env configured for the LLM you want to demo. Without LLM config it still
writes a rules-only timeline (llm: []). Outputs: backend/demo/cache/<id>.timeline.json (committed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend.demo.replay import replay_clip  # noqa: E402
from backend.fusion.llm_interpreter import LLMInterpreter  # noqa: E402
from backend.web.settings import LLMSettings, load_config  # noqa: E402


def read_events(clip_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (clip_dir / "events.jsonl").read_text().splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--clip", default=None)
    a = ap.parse_args(argv)
    cfg = load_config()
    demo = cfg["web"]["demo"]
    manifest_path = Path(a.manifest or demo["manifest"])
    if not manifest_path.exists():
        manifest_path = Path(demo["clips_dir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    cache = Path("backend/demo/cache")
    cache.mkdir(parents=True, exist_ok=True)
    settings = LLMSettings.from_config(cfg)
    print("LLM:", "enabled" if settings.enabled else "disabled (rules-only timelines)")
    for c in manifest["clips"]:
        if a.clip and c["id"] != a.clip:
            continue
        d = manifest_path.parent / c["dir"]
        cap = cv2.VideoCapture(str(d / "clip.mp4"))
        fps = cap.get(cv2.CAP_PROP_FPS) or 8.0

        def get_frame(t: float, cap=cap, fps=fps):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                return None
            ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buf.tobytes() if ok2 else None

        tl = asyncio.run(replay_clip(read_events(d), get_frame, LLMInterpreter(settings)))
        tl["clip"] = c["id"]
        (cache / f"{c['id']}.timeline.json").write_text(json.dumps(tl))
        print(f"{c['id']}: {len(tl['states'])} states, {len(tl['llm'])} llm, {len(tl['notifications'])} notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_replay.py -q` (1 passed; takes a few seconds: 160 mock steps).
- [ ] **Step 5: precompute the placeholder** (rules-only; no LLM needed) — `.venv/bin/python scripts/precompute_demo.py --clip mock-90s`. Check `backend/demo/cache/mock-90s.timeline.json` has states/notifications/treats and `"clip": "mock-90s"`.
- [ ] **Step 6: commit** `git add backend/demo/replay.py scripts/precompute_demo.py tests/web/test_replay.py backend/demo/cache/ && git commit -m "Web step 9c: offline replay + precomputed demo timelines"`

---

### Task 4: Backend demo endpoints + mode switch

**Files:**
- Create: `backend/demo/clips.py`
- Modify: `backend/web/runtime.py` (mode, set_mode, demo notify-once), `backend/main.py` (`/demo/*`, `POST /mode`), `tests/web/test_runtime.py` (status key set), `config.yaml` (nothing — `web.demo` already added in Task 1)
- Test: `tests/web/test_demo.py`

**Interfaces — Produces:**
- `ClipMeta` dataclass: `id, name, emotion, duration_s, has_timeline`.
- `DemoClips(manifest: Path | None, bundled: Path)` with `.list() -> list[ClipMeta]`, `.video_path(id) -> Path`, `.events(id) -> list[dict]`, `.timeline(id) -> dict | None`, `.available -> bool`. `load_demo_clips(cfg) -> DemoClips` (manifest path from config, else bundled dir).
- `Runtime(..., demo_clips: DemoClips | None = None)` (keyword, backward compatible): `.mode: "live"|"demo"`, `.set_mode(mode) -> str` (raises ValueError on unknown; demo with no clips raises ValueError), `.demo_notified: set[str]`, `async .demo_notify(clip_id, t) -> NotifyResult-like dict`.
- `POST /mode {mode}` → `{"mode"}`; `GET /demo/clips`, `GET /demo/clips/{id}/video` (FileResponse, Range-capable), `/events`, `/timeline`; `/status` gains `modes` + `mode`.

- [ ] **Step 1: failing test** `tests/web/test_demo.py`
```python
import json

from fastapi.testclient import TestClient

from backend.demo.clips import DemoClips, load_demo_clips
from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def _write_clip(root, cid="c1", n_events=4):
    d = root / cid
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    evs = [{"t": i * 0.5, "type": "audio", "data": {"ts": i * 0.5, "label": "bark", "score": 0.9}} for i in range(n_events)]
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")
    (d / "meta.json").write_text(json.dumps({"name": "Barks", "emotion": "excited", "duration_s": 2.0}))
    (root / "manifest.json").write_text(json.dumps({"clips": [{"id": cid, "name": "Barks", "emotion": "excited", "dir": cid}]}))
    return root / "manifest.json"


def test_clips_loader_and_fallback(tmp_path):
    man = _write_clip(tmp_path / "a")
    clips = DemoClips(man, tmp_path / "bundled-missing")
    assert [c.id for c in clips.list()] == ["c1"] and clips.available
    assert clips.list()[0].has_timeline is False
    assert len(clips.events("c1")) == 4 and clips.timeline("c1") is None
    assert clips.video_path("c1").name == "clip.mp4"
    try:
        clips.video_path("nope")
        assert False
    except KeyError:
        pass
    fallback = DemoClips(tmp_path / "no-manifest.json", tmp_path / "b")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "manifest.json").write_text(json.dumps({"clips": []}))
    assert fallback.available is False and fallback.list() == []


def test_load_demo_clips_prefers_manifest(tmp_path):
    man = _write_clip(tmp_path / "m")
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["demo"]["manifest"] = str(man)
    assert [c.id for c in load_demo_clips(cfg).list()] == ["c1"]


def _client(tmp_path):
    man = _write_clip(tmp_path / "clips")
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["demo"]["manifest"] = str(man)
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return TestClient(app)


def test_demo_endpoints_and_mode_switch(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/status").json()["modes"] == ["live", "demo"]
        clips = c.get("/demo/clips").json()
        assert clips[0]["id"] == "c1" and clips[0]["duration_s"] == 2.0
        ev = c.get("/demo/clips/c1/events").json()
        assert ev["events"][0]["type"] == "audio"
        assert c.get("/demo/clips/c1/timeline").status_code == 404
        v = c.get("/demo/clips/c1/video")
        assert v.status_code == 200 and v.content.startswith(b"\x00\x00\x00\x18ftypmp42")
        r = c.get("/demo/clips/c1/video", headers={"Range": "bytes=0-7"})
        assert r.status_code == 206 and r.content == b"\x00\x00\x00\x18ftyp"
        assert c.post("/mode", json={"mode": "demo"}).json() == {"mode": "demo"}
        st = c.get("/status").json()
        assert st["mode"] == "demo"
        assert c.post("/mode", json={"mode": "live"}).json() == {"mode": "live"}
        assert c.post("/mode", json={"mode": "x"}).status_code == 400


def test_demo_notify_once_and_disabled(tmp_path):
    with _client(tmp_path) as c:
        r1 = c.post("/demo/notify", json={"clip": "c1", "t": 1.0}).json()
        assert r1["status"] == "dashboard_only"  # default: telegram_first false
        r2 = c.post("/demo/notify", json={"clip": "c1", "t": 2.0}).json()
        assert r2["duplicate"] is True
```

- [ ] **Step 2: run, expect FAIL** — `ModuleNotFoundError: backend.demo.clips`.

- [ ] **Step 3: implement**

`backend/demo/clips.py`:
```python
"""Demo clip packages: manifest + <dir>/{clip.mp4, events.jsonl, meta.json}; timelines in demo/cache/."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "cache"


@dataclass
class ClipMeta:
    id: str
    name: str
    emotion: str
    duration_s: float
    has_timeline: bool


class DemoClips:
    def __init__(self, manifest: Path | None, bundled: Path) -> None:
        self._clips: dict[str, tuple[Path, dict]] = {}
        for src in ([manifest] if manifest and manifest.exists() else []) + [bundled / "manifest.json"]:
            try:
                doc = json.loads(Path(src).read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(doc.get("clips"), list):
                continue
            for c in doc["clips"]:
                if not isinstance(c, dict) or "id" not in c or "dir" not in c:
                    continue
                d = Path(src).parent / c["dir"]
                if (d / "clip.mp4").exists() and (d / "events.jsonl").exists():
                    self._clips.setdefault(c["id"], (d, c))
            if self._clips:
                break

    @property
    def available(self) -> bool:
        return bool(self._clips)

    def list(self) -> list[ClipMeta]:
        out = []
        for cid, (d, c) in self._clips.items():
            try:
                meta = json.loads((d / "meta.json").read_text())
            except (OSError, ValueError):
                meta = {}
            out.append(ClipMeta(cid, meta.get("name", c.get("name", cid)), meta.get("emotion", c.get("emotion", "unknown")),
                                float(meta.get("duration_s", 0.0)), (CACHE / f"{cid}.timeline.json").exists()))
        return out

    def _dir(self, cid: str) -> Path:
        try:
            return self._clips[cid][0]
        except KeyError:
            raise KeyError(f"unknown demo clip {cid!r}") from None

    def video_path(self, cid: str) -> Path:
        return self._dir(cid) / "clip.mp4"

    def events(self, cid: str) -> list[dict]:
        return [json.loads(line) for line in (self._dir(cid) / "events.jsonl").read_text().splitlines() if line.strip()]

    def timeline(self, cid: str) -> dict | None:
        p = CACHE / f"{cid}.timeline.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())


def load_demo_clips(cfg: dict) -> DemoClips:
    demo = cfg["web"]["demo"]
    return DemoClips(Path(demo["manifest"]), Path(demo["clips_dir"]))
```

Runtime changes (`backend/web/runtime.py`):
- Import: `from backend.demo.clips import DemoClips`.
- Signature: `def __init__(self, cfg, pipeline, interpreter, notifier, hub, clock=time.time, demo_clips: DemoClips | None = None):` — store as `self.demo`, and after `self._stopped = False` add:
  ```python
        self.demo = demo_clips
        self.mode: str = "demo" if is_demo(cfg) and demo_clips and demo_clips.available else "live"
        self.demo_notified: set[str] = set()
  ```
- Add methods (after `treat`):
  ```python
    def set_mode(self, mode: str) -> str:
        if mode not in ("live", "demo"):
            raise ValueError(f"mode must be live|demo, got {mode!r}")
        if mode == "demo" and (self.demo is None or not self.demo.available):
            raise ValueError("no demo clips available")
        self.mode = mode
        self.hub.publish("status", {"mode": mode, "modes": self._modes()})
        return mode

    def _modes(self) -> list[str]:
        return ["live", "demo"] if self.demo is not None and self.demo.available else ["live"]

    async def demo_notify(self, clip_id: str, t: float) -> dict:
        """First demo notification per clip: real Telegram only when telegram_first is on and the
        notifier is a Telegram one; otherwise a dashboard_only record. Always idempotent."""
        if clip_id in self.demo_notified:
            return {"status": "dashboard_only", "duplicate": True}
        self.demo_notified.add(clip_id)
        if self.cfg["web"]["demo"].get("telegram_first") and type(self.notifier).__name__ == "TelegramNotifier":
            jpeg = self._latest_jpeg()
            from backend.contracts import EmotionState
            res = await self.notifier.send(
                EmotionState(ts=t, emotion="unknown", confidence=0.0, source="rules",
                             reason=f"Demo clip {clip_id} at {t:.1f}s."), jpeg)
            return {"status": res.status, "detail": res.detail, "duplicate": False}
        return {"status": "dashboard_only", "detail": "would send to owner", "duplicate": False}
  ```
- In `status()`, add `"modes": self._modes(), "mode": self.mode,` to the returned dict. Update `tests/web/test_runtime.py`'s key set (append `"modes", "mode"`).

`backend/main.py` changes:
- Import: `from backend.demo.clips import load_demo_clips` and `from fastapi.responses import FileResponse` (extend the existing `from fastapi.responses import StreamingResponse` line).
- In `lifespan`, after `hub = ...`: `demo = load_demo_clips(cfg)`, and pass `demo_clips=demo` to `Runtime(...)`; also `app.state.demo = demo`.
- Add routes (before `return app`):
  ```python
    @app.get("/demo/clips")
    async def demo_clips() -> list[dict]:
        from dataclasses import asdict
        return [asdict(c) for c in app.state.demo.list()]

    @app.get("/demo/clips/{cid}/video")
    async def demo_video(cid: str) -> FileResponse:
        try:
            path = app.state.demo.video_path(cid)
        except KeyError:
            raise HTTPException(404, f"unknown demo clip {cid!r}")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/demo/clips/{cid}/events")
    async def demo_events(cid: str) -> dict:
        try:
            return {"events": app.state.demo.events(cid)}
        except KeyError:
            raise HTTPException(404, f"unknown demo clip {cid!r}")

    @app.get("/demo/clips/{cid}/timeline")
    async def demo_timeline(cid: str) -> dict:
        try:
            tl = app.state.demo.timeline(cid)
        except KeyError:
            raise HTTPException(404, f"unknown demo clip {cid!r}")
        if tl is None:
            raise HTTPException(404, f"no timeline for {cid!r} (run scripts/precompute_demo.py)")
        return tl

    @app.post("/mode")
    async def set_mode(body: dict) -> dict:
        try:
            return {"mode": app.state.rt.set_mode(body.get("mode"))}
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/demo/notify")
    async def demo_notify(body: dict) -> dict:
        return await app.state.rt.demo_notify(body.get("clip", ""), float(body.get("t", 0.0)))
  ```
- Add `HTTPException` to the fastapi imports.

- [ ] **Step 4: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_demo.py tests/web/test_runtime.py tests/web/test_main.py -q`. The runtime status-key test now expects `modes` and `mode`.
- [ ] **Step 5: commit** `git add backend/demo/clips.py backend/web/runtime.py backend/main.py tests/web/test_demo.py tests/web/test_runtime.py && git commit -m "Web step 9d: demo clip serving, mode switch,Modes in status"`

---

### Task 5: Pure demo selector — `lib/demo.ts`

**Files:**
- Create: `frontend/lib/demo.ts`
- Test: `frontend/lib/demo.test.ts`

**Interfaces — Produces:**
- Types: `ClipMeta {id, name, emotion: Emotion, duration_s, has_timeline}`, `ClipEvent {t, type: "frame"|"audio"|"rules"|"treat", data}`, `DemoTimeline {clip, duration_s, states: TimedState[], live: TimedState[], llm: unknown[], notifications: {t, emotion: Emotion, reason: string}[], treats: number[]}` with `TimedState {t, emotion: Emotion, confidence: number, source: "rules"|"llm"|"fused", reason: string}`, and `LoadedClip {meta: ClipMeta, events: ClipEvent[], timeline: DemoTimeline}`.
- `fetchClip(http: string, id: string): Promise<LoadedClip>` (GETs `/demo/clips` list? No — takes meta + fetches `.../events` and `.../timeline`; throws on HTTP error).
- `buildDemoView(clip: LoadedClip, t: number, dismissed: ReadonlySet<number>, dogName: string): DemoView` (pure) where `DemoView {frame: FrameEvent | null, history: Features[], emotion: EmotionState | null, currentSince: number | null, spans: Span[], audio: AudioMark[], treats: number[], notifications: NotificationItem[], toasts: Toast[]}` — the same shape `Dashboard` consumes from the live reducer.
  - `frame`: last frame event with `t <= now` (compare `data.ts`).
  - `history`: features of the last 24 such frames.
  - `emotion`: last timeline state with `t <= now`, as `EmotionState {ts: stateT, ...}`; `currentSince` = that state's `t`. None before the first state.
  - `spans`: consecutive timeline states (up to now; last span ends at `now`) as `Span[]`.
  - `audio`: audio events with `t <= now` (the Timeline windows them).
  - `treats`: treats `<= now`. `notifications`: timeline notifications `<= now` as `NotificationItem {id: <index>, ts, state: EmotionState{ts, ...}, status: "dashboard_only", detail: "Demo playback", read: true}`.
  - `toasts`: rebuilt deterministically from clip time — sticky negatives until dismissed (by notification index), positives for 6 s of play time. Toast ids are `demo-<notification index>`.

- [ ] **Step 1: failing test** `frontend/lib/demo.test.ts`
```ts
import { describe, expect, it } from "vitest";
import { buildDemoView } from "./demo";
import type { LoadedClip } from "./demo";

const frame = (t: number, tail = 0.5) => ({ t, type: "frame" as const, data: {
  ts: t, source: "clip", dog_detected: true, bbox: [0, 0, 10, 10] as [number, number, number, number], bbox_conf: 0.9,
  body_keypoints: {}, face_landmarks: null,
  features: { tail_height: tail, tail_wag_hz: 2, ear_position: "up" as const, mouth_open: 0.5, body_lowering: 0.1, motion_energy: 0.4, in_feeding_zone: true } } });
const audio = (t: number) => ({ t, type: "audio" as const, data: { ts: t, label: "yip" as const, score: 0.8 } });

const clip = (): LoadedClip => ({
  meta: { id: "c", name: "C", emotion: "excited", duration_s: 10, has_timeline: true },
  events: [frame(0), frame(1), audio(1.5), frame(2)],
  timeline: {
    clip: "c", duration_s: 10,
    states: [
      { t: 1, emotion: "relaxed", confidence: 0.7, source: "rules", reason: "Calm." },
      { t: 2, emotion: "excited", confidence: 0.9, source: "fused", reason: "Treat!" },
    ],
    live: [], llm: [],
    notifications: [{ t: 2.5, emotion: "excited", reason: "Treat!" }],
    treats: [1.0],
  },
});

describe("buildDemoView", () => {
  it("empty before the first state; frame/history follow event time", () => {
    const v = buildDemoView(clip(), 0.5, new Set(), "Bruno");
    expect(v.emotion).toBeNull();
    expect(v.frame?.data.ts).toBe(0);
    expect(v.history).toHaveLength(1);
    expect(v.spans).toEqual([]);
  });
  it("state, spans, treats and audio at t=3", () => {
    const v = buildDemoView(clip(), 3, new Set(), "Bruno");
    expect(v.emotion).toMatchObject({ emotion: "excited", ts: 2 });
    expect(v.currentSince).toBe(2);
    expect(v.spans.map((s) => [s.emotion, s.start, s.end])).toEqual([["relaxed", 1, 2], ["excited", 2, 3]]);
    expect(v.treats).toEqual([1.0]);
    expect(v.audio.map((a) => a.label)).toEqual(["yip"]);
    expect(v.frame?.data.ts).toBe(2);
  });
  it("notification toasts within 6 s; sticky negatives persist; dismissal works", () => {
    const v = buildDemoView(clip(), 3, new Set(), "Bruno");
    expect(v.toasts.map((t) => t.title)).toEqual(["Bruno is excited"]);
    const v2 = buildDemoView(clip(), 20, new Set(), "Bruno");
    expect(v2.toasts).toEqual([]);
    expect(v2.notifications).toHaveLength(1);
  });
  it("seeking backwards rebuilds (no accumulation)", () => {
    const c = clip();
    const fwd = buildDemoView(c, 3, new Set(), "Bruno");
    const back = buildDemoView(c, 0.5, new Set(), "Bruno");
    expect(back.spans).toEqual([]);
    expect(back.toasts).toEqual([]);
    expect(fwd.spans).toHaveLength(2);
  });
});
```
Run: `cd frontend && npm run test` → FAIL (module missing).

- [ ] **Step 2: implement** `frontend/lib/demo.ts`:
```ts
import type { AudioLabel, Emotion, EmotionState, Features, FrameEvent } from "./contracts";
import { NEGATIVE } from "./emotions";
import { FADE_MS, type Toast } from "./toasts";
import type { AudioMark, NotificationItem, Span } from "./types";

export type TimedState = { t: number; emotion: Emotion; confidence: number; source: "rules" | "llm" | "fused"; reason: string };
export type ClipMeta = { id: string; name: string; emotion: Emotion; duration_s: number; has_timeline: boolean };
export type ClipEvent = { t: number; type: "frame" | "audio" | "rules" | "treat"; data: any }; // eslint-disable-line @typescript-eslint/no-explicit-any
export type DemoTimeline = {
  clip: string; duration_s: number; states: TimedState[]; live: TimedState[]; llm: unknown[];
  notifications: { t: number; emotion: Emotion; reason: string }[]; treats: number[];
};
export type LoadedClip = { meta: ClipMeta; events: ClipEvent[]; timeline: DemoTimeline };

export type DemoView = {
  frame: FrameEvent | null; history: Features[]; emotion: EmotionState | null; currentSince: number | null;
  spans: Span[]; audio: AudioMark[]; treats: number[]; notifications: NotificationItem[]; toasts: Toast[];
};

export async function fetchClip(http: string, meta: ClipMeta): Promise<LoadedClip> {
  const get = async (path: string) => {
    const r = await fetch(`${http}${path}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  };
  const [events, timeline] = await Promise.all([
    get(`/demo/clips/${meta.id}/events`).then((d: { events: ClipEvent[] }) => d.events),
    get(`/demo/clips/${meta.id}/timeline`),
  ]);
  return { meta, events, timeline: timeline as DemoTimeline };
}

const asState = (s: TimedState): EmotionState => ({ ts: s.t, emotion: s.emotion, confidence: s.confidence, source: s.source, reason: s.reason, snapshot: null });

export function buildDemoView(clip: LoadedClip, t: number, dismissed: ReadonlySet<number>, dogName: string): DemoView {
  const frames = clip.events.filter((e) => e.type === "frame" && e.data.ts <= t);
  const frame = (frames[frames.length - 1]?.data as FrameEvent | undefined) ?? null;
  const history = frames.slice(-24).map((e) => (e.data as FrameEvent).features);
  const past = clip.timeline.states.filter((s) => s.t <= t);
  const cur = past[past.length - 1] ?? null;
  const spans: Span[] = past.map((s, i) => ({
    emotion: s.emotion, start: s.t,
    end: i + 1 < past.length ? past[i + 1].t : t,
    confidence: s.confidence, reason: s.reason, source: s.source,
  }));
  const audio: AudioMark[] = clip.events
    .filter((e) => e.type === "audio" && e.data.ts <= t)
    .map((e) => ({ ts: e.data.ts as number, label: e.data.label as AudioLabel, score: e.data.score as number }));
  const treats = clip.timeline.treats.filter((x) => x <= t);
  const notifications: NotificationItem[] = clip.timeline.notifications
    .map((n, i) => ({ id: i, ts: n.t, state: { ...asState({ ...n, confidence: 0.8, source: "rules" as const }), ts: n.t }, status: "dashboard_only" as const, detail: "Demo playback", read: true }))
    .filter((n) => n.ts <= t);
  // Demo toasts are rebuilt deterministically from clip time (seek-safe): sticky negatives
  // persist until dismissed (by notification index); positives show for 6 s of play time.
  const toasts: Toast[] = [];
  for (const n of notifications) {
    if (dismissed.has(n.id)) continue;
    const negative = NEGATIVE.has(n.state.emotion);
    if (!negative && (t - n.ts) * 1000 > FADE_MS) continue;
    toasts.push({ id: `demo-${n.id}`, kind: negative ? "negative" : "positive", emotion: n.state.emotion,
      title: `${dogName} ${negative ? "seems" : "is"} ${n.state.emotion}`, body: n.state.reason, ts: n.ts,
      status: "dashboard_only", detail: "Demo playback", count: 1, sticky: negative, createdAt: n.ts * 1000 });
  }
  return { frame, history, emotion: cur ? asState(cur) : null, currentSince: cur ? cur.t : null, spans, audio, treats, notifications, toasts };
}
```
Notes for the implementer: demo toasts are constructed directly (no grouping — precomputed timelines are sparse), so seeking rebuilds them deterministically. `NotificationItem.state.confidence` is 0.8 (timelines don't store it).

- [ ] **Step 3: run** — `npm run test` → demo 4 passed. `npm run typecheck && npm run lint` clean.

- [ ] **Step 4: commit**
```bash
git add frontend/lib/demo.ts frontend/lib/demo.test.ts
git commit -m "Web step 9e: pure demo selector (clip events + timeline -> dashboard view)"
```

---

### Task 6: Demo UI — banner, clip list, player, dashboard switching, `D` hotkey

**Files:**
- Create: `frontend/components/DemoBanner.tsx`, `frontend/components/ClipList.tsx`, `frontend/components/DemoPlayer.tsx`
- Modify: `frontend/components/Dashboard.tsx` (demo/live switching, `D` hotkey, demo toasts/bell wiring)

**Interfaces:**
- Consumes: `fetchClip`, `buildDemoView`, `LoadedClip`, `ClipMeta` (Task 5); `drawOverlay`, `fitContain` (Plan 2); `EmotionCard`, `Timeline`, `SignalsPanel`, `ToastStack`, `VideoPanel` (Plan 2); `useBackend().setMode/http` (Plan 2).
- Produces: `<DemoBanner onGoLive />`, `<ClipList clips activeId onPick />`, `<DemoPlayer http meta dogName />` (self-contained: loads its clip, plays video, derives the view, renders EmotionCard + overlay video + Signals + Timeline + toasts).

- [ ] **Step 1: banner + clip list**

`frontend/components/DemoBanner.tsx` (copy from `ui/screens/10-system-states.html`):
```tsx
type Props = { onGoLive: () => void };

export default function DemoBanner({ onGoLive }: Props) {
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-4 py-2.5 text-small"
      style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15l13-7.5z" /></svg>
      <span className="font-bold">Demo mode</span>
      <span>Playing a recorded clip, not the live camera</span>
      <span className="grow" />
      <button type="button" onClick={onGoLive}
        className="h-9 rounded-md border bg-white px-3 text-[13px] font-semibold"
        style={{ borderColor: "var(--accent)", color: "var(--accent-ink)" }}>
        Go live
      </button>
    </div>
  );
}
```

`frontend/components/ClipList.tsx`:
```tsx
"use client";

import { useEffect, useState } from "react";
import type { ClipMeta } from "@/lib/demo";
import { EMOTION_META } from "@/lib/emotions";
import { durationLabel } from "@/lib/format";
import EmotionIcon from "./EmotionIcon";

type Props = { http: string; activeId: string | null; onPick: (meta: ClipMeta) => void };

export default function ClipList({ http, activeId, onPick }: Props) {
  const [clips, setClips] = useState<ClipMeta[] | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`${http}/demo/clips`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((list: ClipMeta[]) => alive && setClips(list))
      .catch(() => alive && setClips([]));
    return () => { alive = false; };
  }, [http]);
  if (clips === null) return <div className="text-small text-muted">Loading clips…</div>;
  if (!clips.length) return <div className="text-small text-muted">No demo clips yet.</div>;
  return (
    <div role="listbox" aria-label="Demo clips" className="flex flex-col gap-1">
      {clips.map((c) => {
        const active = c.id === activeId;
        return (
          <button key={c.id} type="button" role="option" aria-selected={active} onClick={() => onPick(c)}
            className="flex h-11 items-center gap-3 rounded-md border border-border bg-surface px-3 text-left">
            <EmotionIcon emotion={c.emotion} size={20} />
            <span className="grow truncate text-small font-semibold">{c.name}</span>
            <span className="text-small font-semibold" style={{ color: `var(--${c.emotion}-fg)` }}>{EMOTION_META[c.emotion].label}</span>
            <span className="font-mono text-[12px] text-muted">{durationLabel(c.duration_s)}</span>
            {active && <span className="h-5 w-1 rounded-full" style={{ background: "var(--accent)" }} />}
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: player** — `frontend/components/DemoPlayer.tsx`:
```tsx
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildDemoView, fetchClip, type ClipMeta, type LoadedClip } from "@/lib/demo";
import { fitContain, drawOverlay } from "@/lib/overlay";
import { serverNow } from "@/lib/store";
import EmotionCard from "./EmotionCard";
import SignalsPanel from "./SignalsPanel";
import Timeline from "./Timeline";
import ToastStack from "./ToastStack";

type Props = { http: string; meta: ClipMeta; dogName: string; location: string };

export default function DemoPlayer({ http, meta, dogName, location }: Props) {
  const [clip, setClip] = useState<LoadedClip | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [t, setT] = useState(0);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<number | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });

  useEffect(() => {
    let alive = true;
    setClip(null); setError(null); setT(0); setDismissed(new Set()); setSelected(null);
    fetchClip(http, meta).then((c) => alive && setClip(c)).catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [http, meta]);

  const view = useMemo(() => (clip ? buildDemoView(clip, t, dismissed, dogName) : null), [clip, t, dismissed, dogName]);
  const nowSec = t;

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => setT(v.currentTime);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeked", onTime);
    return () => { v.removeEventListener("timeupdate", onTime); v.removeEventListener("seeked", onTime); };
  }, [clip]);

  useEffect(() => {
    const canvas = canvasRef.current, v = videoRef.current;
    if (!canvas || !v || box.w === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(box.w * dpr);
    canvas.height = Math.round(box.h * dpr);
    const ctx = canvas.getContext("2d");
    if (ctx) drawOverlay(ctx, view?.frame ?? null, fitContain(v.videoWidth || 640, v.videoHeight || 480, box.w, box.h), { box: true, skeleton: true, face: true }, dpr);
  }, [view, box]);

  const onView = useCallback((ts: number) => {
    const v = videoRef.current;
    if (v) { v.currentTime = Math.min(Math.max(0, ts), v.duration || ts); v.play().catch(() => undefined); }
  }, []);

  if (error) return <div role="alert" className="rounded-xl border border-border bg-surface p-6 text-small">Couldn&apos;t load this clip: {error}</div>;
  if (!clip || !view) return <div className="rounded-xl border border-border bg-surface p-6 text-small text-muted">Loading clip…</div>;

  return (
    <div className="flex flex-col gap-4 lg:gap-6">
      <div className="grid gap-4 [grid-template-areas:'card'_'video'_'signals'] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[auto_auto_1fr] lg:gap-6 lg:[grid-template-areas:'video_card'_'video_treat'_'video_signals']">
        <div className="min-h-[216px] [grid-area:video] lg:min-h-[480px]">
          <section aria-label={`Demo clip: ${meta.name}`} className="relative h-full min-h-[216px] overflow-hidden rounded-xl bg-[#1B1D1F]"
            ref={(el) => { if (el) { const r = el.getBoundingClientRect(); setBox((b) => (Math.abs(b.w - r.width) > 1 || Math.abs(b.h - r.height) > 1 ? { w: r.width, h: r.height } : b)); } }}>
            {/* eslint-disable-next-line jsx-a11y/media-has-caption -- demo clips have no captions */}
            <video ref={videoRef} src={`${http}/demo/clips/${meta.id}/video`} controls playsInline preload="auto"
              className="absolute inset-0 h-full w-full object-contain" />
            <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />
            <div className="absolute left-4 top-4 flex h-[30px] items-center gap-2 rounded-lg px-3 text-[12px] font-bold tracking-[0.06em] text-white" style={{ background: "var(--overlay-scrim)" }}>
              <span className="h-2 w-2 rounded-full" style={{ background: "var(--live)" }} />DEMO
            </div>
          </section>
        </div>
        <div className="[grid-area:card]">
          <EmotionCard current={view.emotion} currentSince={view.currentSince} lastEmotionAt={Date.now()}
            nowMs={Date.now()} nowSec={nowSec} paused={false} projector={false} dogName={dogName}
            lastDogTs={null} lastSeenEmotion={null} />
        </div>
        <div className="[grid-area:signals]">
          <SignalsPanel history={view.history} defaultOpen />
        </div>
      </div>
      <Timeline spans={view.spans} audio={view.audio} treats={view.treats} notifications={view.notifications}
        nowSec={nowSec} selected={selected} onSelect={setSelected} />
      <ToastStack toasts={view.toasts}
        onDismiss={(id) => {
          const m = /^demo-(\d+)$/.exec(id);
          if (m) setDismissed((d) => new Set(d).add(Number(m[1])));
        }}
        onView={onView} />
    </div>
  );
}
```

- [ ] **Step 3: dashboard wiring** — in `frontend/components/Dashboard.tsx`:
  - Imports: `import ClipList from "./ClipList";`, `import DemoBanner from "./DemoBanner";`, `import DemoPlayer from "./DemoPlayer";`, and `import type { ClipMeta } from "@/lib/demo";`.
  - State (below `selectedSpan`): `const [demoClip, setDemoClip] = useState<ClipMeta | null>(null);`
  - Mode: `const mode = state.status?.mode ?? "live"; const demo = mode === "demo";`
  - Hotkey effect: extend the existing `onKey` — `if (e.key.toLowerCase() === "d" && !e.metaKey && ...) { void setMode(demo ? "live" : "demo"); return; }`. T stays treat **and live-only**: `if (demo) return;` before the treat call.
  - Render: after `<StatusBar ... />`, insert `{demo && <DemoBanner onGoLive={() => void setMode("live")} />}`.
  - Replace the main grid + timeline block with a conditional: when `demo`, render `<ClipList http={backend.http} activeId={demoClip?.id ?? null} onPick={setDemoClip} />` followed by `{demoClip ? <DemoPlayer http={backend.http} meta={demoClip} dogName={profile.dog_name} location={profile.location} /> : <div className="text-small text-muted">Pick a clip to replay it through the full pipeline.</div>}`; otherwise the existing live grid + timeline. Keep header, status bar, notices, mobile treat bar (hidden in demo: `{!demo && (...)}`), toasts (live) as-is.
  - `DemoPlayer` needs `location`? It doesn't use it (no location chip in the demo variant). Drop the prop: `<DemoPlayer http meta dogName />`. Remove `location` from its Props.

- [ ] **Step 4: verify**
- `npm run test && npm run typecheck && npm run lint && npm run build` → green.
- Manual (backend running with the placeholder clip + precomputed timeline; frontend dev): set `web.pipeline: mock`, open the dashboard, flip the **Demo** toggle (now enabled — `/status` lists `modes: ["live","demo"]`). The banner reads "Demo mode · Playing a recorded clip, not the live camera" with **Go live**. The clip list shows "Mock day · Excited · 1:30". Pick it: the video plays, the emotion card/timeline/signals follow, toasts appear at notification times. Pause and seek: everything stays in sync. Press **D**: back to live. Then `DEMO_MODE=1 make dev-backend`: the dashboard opens directly in demo mode.

- [ ] **Step 5: commit**
```bash
git add frontend/components/DemoBanner.tsx frontend/components/ClipList.tsx frontend/components/DemoPlayer.tsx frontend/components/Dashboard.tsx
git commit -m "Web step 9f: demo banner, clip list, demo player, live/demo switching + D hotkey"
```

---

### Task 7: Real-pipeline integration checklist script

**Files:**
- Create: `scripts/check_integration.py`
- Test: `tests/web/test_check_integration.py` (unit-tests the report builder against a fake WS log)

**Interfaces — Produces:** `scripts/check_integration.py [--base http://localhost:8000] [--seconds 30]` → connects to a running backend, samples `/status`, records `/ws` envelopes for N seconds, validates each `frame`/`audio`/`rules`/`emotion` against `backend/contracts.py`, measures per-type rates and WS latency (envelope `ts` vs receipt time), prints a PASS/FAIL report and exits non-zero on failure. Pure helper `analyse(messages: list[dict], status: dict) -> dict` (testable without a server).

- [ ] **Step 1: failing test** `tests/web/test_check_integration.py`
```python
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from check_integration import analyse


def _env(t, ts, data=None, meta=None):
    m = {"type": t, "seq": 1, "ts": ts, "data": data or {}}
    if meta:
        m["meta"] = meta
    return m


def test_analyse_pass():
    now = time.time()
    msgs = [
        _env("frame", now - 2, {"ts": now - 2, "source": "live", "dog_detected": True, "body_keypoints": {}, "features": {}}),
        _env("audio", now - 1, {"ts": now - 1, "label": "bark", "score": 0.9}),
        _env("rules", now - 1, {"ts": now - 1, "emotion": "relaxed", "confidence": 0.6,
                                "scores": {"relaxed": 0.6, "happy": 0.1}}),
        _env("emotion", now, {"ts": now, "emotion": "relaxed", "confidence": 0.6, "source": "rules", "reason": "r"}),
    ]
    rep = analyse(msgs, {"pipeline": "real", "fps": 8.0}, now)
    assert rep["ok"] and rep["invalid"] == 0 and rep["rates"]["frame"] > 0
    assert rep["max_latency_s"] < 5.0


def test_analyse_fails_on_bad_contract_and_silence():
    rep = analyse([_env("frame", 1.0, {"ts": 1.0, "source": "live"})], {"pipeline": "real"}, time.time())
    assert not rep["ok"] and rep["invalid"] == 1
    rep2 = analyse([], {"pipeline": "real"}, time.time())
    assert not rep2["ok"] and "no messages" in " ".join(rep2["problems"]).lower()
```

- [ ] **Step 2: run, expect FAIL.**

- [ ] **Step 3: implement** `scripts/check_integration.py`:
```python
"""Integration checklist against a RUNNING backend (Person A's real pipeline or the mock).

    .venv/bin/python scripts/check_integration.py [--base http://localhost:8000] [--seconds 30]

Samples /status, records /ws envelopes, validates frame/audio/rules/emotion against contracts.py,
reports per-type rates + WS latency. Exit 0 = PASS, 1 = FAIL. Set web.pipeline: real in config.yaml
first, with a fallback clip in file mode (then the webcam).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from backend.contracts import AudioEvent, EmotionState, FrameEvent, RulesLabel  # noqa: E402
from backend.web.hub import MESSAGE_TYPES  # noqa: E402

CHECKED = {"frame": FrameEvent, "audio": AudioEvent, "rules": RulesLabel, "emotion": EmotionState}
MIN_RATES = {"frame": 3.0, "rules": 3.0}  # per second; audio/emotion are event-driven
MAX_LATENCY_S = 2.0


def analyse(messages: list[dict], status: dict, now: float) -> dict:
    problems: list[str] = []
    invalid = 0
    latencies: list[float] = []
    kinds = Counter()
    first_ts, last_ts = None, None
    for m in messages:
        kinds[m.get("type")] += 1
        model = CHECKED.get(m.get("type"))
        if model is not None:
            try:
                model.model_validate(m.get("data"))
            except ValidationError as exc:
                invalid += 1
                problems.append(f"invalid {m.get('type')}: {exc.errors()[0]['loc']}: {exc.errors()[0]['msg']}")
            else:
                ts = (m.get("data") or {}).get("ts")
                if isinstance(ts, (int, float)):
                    latencies.append(max(0.0, now - ts))
        if isinstance(m.get("ts"), (int, float)):
            first_ts = m["ts"] if first_ts is None else min(first_ts, m["ts"])
            last_ts = m["ts"] if last_ts is None else max(last_ts, m["ts"])
    span = max(0.001, (last_ts or now) - (first_ts or now))
    rates = {k: round(kinds.get(k, 0) / span, 2) for k in CHECKED}
    for k, minimum in MIN_RATES.items():
        if rates[k] < minimum:
            problems.append(f"{k} rate {rates[k]}/s below {minimum}/s (expected ~8 fps in file mode)")
    max_lat = round(max(latencies, default=0.0), 3)
    if max_lat > MAX_LATENCY_S:
        problems.append(f"max WS latency {max_lat}s above {MAX_LATENCY_S}s (event loop blocked?)")
    if not messages:
        problems.append("no messages received: is the pipeline running?")
    unknown = [k for k in kinds if k not in MESSAGE_TYPES]
    if unknown:
        problems.append(f"unknown envelope types: {unknown}")
    return {"ok": not problems, "problems": problems, "invalid": invalid, "rates": rates,
            "max_latency_s": max_lat, "counts": dict(kinds), "pipeline": status.get("pipeline")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--seconds", type=float, default=30.0)
    a = ap.parse_args(argv)

    def get(path: str):
        with urllib.request.urlopen(a.base + path, timeout=10) as r:
            return json.load(r)

    try:
        status = get("/status")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: backend unreachable: {exc}")
        return 1
    print(f"pipeline={status.get('pipeline')} mode={status.get('mode')} fps={status.get('fps')}")
    try:
        from websockets.sync.client import connect
    except ImportError:
        print("FAIL: pip install websockets (in requirements-web.txt)")
        return 1
    messages: list[dict] = []
    deadline = time.time() + a.seconds
    with connect(a.base.replace("http", "ws") + "/ws", max_size=10_000_000) as ws:
        while time.time() < deadline:
            try:
                messages.append(json.loads(ws.recv(timeout=max(0.1, deadline - time.time()))))
            except TimeoutError:
                break
    rep = analyse(messages, status, time.time())
    print(f"counts={rep['counts']} rates={rep['rates']} max_latency={rep['max_latency_s']}s invalid={rep['invalid']}")
    for p in rep["problems"]:
        print("FAIL:", p)
    print("PASS" if rep["ok"] else "FAIL")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```
Note: `ws.recv(timeout=...)` raises `TimeoutError` in `websockets` ≥ 14 sync client. If the installed version raises a different timeout error, catch it there instead — the test doesn't cover this path, so verify by running the script against the mock backend in Step 4.

- [ ] **Step 4: run, expect PASS** — `.venv/bin/python -m pytest tests/web/test_check_integration.py -q`, then live: `make dev-backend` in one terminal, `.venv/bin/python scripts/check_integration.py --seconds 15` in another → PASS against the mock (frame/rules ≥ 3/s).
- [ ] **Step 5: commit** `git add scripts/check_integration.py tests/web/test_check_integration.py && git commit -m "Web step 10: live-pipeline integration checklist (rates, contracts, latency)"`

---

### Task 8: Provider benchmark, soak script, failure drills, airplane-mode test

**Files:**
- Create: `scripts/llm_benchmark.py`, `scripts/soak.py`
- Modify: `docs/DEMO_RUNBOOK.md` (+ "Offline fallback demo" + "Failure drills"), `CLAUDE.md` (Commands: demo + harden lines)

**Interfaces — Produces:**
- `scripts/llm_benchmark.py [--frames DIR] [--n 20] [--rules labels.jsonl]` — loads up to N JPEGs (extracted from a clip or saved frames), calls the configured provider once per frame (features stub: mid-range values; rules label from labels.jsonl line i if given), prints median/p95 latency, parse-failure rate and rules-agreement rate; exit 0. Run once per provider via env (Groq, then OpenRouter) and record both.
- `scripts/soak.py [--base http://localhost:8000] [--minutes 20]` — connects to `/ws`, counts messages per type, samples server RSS from `/proc/<pid>/status` (finds the uvicorn pid via `--pid` or by matching the port with `ss`; falls back to "RSS unavailable" on macOS), prints per-minute counts + RSS delta, FAILs if RSS grows > 100 MB or message rate drops to zero for > 30 s.

- [ ] **Step 1: implement both scripts** (no unit tests — they're manual tools; verify by running).

`scripts/llm_benchmark.py`:
```python
"""Benchmark the configured LLM provider over N saved frames.

    .venv/bin/python scripts/llm_benchmark.py --frames out/bench-frames [--n 20] [--rules out/bench-rules.jsonl]

Run once with Groq env and once with OpenRouter env; recommend the default from median/p95 latency,
parse-failure rate and rules agreement. Frames: extract with
`ffmpeg -i clip.mp4 -vf fps=1 out/bench-frames/f%03d.jpg` (imageio-ffmpeg binary works too).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend.contracts import EMOTIONS, Features, RulesLabel  # noqa: E402
from backend.fusion.llm_interpreter import LLMInterpreter  # noqa: E402
from backend.web.settings import LLMSettings, load_config  # noqa: E402


async def one(it: LLMInterpreter, jpeg: bytes, now: float, rules: RulesLabel | None) -> dict:
    feats = [(now - 1, Features(tail_height=0.4, tail_wag_hz=2.0, ear_position="neutral", mouth_open=0.5,
                                body_lowering=0.1, motion_energy=0.4, in_feeding_zone=True))]
    r = await it.interpret(trigger="benchmark", now=now, features=feats, audio=[], rules=rules, jpeg=jpeg)
    last = it.last_call or {}
    agree = r is not None and rules is not None and r.emotion == rules.emotion
    return {"ok": r is not None, "latency_ms": last.get("latency_ms"), "agree": agree}


async def amain(a: argparse.Namespace, paths: list[Path], rules: list[RulesLabel | None]) -> list[dict]:
    load_dotenv()
    s = LLMSettings.from_config(load_config())
    if not s.enabled:
        sys.exit("LLM not configured (.env)")
    it = LLMInterpreter(s)
    out = []
    for i, p in enumerate(paths):
        out.append(await one(it, p.read_bytes(), time.time(), rules[i] if i < len(rules) else None))
        print(f"{i + 1}/{len(paths)}: {out[-1]}", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--rules", default=None, help="jsonl of RulesLabel, one per frame")
    a = ap.parse_args(argv)
    paths = sorted(Path(a.frames).glob("*.jpg"))[: a.n]
    if not paths:
        sys.exit(f"no jpgs in {a.frames}")
    rules: list = []
    if a.rules:
        rules = [RulesLabel.model_validate(json.loads(line)) for line in Path(a.rules).read_text().splitlines() if line.strip()]
    res = asyncio.run(amain(a, paths, rules))
    lat = sorted(r["latency_ms"] for r in res if r["latency_ms"] is not None)
    ok = [r for r in res if r["ok"]]
    print(f"n={len(res)} parsed={len(ok)} ({len(ok)/len(res):.0%}) "
          f"median={statistics.median(lat):.0f}ms p95={lat[min(len(lat)-1, int(len(lat)*0.95))]:.0f}ms "
          f"agree={sum(r['agree'] for r in res)}/{len(res)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`scripts/soak.py`:
```python
"""Soak test: 20 min on /ws, flags RSS growth and message stalls.

    .venv/bin/python scripts/soak.py [--base http://localhost:8000] [--minutes 20] [--pid PID]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def rss_mb(pid: int) -> float | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return float(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def find_pid_by_port(port: int) -> int | None:
    try:
        out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if f":{port}" in line and "pid=" in line:
            try:
                return int(line.split("pid=")[1].split(",")[0])
            except ValueError:
                pass
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--pid", type=int, default=None)
    a = ap.parse_args(argv)
    from websockets.sync.client import connect

    pid = a.pid or find_pid_by_port(int(a.base.rsplit(":", 1)[1].split("/")[0]))
    start_rss = rss_mb(pid) if pid else None
    print(f"server pid={pid} start_rss={start_rss}MB")
    counts: Counter = Counter()
    per_minute: Counter = Counter()
    start = last_msg = time.time()
    minute = 0
    fails: list[str] = []
    with connect(a.base.replace("http", "ws") + "/ws", max_size=10_000_000) as ws:
        while (time.time() - start) < a.minutes * 60:
            try:
                m = json.loads(ws.recv(timeout=5.0))
                counts[m.get("type")] += 1
                per_minute[m.get("type")] += 1
                last_msg = time.time()
            except TimeoutError:
                if time.time() - last_msg > 30:
                    fails.append("no WS messages for > 30 s")
                    break
            if time.time() - start >= (minute + 1) * 60:
                minute += 1
                rss = rss_mb(pid) if pid else None
                print(f"min {minute}: {dict(per_minute)} rss={rss}MB", flush=True)
                per_minute.clear()
    end_rss = rss_mb(pid) if pid else None
    print(f"counts={dict(counts)} rss {start_rss} -> {end_rss}MB")
    if start_rss and end_rss and end_rss - start_rss > 100:
        fails.append(f"RSS grew {end_rss - start_rss:.0f}MB (> 100MB)")
    for f in fails:
        print("FAIL:", f)
    print("PASS" if not fails else "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: verify** — `.venv/bin/python scripts/llm_benchmark.py --help` and `.venv/bin/python scripts/soak.py --help` (argparse works); `.venv/bin/python scripts/soak.py --minutes 0.2` against `make dev-backend` (runs ~12 s, prints per-minute line only at minute boundaries — with 0.2 min it prints just the summary; PASS expected on the mock).

- [ ] **Step 3: runbook + CLAUDE.md** — append to `docs/DEMO_RUNBOOK.md`:
```markdown
## Offline fallback demo

1. While online: `.venv/bin/python scripts/precompute_demo.py` (commits fresh timelines), then
   `cd frontend && npm run build` (fonts + pages baked in).
2. Switch the laptop to airplane mode. Start the backend: `DEMO_MODE=1 make dev-backend`.
   Start the frontend: `cd frontend && npm run start` (NOT `dev` — dev mode needs network for HMR).
3. Open http://localhost:3000, flip to **Demo**, pick a clip. Video, emotions, toasts and timeline
   all run from localhost. Full test: airplane mode, full run-through of one clip.

## Failure drills (rehearse once before the demo)

- Kill the network mid-session: dashboard shows "Reconnecting…", keeps the last state greyed, recovers
  without reload. LLM calls fail → "AI offline · rules only".
- Revoke the API key (bad `LLM_API_KEY`): same rules-only fallback; check `llm_smoke_test.py` reports `error`.
- Stop the pipeline / kill the backend: banner + paused card; restart, everything resumes.
- Disconnect the phone page: camera banner appears; reopen the page, streaming resumes.
- WebSocket hard refresh mid-demo: `/events` bootstrap restores timeline + last state.
```
And in `CLAUDE.md` Commands, after the telegram line add:
```
make demo                     # DEMO_MODE=1 backend (offline fallback path; frontend: npm run start)
python scripts/check_integration.py --seconds 30   # live-pipeline checklist (Step 10)
python scripts/llm_benchmark.py --frames <dir> --n 20  # provider comparison (Step 11)
python scripts/soak.py --minutes 20                    # leak + stall check (Step 11)
```

- [ ] **Step 4: commit** `git add scripts/llm_benchmark.py scripts/soak.py docs/DEMO_RUNBOOK.md CLAUDE.md && git commit -m "Web step 11: provider benchmark, soak script, failure drills"`

---

## Self-review (done while writing)

- **Spec coverage:** Step 9.1 precompute → Task 3; 9.2 frontend playback keyed to currentTime → Tasks 5–6; 9.3 demo toasts + telegram-first → Tasks 4/6; 9.4 picker + hotkey → Task 6. Step 10 integration + mismatch template → Task 7 + handoff doc. Step 11 benchmarks, drills, soak, runbook → Task 8. Clip fallback when Person A is late → manifest fallback (Tasks 1–2). Zero-network demo → Task 6 note + airplane test (Task 8 runbook).
- **Consistency:** `Runtime(..., demo_clips=None)` is backward compatible with all Plan 1/3 tests; the status key set gains `modes`+`mode` (test_runtime updated). `Toast` shape matches `lib/toasts.ts`. `ClipEvent.data` for frames validates against `FrameEvent` (validator Task 1). Timeline `states` use clip-relative `t`; `buildDemoView` never mixes server clocks in.
- **Known risks:** (1) `ws.recv(timeout=)` exception type varies by `websockets` version — each script notes where to adjust; verify live in the task. (2) The 90 s placeholder's exact size is unknown until Task 2 runs it; the task asserts < 5 MB and the test asserts < 1 MB for the 4 s version. (3) `POST /mode` while Person A's real pipeline runs: mode only affects what the dashboard shows (frontend), never the pipeline — no interference.
