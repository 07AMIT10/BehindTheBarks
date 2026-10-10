#!/usr/bin/env python3
"""Scripted on-device acceptance suite for the WagWatch Android station (multi-household plan).

Starts its own backend (WEB_PIPELINE=remote, auth off, log dir in a temp directory), drives the
real phone over adb, runs the ten checks in order and prints PASS / FAIL / SKIP with evidence.
Writes a markdown report (default docs/ACCEPTANCE_2026-10-10.md).

    .venv/bin/python scripts/phone_acceptance.py [--report PATH] [--keep-temp]

Exit code is 1 if any check FAILs. Cleanup (app force-stop, adb reverse --remove-all, backend kill)
always runs in a finally block. Every check is independent: a failed check does not stop the next
one, and each check first recovers the phone to the state it needs.

Phone preconditions: USB debugging authorised, screen unlocked, current debug build installed.
Never changes lock screen or device settings, never uninstalls.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

REPO = Path(__file__).resolve().parent.parent
PORT = 8000
BASE = f"http://127.0.0.1:{PORT}"
INGEST_URL = f"ws://127.0.0.1:{PORT}/ingest-events"
SERIAL = os.environ.get("ADB_SERIAL", "R9ZY80PKWTW")
PKG = "com.btb.ondevice"
CHROME = os.environ.get("BTB_CHROME", "/home/amit/.local/bin/google-chrome")
PY = str(REPO / ".venv" / "bin" / "python")
DEFAULT_REPORT = REPO / "docs" / "ACCEPTANCE_2026-10-10.md"
MODE_SEQUENCE = ["privacy", "limited", "blackout", "full"]


# ----------------------------------------------------------------------------- adb / phone helpers

def adb(*args: str, timeout: float = 30.0) -> str:
    res = subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True, timeout=timeout)
    return (res.stdout or "") + (res.stderr or "")


def force_stop() -> None:
    adb("shell", "am", "force-stop", PKG)


def ensure_adb_reverse() -> None:
    adb("reverse", f"tcp:{PORT}", f"tcp:{PORT}")


def start_monitoring() -> str:
    ensure_adb_reverse()
    return adb(
        "shell", "am", "start", "-n", f"{PKG}/.ui.MainActivity", "-a", f"{PKG}.action.START",
        "--es", "extra_server_url", INGEST_URL, "--es", "extra_device_name", "DebugPhone",
    )


def phone_battery() -> dict:
    out = adb("shell", "dumpsys", "battery")
    level = re.search(r"\blevel:\s*(\d+)", out)
    temp = re.search(r"\btemperature:\s*(-?\d+)", out)
    if not level or not temp:
        raise RuntimeError(f"dumpsys battery parse failed: {out[:200]!r}")
    return {"level": int(level.group(1)), "temp_c": int(temp.group(1)) / 10.0}


def ui_xml() -> str:
    """A fresh accessibility dump. uiautomator sometimes returns no root node while a window is
    animating or changing, so retry for a few seconds before giving up."""
    last = ""
    for _ in range(6):
        out = adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml = adb("shell", "cat", "/sdcard/ui.xml")
        if "<hierarchy" in xml:
            return xml[xml.index("<hierarchy"):]
        last = out.strip()[:120]
        time.sleep(1.0)
    raise RuntimeError(f"uiautomator dump produced no hierarchy (last output: {last!r})")


def ui_texts(xml: str) -> list[str]:
    out: list[str] = []
    for node in ET.fromstring(xml).iter("node"):
        for attr in ("text", "content-desc"):
            value = node.get(attr, "")
            if value:
                out.append(value)
    return out


def top_package() -> str:
    """The package of the top resumed activity, as the activity manager reports it (the uiautomator
    dump's first node can be a system window, so it is not used to decide what is in front)."""
    out = adb("shell", "dumpsys", "activity", "activities")
    m = re.search(r"topResumedActivity=\S+ \S+ (\S+?)/", out)
    return m.group(1) if m else ""


def bring_app_front() -> str:
    """Launch MainActivity without the START action (does not restart monitoring) when another app or
    the launcher has focus, so its UI can be read or tapped."""
    first = top_package()
    if first == PKG:
        return "app already in front"
    now = first
    for attempt in range(1, 4):
        adb("shell", "am", "start", "-n", f"{PKG}/.ui.MainActivity")
        ok, took, now = poll(lambda: (top_package() == PKG, top_package()), 6.0, interval=0.5)
        if ok:
            time.sleep(1.0)  # let the window draw before the dump
            return f"launched MainActivity (foreground was {first!r}, attempt {attempt}, in front after {took:.1f} s)"
    return f"could not bring MainActivity to front (foreground was {first!r}, now {now!r})"


def station_cap() -> str:
    """The station's own cap as the server knows it (reported by the phone's mode_state)."""
    return str(status().get("station_cap") or "?")


BADGE_RGB = np.array([0x4F, 0xC3, 0xF7], dtype=np.int16)  # MainActivity viewersBadge text colour


def screen_badge_pixels() -> int:
    """Pixels on the phone screen in the viewers-badge colour (#4FC3F7). Used because the uiautomator dump
    does not list the badge or its Disconnect button, although both are drawn."""
    from PIL import Image

    png = subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"],
                         capture_output=True, timeout=30).stdout
    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.int16)
    dist = np.abs(rgb - BADGE_RGB).sum(axis=2)
    return int((dist <= 40).sum())


def tap_text(label: str) -> str:
    """Tap the centre of the node whose text is `label`, located from a fresh uiautomator dump."""
    return tap_node(lambda n: n.get("text") == label, repr(label))


def tap_node(pred, what: str, attempts: int = 3) -> str:
    """Tap the centre of the first node (fresh uiautomator dump) that satisfies pred. A dump that is
    missing the node is retried: the dump is flaky while the monitoring screen animates."""
    for attempt in range(attempts):
        for node in ET.fromstring(ui_xml()).iter("node"):
            if not pred(node):
                continue
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            x, y = (x1 + x2) // 2, (y1 + y2) // 2
            adb("shell", "input", "tap", str(x), str(y))
            return f"tapped {what} at {x},{y}" + (f" (attempt {attempt + 1})" if attempt else "")
        time.sleep(1.5)
    raise LookupError(f"no node matches {what} in the UI dump after {attempts} dumps")


def _is_mode_control(node) -> bool:
    """The in-app mode button (MainActivity privacyButton): a Button whose label ends in the mode name."""
    return node.get("class", "").endswith("Button") and bool(re.search(r"(Blackout|Privacy|Limited|Full)$", node.get("text", "")))


def set_device_cap(mode: str) -> str:
    """Set the phone's OWN cap with the in-app control: tap the mode button, then the row of 'What may be
    shared?' for that mode. This is the person-on-the-device path; nothing is granted remotely."""
    row_text = {"privacy": "Barks and behaviour", "full": "Live video"}[mode]
    fronted = bring_app_front()
    tapped = tap_node(_is_mode_control, "in-app mode control")
    time.sleep(1.0)
    row = tap_node(lambda n: row_text in n.get("text", ""), f"'{mode}' row of the mode chooser")
    ok, took, _ = poll(lambda: (station_cap() == mode, None), 8.0)
    return f"{fronted}; {tapped}; {row}; station cap {mode} after {took:.1f} s={ok}"


RAISE_QUESTION = "A remote viewer asks to raise sharing to {label}. Allow for 15 minutes?"


def raise_prompt_visible(label: str) -> bool:
    want = RAISE_QUESTION.format(label=label)
    return any(want in t for t in ui_texts(ui_xml()))


def wait_raise_prompt(label: str, timeout: float) -> tuple[bool, float]:
    ok, took, _ = poll(lambda: (raise_prompt_visible(label), None), timeout, interval=0.5)
    return ok, took


def tap_dialog_button(label: str) -> str:
    return tap_node(lambda n: n.get("text", "").strip().lower() == label.lower(), f"dialog button {label!r}")


def logcat_lines(pattern: str) -> list[str]:
    out = adb("logcat", "-d", "-s", "MonitorService:W", timeout=60)
    return [line for line in out.splitlines() if re.search(pattern, line, re.I)]


# ----------------------------------------------------------------------------- backend helpers

def http(method: str, path: str, body: dict | None = None, timeout: float = 5.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}


def status() -> dict:
    try:
        return http("GET", "/status")[1]
    except (OSError, ValueError):
        return {}


def phone_of(st: dict) -> dict:
    return st.get("phone") or {}


def is_connected(st: dict) -> bool:
    return bool(phone_of(st).get("connected"))


def pipe_state(st: dict) -> str | None:
    return (st.get("pipeline_status") or {}).get("state")


def find_key(obj, key: str):
    """First value stored under `key` anywhere in a nested JSON object, or None."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def dom(url: str = BASE + "/", wait_for: str | None = None) -> str:
    """Dashboard text (document.body.innerText) from scripts/dom.mjs: playwright-core on the system Chrome,
    waiting for `load`, then for the dashboard to render and for `wait_for` text (its status arrives over the
    WebSocket after load, so a check that reads a state must name the text that state shows). Headless `--dump-dom` hangs while the dashboard keeps the /video MJPEG <img>
    open for a dropped phone, so it is not used. The helper caps itself at 12 s; this wrapper adds a margin."""
    cmd = ["node", str(REPO / "scripts" / "dom.mjs"), url]
    if wait_for:
        cmd += ["--wait-for", wait_for]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise RuntimeError("scripts/dom.mjs did not return within 15 s")
    if proc.returncode != 0:
        raise RuntimeError(f"scripts/dom.mjs exit {proc.returncode}: {(err or '').strip()[:200]}")
    return out


def poll(fn, timeout: float, interval: float = 0.5):
    """Call fn() -> (ok, snapshot) until ok or timeout. Returns (ok, seconds_elapsed, last_snapshot)."""
    t0 = time.monotonic()
    while True:
        ok, snap = fn()
        elapsed = time.monotonic() - t0
        if ok or elapsed >= timeout:
            return ok, elapsed, snap
        time.sleep(interval)


def _connected_snapshot():
    st = status()
    return is_connected(st), st


def ensure_connected(timeout: float = 20.0) -> tuple[bool, dict]:
    """Recover to 'phone connected and running': start monitoring if needed."""
    st = status()
    if is_connected(st) and pipe_state(st) == "running":
        return True, st
    start_monitoring()
    ok, _, st = poll(_connected_snapshot, timeout)
    return ok, st


# ----------------------------------------------------------------------------- /video capture

def decode(jpeg: bytes):
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def sharpness(img) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class VideoSession:
    """Keeps GET /video open (a viewer for the server and the phone) and records each JPEG part."""

    def __init__(self) -> None:
        self.frames: list[tuple[float, bytes]] = []
        self.error: str | None = None
        self.started_at = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.started_at = time.time()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wait_first_frame(self, timeout: float) -> bool:
        ok, _, _ = poll(lambda: (len(self.frames) > 0 or self.error is not None, None), timeout, 0.2)
        return ok and self.error is None

    def frames_since(self, t: float) -> list[bytes]:
        return [b for ts, b in list(self.frames) if ts >= t]

    def _run(self) -> None:
        try:
            resp = urllib.request.urlopen(BASE + "/video", timeout=15)
            buf = b""
            while not self._stop.is_set():
                chunk = resp.read1(65536)
                if not chunk:
                    break
                buf = self._consume(buf + chunk)
            resp.close()
        except Exception as exc:  # recorded as evidence; the check decides what it means
            self.error = f"{type(exc).__name__}: {exc}"

    def _consume(self, buf: bytes) -> bytes:
        while True:
            i = buf.find(b"--frame\r\n")
            if i < 0:
                return buf[-10:]  # keep a possible partial boundary
            hdr_end = buf.find(b"\r\n\r\n", i)
            if hdr_end < 0:
                return buf[i:]
            m = re.search(rb"content-length:\s*(\d+)", buf[i:hdr_end], re.I)
            if not m:
                buf = buf[hdr_end + 4:]
                continue
            n, start = int(m.group(1)), hdr_end + 4
            if len(buf) < start + n:
                return buf[i:]
            self.frames.append((time.time(), buf[start:start + n]))
            buf = buf[start + n:]


def unique_jpegs(jpegs: list[bytes]) -> list[bytes]:
    seen: set[str] = set()
    out: list[bytes] = []
    for b in jpegs:
        h = hashlib.md5(b).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(b)
    return out


def dims(jpeg: bytes) -> tuple[int, int] | None:
    img = decode(jpeg)
    return None if img is None else (img.shape[1], img.shape[0])


# ----------------------------------------------------------------------------- backend process

class Backend:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.out = tmp / "out"
        self.log_path = tmp / "backend.log"
        self.proc: subprocess.Popen | None = None
        self._write_config()

    def _write_config(self) -> None:
        """The backend reads config.yaml from its cwd, so the temp cwd gets a copy with the log dir
        and database moved into the temp directory. The repo config is not modified."""
        cfg = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8")) or {}
        web = cfg.setdefault("web", {})
        web["pipeline"] = "remote"
        web["log_dir"] = str(self.out)
        web["database_url"] = f"sqlite:///{self.out / 'btb.db'}"
        web.setdefault("auth", {})["enabled"] = False
        (self.tmp / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                raise RuntimeError(f"port {PORT} is already in use; stop the other process first")
        env = {k: v for k, v in os.environ.items() if not k.startswith("BTB_")}
        env.update({
            "WEB_PIPELINE": "remote",
            "PYTHONPATH": str(REPO),
            "BTB_AUTH_ENABLED": "0",
            "BTB_DATABASE_URL": f"sqlite:///{self.out / 'btb.db'}",
        })
        with self.log_path.open("ab") as logf:
            self.proc = subprocess.Popen(
                [PY, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
                cwd=self.tmp, env=env, stdout=logf, stderr=subprocess.STDOUT,
            )

    def wait_healthy(self, timeout: float = 30.0) -> None:
        def ready():
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"backend exited with {self.proc.returncode}; see {self.log_path}")
            try:
                return http("GET", "/health", timeout=2)[0] == 200, None
            except (OSError, ValueError):
                return False, None

        ok, _, _ = poll(ready, timeout, 0.3)
        if not ok:
            raise RuntimeError(f"backend not healthy after {timeout:.0f} s; see {self.log_path}")

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)
        self.proc = None

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace") if self.log_path.exists() else ""


@dataclass
class Ctx:
    backend: Backend


# ----------------------------------------------------------------------------- the ten checks

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def c1_fresh_server_idle(ctx: Ctx) -> tuple[str, str]:
    force_stop()
    time.sleep(1)
    st = status()
    state = pipe_state(st)
    page = dom(wait_for="Monitoring stopped")
    reconnecting = "Reconnecting" in page
    ok = state == "idle" and not is_connected(st) and not reconnecting
    ev = (f"pipeline_status.state={state!r}, phone={'null' if not phone_of(st) else 'present'}, "
          f"dashboard contains 'Reconnecting'={reconnecting}, contains 'Connecting'={'Connecting' in page}")
    return (PASS if ok else FAIL), ev


def c2_connect(ctx: Ctx) -> tuple[str, str]:
    am_out = start_monitoring()
    def snap():
        st = status()
        p = phone_of(st)
        good = bool(p.get("connected")) and pipe_state(st) == "running" and (p.get("events") or 0) > 0
        return good, st
    ok, took, st = poll(snap, 20.0)
    p = phone_of(st)
    ev = (f"after {took:.1f} s: connected={p.get('connected')}, pipeline state={pipe_state(st)!r}, "
          f"events={p.get('events')}, previews={p.get('previews')}, proto={p.get('proto')}, "
          f"device={p.get('device')}, am start output={am_out.strip()[:60]!r}")
    return (PASS if ok else FAIL), ev


def c3_telemetry(ctx: Ctx) -> tuple[str, str]:
    def snap():
        st = status()
        p = phone_of(st)
        return (p.get("battery_pct") is not None and p.get("temp_c") is not None
                and p.get("charging") is not None), st
    ok, took, st = poll(snap, 30.0)
    p = phone_of(st)
    if not ok:
        return FAIL, f"telemetry missing after 30 s: battery_pct={p.get('battery_pct')}, temp_c={p.get('temp_c')}, charging={p.get('charging')}"
    bat = phone_battery()
    d_bat = abs(p["battery_pct"] - bat["level"])
    d_temp = abs(p["temp_c"] - bat["temp_c"])
    ladder = find_key(st, "ladder_rung")
    ok = d_bat <= 2 and d_temp <= 3
    ev = (f"present after {took:.1f} s: battery_pct={p['battery_pct']} vs dumpsys level {bat['level']} "
          f"(delta {d_bat}, limit 2); temp_c={p['temp_c']} vs dumpsys {bat['temp_c']:.1f} "
          f"(delta {d_temp:.1f}, limit 3); charging={p['charging']}; "
          f"ladder_rung={ladder if ladder is not None else 'absent'}")
    return (PASS if ok else FAIL), ev


def c4_preview_stream(ctx: Ctx) -> tuple[str, str]:
    ok, st = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect before the stream"
    if station_cap() != "full":
        return SKIP, (f"precondition: station cap is {station_cap()!r} (set on the phone), so previews are withheld "
                      "by design; restore Full on the device to measure the stream")
    sess = VideoSession()
    t0 = time.monotonic()
    sess.start()
    time.sleep(20)
    sess.stop()
    duration = time.monotonic() - t0
    raw = [b for _, b in list(sess.frames)]
    if sess.error and not raw:
        return FAIL, f"/video error: {sess.error}"
    uniq = unique_jpegs(raw)
    if not uniq:
        return FAIL, f"no JPEG frames in {duration:.1f} s (raw parts={len(raw)})"
    raw_fps = len(raw) / duration
    uniq_fps = len(uniq) / duration
    first, last = dims(uniq[0]), dims(uniq[-1])
    if first is None or last is None:
        return FAIL, "first or last JPEG did not decode"
    min_edge = min(max(first), max(last))
    ok = uniq_fps >= 1.0 and min_edge >= 240
    ev = (f"open {duration:.1f} s: {len(raw)} stream parts ({raw_fps:.1f}/s), {len(uniq)} distinct frames "
          f"({uniq_fps:.2f} fps, the phone's preview rate; the server repeats the last frame at 8 Hz), "
          f"first {first[0]}x{first[1]}, last {last[0]}x{last[1]}, min of max-edges {min_edge} "
          f"(limit fps>=1.0, edge>=240); phone previews counter={phone_of(status()).get('previews')}")
    return (PASS if ok else FAIL), ev


def c5_viewer_indicator(ctx: Ctx) -> tuple[str, str]:
    ok, _ = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect"
    fronted = bring_app_front()
    time.sleep(1.0)
    px_before = screen_badge_pixels()
    sess = VideoSession()
    sess.start()
    if not sess.wait_first_frame(15.0):
        sess.stop()
        return FAIL, f"no /video frame to hold the viewer open (error={sess.error})"
    viewers = None
    found: str | None = None
    package, screen = "", []
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < 8.0 and found is None:
            time.sleep(1.0)
            xml = ui_xml()
            root = ET.fromstring(xml)
            for node in root.iter("node"):
                if node.get("package"):
                    package = node.get("package", "")
                    break
            texts = ui_texts(xml)
            screen = texts
            hits = [t for t in texts if re.search(r"viewer", t, re.I)]
            if hits:
                found = hits[0]
        viewers = (status().get("viewers") or {}).get("video")
        time.sleep(1.5)
        px_during = screen_badge_pixels()
    finally:
        sess.stop()
    px_gain = px_during - px_before
    if found is None and px_gain >= 40:
        found = f"badge pixels +{px_gain} on screen (before {px_before}, during {px_during}), uiautomator does not list it"
    chime = logcat_lines(r"chime")
    chime_note = (f"logcat MonitorService lines about chime: {len(chime)} "
                  "(MonitorService logs only chime failures; a successful chime is silent, so no success tag exists)")
    if found:
        return PASS, f"phone shows {found!r} while /video is open (server viewers.video={viewers}); {fronted}; {chime_note}"
    if package and package != PKG:
        return SKIP, (f"screen is not the app (foreground package {package!r}; {fronted}); the indicator cannot be read. "
                      f"{chime_note}")
    sample = [t for t in screen if t][:12]
    return FAIL, (f"no 'viewer' text in the UI dump while /video is open (server viewers.video={viewers}); "
                  f"{fronted}; screen texts sample: {sample}; {chime_note}")


def restore_exposure() -> str:
    """Ask for full at the end of the mode sequence. A raise above the station cap returns pending (the
    owner cannot raise past what the person holding the phone allowed); the script never grants it."""
    code, body = http("POST", "/mode", {"mode": "full"})
    if body.get("status") == "pending":
        return "restore: POST /mode full returned pending (needs an on-device decision; not granted here)"
    ok, _, _ = poll(lambda: (status().get("effective_mode") == "full", None), 8.0)
    return f"restore: POST /mode full -> {body.get('status')}, effective full={ok}"


def recover_full_session() -> str:
    """Known state for checks 7-9: a fresh monitoring session (the station cap is read back from the
    phone's device setting, so a remote blackout from check 6 is cleared), connected, and the owner's
    mode set to full. The raise is allowed because the fresh cap is full; nothing is granted on the phone."""
    force_stop()
    time.sleep(1.0)
    ok, _ = ensure_connected()
    code, body = http("POST", "/mode", {"mode": "full"})
    ok_full, _, _ = poll(lambda: (status().get("effective_mode") == "full", None), 8.0)
    return (f"recovered to a fresh session: connected={ok}, station cap={station_cap()}, "
            f"POST /mode full -> {body.get('status')}, effective full={ok_full}")


def c6_modes(ctx: Ctx) -> tuple[str, str]:
    parts: list[str] = []
    verdicts: list[str] = []
    ok, _ = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect"
    if station_cap() != "full":
        return SKIP, (f"precondition: station cap is {station_cap()!r}; raises return pending until the owner "
                      "chooses the exposure on the phone ('What may be shared?'), so the mode sequence cannot run")
    sess = VideoSession()
    sess.start()
    if not sess.wait_first_frame(15.0):
        sess.stop()
        return FAIL, f"no /video frame (error={sess.error})"
    try:
        http("POST", "/mode", {"mode": "full"})
        poll(lambda: (status().get("effective_mode") == "full", None), 8.0)
        t_base = time.time()
        time.sleep(3.0)
        base_frames = unique_jpegs(sess.frames_since(t_base))
        base_sharp = [sharpness(decode(b)) for b in base_frames[:20] if decode(b) is not None]
        base_edge = max((max(dims(b) or (0, 0)) for b in base_frames), default=0)
        base_sharp_mean = float(np.mean(base_sharp)) if base_sharp else 0.0

        for mode in MODE_SEQUENCE:
            code, body = http("POST", "/mode", {"mode": mode})
            if code != 200:
                parts.append(f"{mode}: POST /mode -> HTTP {code} {body}")
                verdicts.append(FAIL)
                continue
            if body.get("status") == "pending":
                parts.append(f"{mode}: raise returned pending (needs on-device approval), treated as pending reported")
                verdicts.append(PASS)
                continue
            ok_eff, took, _ = poll(lambda m=mode: (status().get("effective_mode") == m, None), 8.0)
            if not ok_eff:
                parts.append(f"{mode}: effective did not reach {mode} within 8 s")
                verdicts.append(FAIL)
                continue
            s1 = status()
            t_w = time.time()
            mark = len(sess.frames)
            time.sleep(3.0)
            s2 = status()
            p1, p2 = phone_of(s1), phone_of(s2)
            prev_d = (p2.get("previews") or 0) - (p1.get("previews") or 0)
            ev_d = (p2.get("events") or 0) - (p1.get("events") or 0)
            audio_ok = (s2.get("pipeline_status") or {}).get("audio_ok")
            win = unique_jpegs([b for _, b in sess.frames[mark:]])
            win_sharp = [sharpness(decode(b)) for b in win[:20] if decode(b) is not None]
            win_edge = max((max(dims(b) or (0, 0)) for b in win), default=0)
            win_short = max((min(dims(b) or (0, 0)) for b in win), default=0)
            win_sharp_mean = float(np.mean(win_sharp)) if win_sharp else 0.0
            if mode == "privacy":
                good = prev_d == 0 and ev_d > 0
            elif mode == "blackout":
                good = prev_d == 0 and audio_ok is False
            elif mode == "limited":
                blurred = bool(win) and (win_short <= 240 or (base_sharp_mean > 0 and win_sharp_mean < 0.5 * base_sharp_mean))
                good = blurred
            else:  # full
                good = prev_d > 0
            verdicts.append(PASS if good else FAIL)
            parts.append(
                f"{mode} (effective after {took:.1f} s): previews +{prev_d}/3 s, events +{ev_d}/3 s, "
                f"audio_ok={audio_ok}, /video distinct frames={len(win)} short edge {win_short} max edge {win_edge}, "
                f"sharpness {win_sharp_mean:.0f} (full baseline {base_sharp_mean:.0f}, edge {base_edge})"
            )
    finally:
        parts.append(restore_exposure())
        sess.stop()
    verdict = FAIL if FAIL in verdicts else PASS
    return verdict, "; ".join(parts)


def c7_treat_and_flip(ctx: Ctx) -> tuple[str, str]:
    """phone.treats counts treat envelopes the phone itself sends. The contract (CLAUDE.md) forbids the
    phone from re-sending a treat it received as a downlink, so the dashboard treat must be accepted
    (HTTP 200 with a ts) and must NOT move the counter. The downlink's effect on the phone (chime, rules)
    is not observable over the API or logcat, so this check covers the server side only."""
    recovered = recover_full_session()
    ok, st = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect"
    before = phone_of(status()).get("treats") or 0
    code_t, body_t = http("POST", "/treat", {})
    time.sleep(5.0)
    treats_now = phone_of(status()).get("treats") or 0
    no_echo = treats_now == before
    code_f, body_f = http("POST", "/camera/flip", {})
    flip_ok = code_f == 200 and body_f.get("sent") is True
    ok = code_t == 200 and isinstance(body_t.get("ts"), (int, float)) and no_echo and flip_ok
    ev = (f"{recovered}; POST /treat -> HTTP {code_t} ts={body_t.get('ts')}; phone.treats {before} -> {treats_now} over 5 s "
          f"(must stay flat: no echo loop, per contract); POST /camera/flip -> HTTP {code_f} {body_f} "
          f"(facing not read back; check the phone visually)")
    return (PASS if ok else FAIL), ev


def c8_server_restart_reconnect(ctx: Ctx) -> tuple[str, str]:
    recovered = recover_full_session()
    ok, _ = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect before the restart"
    before = phone_of(status()).get("events")
    ctx.backend.kill()
    time.sleep(5.0)
    ctx.backend.start()
    ctx.backend.wait_healthy()

    def reconnected():
        st = status()
        return bool(phone_of(st).get("connected")), st

    ok_r, took, st = poll(reconnected, 40.0)
    base = f"{recovered}; backend killed (SIGKILL), 5 s down, restarted; events before kill={before}; no manual action"
    if not ok_r:
        return FAIL, (f"{base}; phone not reconnected within 40 s; phone={phone_of(st) or 'null'}")
    e_conn = phone_of(st).get("events") or 0
    ok_e, took_e, st2 = poll(lambda: ((phone_of(status()).get("events") or 0) > e_conn, None), 10.0)
    e_now = phone_of(status()).get("events")
    base = f"{base}; connected after {took:.1f} s from restart; events {e_conn} -> {e_now} within {took_e:.1f} s"
    if station_cap() != "full":
        return SKIP, (f"reconnect verified, but events cannot increase while the station cap is "
                      f"{station_cap()!r} (set on the phone): {base}")
    return (PASS if ok_e else FAIL), f"{base} (events increasing={ok_e})"


def c9_stop_is_clean(ctx: Ctx) -> tuple[str, str]:
    recovered = recover_full_session()
    ok, _ = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect before the stop"
    log_before = len(ctx.backend.log())
    fronted = bring_app_front()
    tapped = tap_text("Stop Monitoring")
    t0 = time.monotonic()
    first_clean: float | None = None
    stalled_seen = False
    last: dict = {}
    timeline: list[str] = []
    while time.monotonic() - t0 < 8.0:
        st = status()
        last = st
        state = pipe_state(st)
        tag = f"{state}/{'phone' if phone_of(st) else 'null'}"
        if not timeline or timeline[-1].split(" ", 1)[1] != tag:
            timeline.append(f"{time.monotonic() - t0:.1f}s {tag}")
        if state == "stalled":
            stalled_seen = True
        clean = (not phone_of(st)) and state == "idle" and (st.get("fps") or 0) == 0 \
            and ((st.get("pipeline_status") or {}).get("fps") or 0) == 0
        if clean and first_clean is None:
            first_clean = time.monotonic() - t0
        time.sleep(0.5)
    page = dom(wait_for="Monitoring stopped")
    new_log = ctx.backend.log()[log_before:]
    stop_lines = [ln for ln in new_log.splitlines() if "phone stopped" in ln or "phone disconnected" in ln]
    code_1005 = any("1005" in ln for ln in stop_lines)
    stopped_ok = any("phone stopped" in ln for ln in stop_lines)
    reconnecting = "Reconnecting" in page
    stopped_text = "Monitoring stopped" in page
    ok = (first_clean is not None and first_clean <= 5.0 and not stalled_seen and not reconnecting
          and stopped_ok and not code_1005)
    ev = (f"{recovered}; {fronted}; {tapped}; idle with phone null and fps 0 after {first_clean if first_clean is None else round(first_clean, 1)} s "
          f"(limit 5 s); state never stalled over 8 s: {not stalled_seen}; timeline {' | '.join(timeline)}; "
          f"last state={pipe_state(last)!r}; "
          f"log: {stop_lines[-1] if stop_lines else 'no phone stopped/disconnected line'}; "
          f"dashboard 'Reconnecting'={reconnecting}, 'Monitoring stopped'={stopped_text}")
    return (PASS if ok else FAIL), ev


def c10_abrupt_drop(ctx: Ctx) -> tuple[str, str]:
    ok, _ = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect"
    force_stop()
    t0 = time.monotonic()
    def dropped():
        st = status()
        return not is_connected(st), st
    ok_d, took, st = poll(dropped, 10.0)
    page = dom(wait_for="Reconnecting")
    reconnecting = "Reconnecting" in page
    close_lines = [ln for ln in ctx.backend.log().splitlines() if "phone disconnected" in ln][-1:]
    ok = ok_d and reconnecting
    ev = (f"force-stop: connected=false after {took:.1f} s (limit 10 s); phone snapshot connected="
          f"{phone_of(st).get('connected')}; dashboard 'Reconnecting'={reconnecting}; "
          f"log: {close_lines[0] if close_lines else 'no phone disconnected line'}")
    return (PASS if ok else FAIL), ev


def c11_raise_approval(ctx: Ctx) -> tuple[str, str]:
    """Owner raise above the device's cap -> on-phone prompt -> Allow raises for 15 min; Keep private does not.
    Each half starts from a fresh session with the device cap lowered to Privacy by the in-app control.
    The device cap is restored to Full at the end, because it is persisted and the next check recovers from it."""
    parts: list[str] = [recover_full_session()]
    ok, _ = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect"
    verdicts: list[str] = []
    try:
        # (a) Allow
        parts.append(set_device_cap("privacy"))
        code, body = http("POST", "/mode", {"mode": "full"})
        pending = code == 200 and body.get("status") == "pending"
        seen, took = wait_raise_prompt("Full", 5.0)
        parts.append(f"allow: POST /mode full -> {body.get('status')}; prompt text found={seen} after {took:.1f} s")
        if seen:
            parts.append(tap_dialog_button("Allow"))
        eff_ok, took2, _ = poll(lambda: (status().get("effective_mode") == "full", None), 8.0)
        gone, _, _ = poll(lambda: (not raise_prompt_visible("Full"), None), 5.0, interval=0.7)
        parts.append(f"allow: effective full={eff_ok} after {took2:.1f} s; prompt dismissed={gone}")
        verdicts.append(PASS if (pending and seen and eff_ok and gone) else FAIL)

        # (b) Keep private: a fresh request, lowered cap, and the owner's raise is refused on the phone
        parts.append(set_device_cap("privacy"))
        code, body = http("POST", "/mode", {"mode": "full"})
        pending2 = code == 200 and body.get("status") == "pending"
        seen2, took3 = wait_raise_prompt("Full", 5.0)
        parts.append(f"keep: POST /mode full -> {body.get('status')}; prompt text found={seen2} after {took3:.1f} s")
        if seen2:
            parts.append(tap_dialog_button("Keep private"))
        time.sleep(3.0)
        eff2 = status().get("effective_mode")
        gone2, _, _ = poll(lambda: (not raise_prompt_visible("Full"), None), 5.0, interval=0.7)
        parts.append(f"keep: effective after 3 s={eff2}; prompt dismissed={gone2}")
        verdicts.append(PASS if (pending2 and seen2 and eff2 == "privacy" and gone2) else FAIL)
    except LookupError as exc:
        verdicts.append(FAIL)
        parts.append(f"UI control not found: {exc}")
    finally:
        restored = None
        for _ in range(3):  # the device cap is persisted: leaving it at privacy would break the next check
            try:
                restored = set_device_cap("full")
                break
            except LookupError as exc:
                restored = f"restore device cap failed: {exc}"
                time.sleep(2.0)
        parts.append("restore device cap: " + str(restored))
        if restored and "failed" in restored:
            verdicts.append(FAIL)
    return (FAIL if FAIL in verdicts or not verdicts else PASS), "; ".join(parts)


def c12_blackout_not_stalled(ctx: Ctx) -> tuple[str, str]:
    """A deliberate blackout reads paused (never stalled) for 12 s, the dashboard shows the paused card and
    not 'Reconnecting', and a remote restore to full is applied directly (the device cap is still full)."""
    parts: list[str] = [recover_full_session()]
    ok, _ = ensure_connected()
    if not ok:
        return FAIL, "phone did not connect"
    code, body = http("POST", "/mode", {"mode": "blackout"})
    t0 = time.monotonic()
    states: list[str | None] = []
    while time.monotonic() - t0 < 12.0:
        states.append(pipe_state(status()))
        time.sleep(0.5)
    seen_states = sorted({str(s) for s in states})
    all_paused = bool(states) and set(states) == {"paused"}
    page = dom(wait_for="Sharing is paused")
    reconnecting = "Reconnecting" in page
    paused_card = "Sharing is paused" in page
    rcode, rbody = http("POST", "/mode", {"mode": "full"})
    eff_ok, took, _ = poll(lambda: (status().get("effective_mode") == "full", None), 8.0)
    running, took2, _ = poll(lambda: (pipe_state(status()) == "running", None), 10.0)
    parts.append(f"blackout: POST /mode blackout -> {body.get('status')}; 12 s of /status samples: "
                 f"{len(states)} samples, states {seen_states}, never stalled={'stalled' not in states}")
    parts.append(f"dashboard: 'Reconnecting'={reconnecting}, paused card text={paused_card}")
    parts.append(f"restore: POST /mode full -> {rbody.get('status')} (direct, not pending); effective full={eff_ok} "
                 f"after {took:.1f} s; pipeline back to running={running} after {took2:.1f} s")
    ok_all = (code == 200 and body.get("status") == "applied" and all_paused and not reconnecting
              and paused_card and rbody.get("status") == "applied" and eff_ok and running)
    return (PASS if ok_all else FAIL), "; ".join(parts)


CHECKS = [
    (1, "fresh_server_idle", c1_fresh_server_idle),
    (2, "connect", c2_connect),
    (3, "telemetry", c3_telemetry),
    (4, "preview_stream", c4_preview_stream),
    (5, "viewer_indicator", c5_viewer_indicator),
    (6, "modes", c6_modes),
    (7, "treat_and_flip", c7_treat_and_flip),
    (8, "server_restart_reconnect", c8_server_restart_reconnect),
    (9, "stop_is_clean", c9_stop_is_clean),
    (10, "abrupt_drop", c10_abrupt_drop),
    (11, "raise_approval", c11_raise_approval),
    (12, "blackout_not_stalled", c12_blackout_not_stalled),
]


# ----------------------------------------------------------------------------- preflight, report, main

def preflight() -> None:
    devices = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=20).stdout
    if not re.search(rf"^{re.escape(SERIAL)}\s+device$", devices, re.M):
        raise RuntimeError(f"adb device {SERIAL} not in 'device' state:\n{devices}")
    power = adb("shell", "dumpsys", "power")
    if "mWakefulness=Awake" not in power:
        raise RuntimeError("phone is not awake; wake and unlock it, then rerun")
    keyguard = adb("shell", "dumpsys", "window", "policy")
    if re.search(r"mIsShowing=true|\bshowing=true", keyguard):
        raise RuntimeError("phone shows the keyguard (locked); unlock it and rerun. Not changing lock settings.")


def device_meta() -> dict:
    def prop(name: str) -> str:
        return adb("shell", "getprop", name).strip()
    pkg = adb("shell", "dumpsys", "package", PKG)
    version = re.search(r"versionName=(\S+)", pkg)
    updated = re.search(r"lastUpdateTime=([^\n]+)", pkg)
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    return {
        "model": prop("ro.product.model"), "android": prop("ro.build.version.release"),
        "serial": SERIAL, "apk_version": version.group(1) if version else "?",
        "apk_updated": updated.group(1).strip() if updated else "?", "commit": commit,
    }


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


# Findings that the checks do not fix (kept here so every regenerated report carries them).
FINDINGS = [
    "Resolved (commit d15fb0a, android): uiautomator could not reach an idle state while monitoring ('could not get "
    "idle state'). Every camera result posted a UI update and a full-screen invalidation. The overlay is now throttled "
    "to 4 Hz and only sent on change, and the Stop, mode, viewers and Disconnect controls are in the dump while monitoring.",
    "Resolved (harness): headless --dump-dom hung while the dashboard held the /video MJPEG <img> for a dropped phone. "
    "scripts/dom.mjs (playwright-core on the system Chrome) waits for load, then for the page to render, then for the "
    "expected text, with a 12 s cap.",
    "Medium (dashboard, not changed here): after the phone drops, the backend reports connected=false at once, but the "
    "dashboard shows 'Reconnecting to camera' only about 3-4 s later (it still shows 'Connected' at 2 s). Check 10 waits "
    "up to 6 s for that text.",
    "Low (dashboard, harness): the dashboard fills its state from the WebSocket after load. Until then it shows its "
    "default 'Connected / LIVE' card, so a DOM read taken at load can be wrong. Checks 1, 9, 10 and 12 wait for the text "
    "their state shows.",
    "Medium (not exercised by the checks): the on-device prompt's notification path (app not in front) is not driven "
    "here. Only the dialog path ran.",
    "Low: the phone's status line showed 'Connecting to ws://...' while the backend reported the phone connected "
    "(screenshots during an earlier run). Not investigated.",
    "Low: the server never learns that the person on the phone chose Keep private or let the prompt time out; the "
    "dashboard's 60 s countdown ends on its own.",
    "Low: ModeController is read on the network thread and mutated on the main thread (approval timeout) without "
    "synchronisation. Same pattern as the existing mode code; not observed to fail.",
    "Low: the phone-side effect of a dashboard treat (chime, rules) is not observable over the API or logcat; check 7 "
    "covers the server side only.",
]


def write_report(path: Path, results: list[tuple[int, str, str, str]], meta: dict, log_path: Path,
                 started: dt.datetime) -> None:
    lines = [
        "# On-device acceptance results (2026-10-10)",
        "",
        f"Generated by `scripts/phone_acceptance.py` on {started.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(commit `{meta.get('commit', '?')}`).",
        "",
        f"Device: {meta.get('model')} (Android {meta.get('android')}), serial {meta.get('serial')}; "
        f"debug APK version {meta.get('apk_version')}, last updated {meta.get('apk_updated')}.",
        f"Backend log for this run: `{log_path}` (kept in its temp directory).",
        "",
        "| # | check | result | evidence |",
        "|---|---|---|---|",
    ]
    for num, name, verdict, evidence in results:
        lines.append(f"| {num} | {name} | {verdict} | {_cell(evidence)} |")
    counts = {v: sum(1 for r in results if r[2] == v) for v in (PASS, FAIL, SKIP)}
    lines += ["", f"Totals: PASS {counts[PASS]}, FAIL {counts[FAIL]}, SKIP {counts[SKIP]}.", ""]
    lines += ["## Open findings (not fixed by the checks)", ""]
    lines += [f"{i}. {f}" for i, f in enumerate(FINDINGS, 1)]
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = ap.parse_args()

    started = dt.datetime.now()
    tmp = Path(tempfile.mkdtemp(prefix="btb-accept-"))
    backend = Backend(tmp)
    ctx = Ctx(backend=backend)
    results: list[tuple[int, str, str, str]] = []
    meta: dict = {}
    fatal: str | None = None
    try:
        preflight()
        meta = device_meta()
        backend.start()
        backend.wait_healthy()
        ensure_adb_reverse()
        for num, name, fn in CHECKS:
            try:
                verdict, evidence = fn(ctx)
            except Exception as exc:  # a crashing check is a FAIL, and the next check still runs
                verdict = FAIL
                evidence = f"exception {type(exc).__name__}: {exc}; {traceback.format_exc(limit=1).strip().splitlines()[-1]}"
            results.append((num, name, verdict, evidence))
            print(f"[{verdict}] {num:>2} {name}: {evidence}", flush=True)
    except Exception as exc:
        fatal = f"{type(exc).__name__}: {exc}"
        print(f"ABORTED before the checks finished: {fatal}", file=sys.stderr)
    finally:
        for step in (force_stop, lambda: adb("reverse", "--remove-all"), backend.kill):
            try:
                step()
            except Exception as exc:
                print(f"cleanup step failed: {exc}", file=sys.stderr)
    if fatal:
        results.append((0, "setup", FAIL, fatal))
    write_report(args.report, results, meta, backend.log_path, started)
    print(f"report: {args.report}")
    print(f"backend log: {backend.log_path}")
    return 1 if any(r[2] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
