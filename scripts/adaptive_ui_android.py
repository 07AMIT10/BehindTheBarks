#!/usr/bin/env python3
"""Adaptive-layout matrix for the Android app on the USB phone (SM-E075F, serial R9ZY80PKWTW).

For each display profile x font scale (and landscape for the native and tablet-like profiles) it drives
the real app against a local backend and captures four screens:

  a  idle main screen                  (not monitoring)
  b  monitoring screen                 (connected to the local backend)
  c  mode control: the "What may be shared?" chooser opened from the in-app mode button
  d  on-phone approval dialog          (device cap lowered in-app, POST /mode full, prompt appears)

Per cell it saves a compressed JPEG under docs/screenshots/adaptive-android/, keeps the uiautomator dump
in /tmp, and asserts:
  * every clickable's bounds lie inside the screen (a clickable below the fold of a scrollable container
    counts as reachable by scrolling and is listed as a note instead);
  * every clickable is at least 44dp in both dimensions (a fail below 44dp, a warning below the 48dp target);
  * no two clickables overlap (ancestor/descendant pairs are not overlap);
  * content that does not fit is reachable: a clickable outside the screen needs a scrollable ancestor;
  * the status line matches the backend (Live when the backend reports the phone connected and running,
    Idle when the phone is not monitoring).
Text clipping cannot be read from a uiautomator dump (it always carries the full string), so it is judged
from the screenshots and recorded in the report by hand.

Rotation: after the portrait cells of a landscape-capable profile, the device is rotated to landscape while
monitoring. The run then checks that the backend still reports the phone connected and running with events
increasing, and that the UI still says Stop Monitoring (the activity is recreated by the rotation).

The Station Dim round trip is checked once (native, font 1.0, portrait): the dim overlay hides the controls,
a tap wakes the screen, and the controls come back.

Display settings: `wm size`, `wm density`, `font_scale`, `accelerometer_rotation` and `user_rotation` are
changed for the matrix. The originals are recorded first in /tmp/btb-display-orig.env and restored in a
finally block; scripts/adaptive_ui_reset.sh restores them standalone. Nothing else is touched.

Results go to docs/screenshots/adaptive-android/results.json (one entry per cell, so a re-run with --only
replaces just those cells), and the report docs/ADAPTIVE_UI_ANDROID_2026-10-11.md is rendered from it.

  .venv/bin/python scripts/adaptive_ui_android.py                       # full matrix (about 20 min)
  .venv/bin/python scripts/adaptive_ui_android.py --only native:1.5:land   # re-run one configuration
  .venv/bin/python scripts/adaptive_ui_android.py --report-only           # re-render the report
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import phone_acceptance as pa  # noqa: E402  (adb, uiautomator, backend and tap helpers shared with the acceptance run)

SHOT_DIR = REPO / "docs" / "screenshots" / "adaptive-android"
RESULTS = SHOT_DIR / "results.json"
REPORT = REPO / "docs" / "ADAPTIVE_UI_ANDROID_2026-10-11.md"
DUMP_DIR = Path("/tmp/btb-adaptive-dumps")
ORIG_ENV = Path("/tmp/btb-display-orig.env")
MAX_TOTAL_BYTES = 6 * 1024 * 1024

# name -> (physical size in px, density in dpi)
PROFILES = {
    "small": ((480, 854), 240),
    "native": ((720, 1600), 300),
    "large": ((1080, 2400), 420),
    "tablet": ((1200, 1920), 240),
}
FONT_SCALES = ["0.85", "1.0", "1.3", "1.5"]
LANDSCAPE_PROFILES = ["native", "tablet"]
# Pruned for the time budget: landscape at the two ends of the font range for both landscape profiles.
LANDSCAPE_FONTS = ["1.0", "1.5"]
MIN_HARD_DP = 44
MIN_TARGET_DP = 48

SCREENS = {"a": "idle", "b": "monitoring", "c": "mode-chooser", "d": "approval"}


# ----------------------------------------------------------------------------- display settings

def adb(*args: str, timeout: float = 30.0) -> str:
    return pa.adb(*args, timeout=timeout)


def record_originals() -> dict:
    """Read the values the matrix will change, before it changes anything, and write them down."""
    size_out = adb("shell", "wm", "size")
    dens_out = adb("shell", "wm", "density")
    orig = {
        "ORIG_SIZE_OVERRIDE": (re.search(r"Override size:\s*(\S+)", size_out) or [None, "none"])[1],
        "ORIG_SIZE_PHYSICAL": (re.search(r"Physical size:\s*(\S+)", size_out) or [None, "?"])[1],
        "ORIG_DENSITY_OVERRIDE": (re.search(r"Override density:\s*(\S+)", dens_out) or [None, "none"])[1],
        "ORIG_DENSITY_PHYSICAL": (re.search(r"Physical density:\s*(\S+)", dens_out) or [None, "?"])[1],
        "FONT_SCALE": adb("shell", "settings", "get", "system", "font_scale").strip() or "1.0",
        "ACCEL_ROTATION": adb("shell", "settings", "get", "system", "accelerometer_rotation").strip() or "1",
        "USER_ROTATION": adb("shell", "settings", "get", "system", "user_rotation").strip() or "0",
    }
    for key in ("FONT_SCALE", "ACCEL_ROTATION", "USER_ROTATION"):
        if orig[key] in ("null", ""):
            orig[key] = {"FONT_SCALE": "1.0", "ACCEL_ROTATION": "1", "USER_ROTATION": "0"}[key]
    ORIG_ENV.write_text("".join(f"{k}={v}\n" for k, v in orig.items()), encoding="utf-8")
    return orig


def set_display(profile: str, font: str, landscape: bool) -> None:
    (w, h), dpi = PROFILES[profile]
    adb("shell", "wm", "size", f"{w}x{h}")
    adb("shell", "wm", "density", str(dpi))
    adb("shell", "settings", "put", "system", "font_scale", font)
    adb("shell", "settings", "put", "system", "accelerometer_rotation", "0")
    adb("shell", "settings", "put", "system", "user_rotation", "1" if landscape else "0")
    time.sleep(3.0)  # the activity is recreated on a size, density or rotation change


def restore_display() -> str:
    res = subprocess.run([str(REPO / "scripts" / "adaptive_ui_reset.sh")], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "BTB_DISPLAY_ORIG_FILE": str(ORIG_ENV)})
    return (res.stdout + res.stderr).strip()


# ----------------------------------------------------------------------------- capture and dump

def capture_png() -> bytes:
    res = subprocess.run(["adb", "-s", pa.SERIAL, "exec-out", "screencap", "-p"], capture_output=True, timeout=30)
    if not res.stdout:
        raise RuntimeError("screencap returned no image")
    return res.stdout


def save_jpeg(png: bytes, name: str) -> Path:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    scale = min(1.0, 960 / max(img.size))
    if scale < 1.0:
        img = img.resize((round(img.size[0] * scale), round(img.size[1] * scale)), Image.LANCZOS)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{name}.jpg"
    img.save(path, "JPEG", quality=60, optimize=True)
    return path


@dataclass
class Node:
    cls: str
    text: str
    desc: str
    rid: str
    clickable: bool
    scrollable: bool
    bounds: tuple[int, int, int, int]
    path: tuple[int, ...]

    @property
    def label(self) -> str:
        return self.text or self.desc or self.rid or self.cls.rsplit(".", 1)[-1]


def parse_nodes(xml: str) -> list[Node]:
    out: list[Node] = []

    def walk(el: ET.Element, path: tuple[int, ...]) -> None:
        if el.tag == "node":
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.get("bounds", ""))
            if m:
                out.append(Node(
                    cls=el.get("class", ""), text=el.get("text", ""), desc=el.get("content-desc", ""),
                    rid=el.get("resource-id", ""), clickable=el.get("clickable") == "true",
                    scrollable=el.get("scrollable") == "true", bounds=tuple(map(int, m.groups())), path=path,
                ))
        for i, child in enumerate(list(el)):
            walk(child, path + (i,))

    walk(ET.fromstring(xml), ())
    return out


def is_ancestor(a: Node, b: Node) -> bool:
    return len(a.path) < len(b.path) and b.path[: len(a.path)] == a.path


def dump(tag: str) -> tuple[str, list[Node]]:
    xml = pa.ui_xml()
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_DIR / f"{tag}.xml").write_text(xml, encoding="utf-8")
    return xml, parse_nodes(xml)


def status_line(nodes: list[Node]) -> str | None:
    for n in nodes:
        if re.match(r"^[●○⚠] ", n.text):
            return n.text
    return None


# ----------------------------------------------------------------------------- the assertions

@dataclass
class Verdict:
    offenders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.offenders


def analyze(nodes: list[Node], screen: tuple[int, int], dpi: int, dialog: bool) -> Verdict:
    """Layout assertions for one dump. `dialog` restricts the checks to the on-screen dialog panel (the main
    window underneath a modal dialog is covered and not reachable, so its controls are not judged)."""
    v = Verdict()
    W, H = screen
    px_per_dp = dpi / 160.0
    scope = nodes
    panels = [n for n in nodes if n.rid.endswith("parentPanel")]
    if dialog and panels:
        panel = panels[0]
        scope = [n for n in nodes if n.path[: len(panel.path)] == panel.path]
        x1, y1, x2, y2 = panel.bounds
        if x1 < 0 or y1 < 0 or x2 > W or y2 > H:
            v.offenders.append(f"dialog panel clipped: bounds={panel.bounds} screen={W}x{H}")
    elif dialog:
        v.offenders.append("no dialog panel found in the dump")

    clickables = [
        n for n in scope
        if n.clickable and n.bounds[2] > n.bounds[0] and n.bounds[3] > n.bounds[1]
        and not (n.bounds[0] <= 0 and n.bounds[1] <= 0 and n.bounds[2] >= W and n.bounds[3] >= H)
    ]
    scrollers = [n for n in nodes if n.scrollable]
    for n in clickables:
        x1, y1, x2, y2 = n.bounds
        wdp = (x2 - x1) / px_per_dp
        hdp = (y2 - y1) / px_per_dp
        # A dump reports the visible part of a node. A node cut by the screen edge or by the edge of a scroll
        # container is partly scrolled out of view, so its size cannot be judged from the dump.
        containers = [s for s in scrollers if is_ancestor(s, n)]
        cut = (x1 <= 0 or y1 <= 0 or x2 >= W or y2 >= H) or any(
            abs(y1 - c.bounds[1]) <= 2 or abs(y2 - c.bounds[3]) <= 2 or abs(x1 - c.bounds[0]) <= 2 or abs(x2 - c.bounds[2]) <= 2
            for c in containers)
        if cut:
            v.notes.append(f"'{n.label}' partly scrolled out of view (visible {wdp:.0f}x{hdp:.0f}dp), size not judged")
        elif wdp < MIN_HARD_DP or hdp < MIN_HARD_DP:
            v.offenders.append(f"'{n.label}' is {wdp:.0f}x{hdp:.0f}dp (need >= {MIN_HARD_DP}dp)")
        elif wdp < MIN_TARGET_DP or hdp < MIN_TARGET_DP:
            v.warnings.append(f"'{n.label}' is {wdp:.0f}x{hdp:.0f}dp (target {MIN_TARGET_DP}dp)")
        inside = x1 >= 0 and y1 >= 0 and x2 <= W and y2 <= H
        if not inside:
            container = containers
            if container:
                sx1, sy1, sx2, sy2 = container[-1].bounds
                if y1 < sy2 and x1 >= 0 and x2 <= W:
                    v.notes.append(f"'{n.label}' below the fold, reachable by scrolling")
                else:
                    v.offenders.append(f"'{n.label}' outside the screen and its scroll container: {n.bounds}")
            else:
                v.offenders.append(f"'{n.label}' not fully inside the screen: bounds={n.bounds} screen={W}x{H}")

    for i, a in enumerate(clickables):
        for b in clickables[i + 1:]:
            if is_ancestor(a, b) or is_ancestor(b, a):
                continue
            ax1, ay1, ax2, ay2 = a.bounds
            bx1, by1, bx2, by2 = b.bounds
            if min(ax2, bx2) > max(ax1, bx1) and min(ay2, by2) > max(ay1, by1):
                v.offenders.append(f"'{a.label}' overlaps '{b.label}'")
    return v


def wait_status(expect: str | None, timeout: float) -> tuple[bool, str | None]:
    """Poll fresh dumps until the status line contains `expect` (or is absent when expect is None)."""
    last = None
    t0 = time.monotonic()
    while True:
        _, nodes = dump("status-probe")
        last = status_line(nodes)
        ok = (expect in (last or "")) if expect else last is None
        if ok or time.monotonic() - t0 >= timeout:
            return ok, last
        time.sleep(1.0)


# ----------------------------------------------------------------------------- one cell

@dataclass
class Cell:
    cid: str
    profile: str
    font: str
    orient: str
    screen: str
    ok: bool
    offenders: list[str]
    warnings: list[str]
    notes: list[str]
    shot: str
    detail: str = ""
    run: str = ""  # when and against which commit this cell was (re-)run; empty = the full matrix of 2026-10-11


def run_cell(cfg: str, letter: str, nodes: list[Node], png: bytes, dpi: int, dialog: bool, detail: str = "") -> Cell:
    profile, font, orient = cfg.split(":")
    screen_name = SCREENS[letter]
    cid = f"{profile}:{font}:{orient}:{letter}"
    W, H = Image.open(io.BytesIO(png)).size
    verdict = analyze(nodes, (W, H), dpi, dialog)
    name = f"{profile}-f{font}-{orient}-{letter}-{screen_name}"
    save_jpeg(png, name)
    return Cell(cid, profile, font, orient, screen_name, verdict.ok, verdict.offenders, verdict.warnings,
                verdict.notes, f"{name}.jpg", detail)


def shoot_and_analyse(cfg: str, letter: str, tag: str, dpi: int, dialog: bool, detail: str = "") -> Cell:
    _, nodes = dump(tag)
    png = capture_png()
    return run_cell(cfg, letter, nodes, png, dpi, dialog, detail)


def error_cell(cfg: str, letter: str, message: str) -> Cell:
    profile, font, orient = cfg.split(":")
    return Cell(f"{profile}:{font}:{orient}:{letter}", profile, font, orient, SCREENS[letter], False,
                [f"could not run: {message}"], [], [], "", "")


def tap_if_present(text: str) -> bool:
    _, nodes = dump("stray")
    for n in nodes:
        if n.text.strip() == text and n.clickable:
            x1, y1, x2, y2 = n.bounds
            adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
            return True
    return False


def dismiss_tips() -> None:
    for label in ("Got it!", "Later", "Cancel"):
        tap_if_present(label)
        time.sleep(0.5)


def is_mode_control(n: Node) -> bool:
    return n.cls.endswith("Button") and bool(re.search(r"(Blackout|Privacy|Limited|Full)$", n.text))


def screen_size() -> tuple[int, int]:
    return Image.open(io.BytesIO(capture_png())).size


def swipe_up(W: int, H: int) -> None:
    adb("shell", "input", "swipe", str(W // 2), str(int(H * 0.8)), str(W // 2), str(int(H * 0.35)), "350")


def reach(pred, what: str, max_swipes: int = 8) -> tuple[Node, int]:
    """Find a clickable on screen, scrolling the content when it is below the fold. Controls in a
    scroll container that is taller than the screen are not in the dump until they scroll into view, so
    reaching them by scrolling is part of what the matrix proves. Returns the node and the swipes used."""
    swipes = 0
    while True:
        _, nodes = dump("reach")
        W, H = screen_size()
        for n in nodes:
            x1, y1, x2, y2 = n.bounds
            if n.clickable and pred(n) and x1 >= 0 and y1 >= 0 and x2 <= W and y2 <= H:
                return n, swipes
        if swipes >= max_swipes:
            raise LookupError(f"{what} not reachable on screen after {swipes} swipes")
        swipe_up(W, H)
        swipes += 1
        time.sleep(0.8)


def tap_reached(pred, what: str) -> tuple[str, int]:
    node, swipes = reach(pred, what)
    x1, y1, x2, y2 = node.bounds
    adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
    return f"tapped {what} at {(x1 + x2) // 2},{(y1 + y2) // 2} after {swipes} swipes", swipes


def open_mode_chooser() -> str:
    text, _ = tap_reached(is_mode_control, "in-app mode control")
    return text


def set_cap(mode: str) -> str:
    """The in-app cap control (scrolled into view when needed), then the chooser row for `mode`."""
    row_text = {"privacy": "Barks and behaviour", "full": "Live video"}[mode]
    opened = open_mode_chooser()
    time.sleep(1.2)
    row = pa.tap_node(lambda n: row_text in n.get("text", ""), f"'{mode}' row of the mode chooser")
    ok, took, _ = pa.poll(lambda: (pa.station_cap() == mode, None), 8.0)
    if not ok:
        raise RuntimeError(f"station cap is not {mode} after {took:.1f} s")
    return f"{opened}; {row}"


def run_config(cfg: str, dpi: int, results: list[Cell], log) -> None:
    profile, font, orient = cfg.split(":")
    landscape = orient == "land"
    set_display(profile, font, landscape)
    log(f"[{cfg}] display set")
    pa.force_stop()  # a clean start: the activity is recreated with the new display
    time.sleep(1.0)
    log(f"[{cfg}] {pa.bring_app_front()}")
    dismiss_tips()

    # (a) idle main screen. The phone is not monitoring at the start of a configuration.
    if tap_if_present("Stop Monitoring"):
        time.sleep(2.0)
    ok, line = wait_status("Idle", 8.0)
    results.append(shoot_and_analyse(cfg, "a", f"{profile}-{font}-{orient}-a", dpi, dialog=False,
                                     detail=f"status line {line!r}, expected Idle"))
    if not ok:
        results[-1].offenders.append(f"status line is {line!r}, expected Idle")
        results[-1].ok = False

    # (b) start monitoring, then the monitoring screen.
    pa.start_monitoring()
    connected, _, st = pa.poll(lambda: (pa.is_connected(pa.status()) and pa.pipe_state(pa.status()) == "running",
                                        pa.status()), 25.0, interval=1.0)
    time.sleep(2.0)
    live_ok, line = wait_status("Live", 8.0)
    b = shoot_and_analyse(cfg, "b", f"{profile}-{font}-{orient}-b", dpi, dialog=False,
                          detail=f"backend connected={connected}, status line {line!r}")
    try:
        _, swipes = reach(lambda n: n.text == "Stop Monitoring", "Stop Monitoring")
        b.notes.append(f"Stop Monitoring reachable after {swipes} swipe(s)")
    except LookupError as exc:
        b.offenders.append(str(exc))
        b.ok = False
    if not connected or not live_ok:
        b.offenders.append(f"status line {line!r} does not match the backend (connected={connected}, expect Live)")
        b.ok = False
    results.append(b)

    # (c) the mode control: open the in-app chooser, then cancel it.
    try:
        tapped = open_mode_chooser()
        time.sleep(1.5)
        c = shoot_and_analyse(cfg, "c", f"{profile}-{font}-{orient}-c", dpi, dialog=True, detail=tapped)
        results.append(c)
        tap_if_present("Cancel")
        time.sleep(1.0)
    except LookupError as exc:
        results.append(error_cell(cfg, "c", str(exc)))

    # (d) the on-phone approval dialog: lower the cap in-app, owner asks for full, prompt appears.
    try:
        lowered = set_cap("privacy")
        code, body = pa.http("POST", "/mode", {"mode": "full"})
        seen, took = pa.wait_raise_prompt("Full", 6.0)
        time.sleep(1.0)
        d = shoot_and_analyse(cfg, "d", f"{profile}-{font}-{orient}-d", dpi, dialog=True,
                              detail=f"lowered: {lowered[:80]}; POST /mode full -> {body.get('status')}; prompt seen={seen}")
        if not seen:
            d.offenders.append("approval prompt did not appear")
            d.ok = False
        results.append(d)
        if seen:
            pa.tap_dialog_button("Keep private")
        time.sleep(2.0)
    except (LookupError, RuntimeError, OSError) as exc:
        results.append(error_cell(cfg, "d", str(exc)))
    finally:
        try:
            set_cap("full")
        except (LookupError, RuntimeError) as exc:
            log(f"[{cfg}] restoring the device cap failed: {exc}")

    # Rotation (landscape-capable profiles, portrait configurations only).
    if orient == "port" and profile in LANDSCAPE_PROFILES and font in LANDSCAPE_FONTS:
        rotation_check(cfg, dpi, results, log, profile, font)

    # Stop monitoring at the end of the configuration so the next one starts idle (already stopped after a
    # landscape run, which ends by stopping).
    if tap_if_present("Stop Monitoring"):
        time.sleep(2.0)


def rotation_check(cfg: str, dpi: int, results: list[Cell], log, profile: str, font: str) -> None:
    """Rotate to landscape while monitoring, check that monitoring survived, then run b, c and d in landscape."""
    before = pa.status()
    ev0 = pa.phone_of(before).get("events") or 0
    adb("shell", "settings", "put", "system", "user_rotation", "1")
    time.sleep(5.0)
    after = pa.status()
    ev1 = pa.phone_of(after).get("events") or 0
    still = pa.is_connected(after) and pa.pipe_state(after) == "running" and ev1 > ev0
    try:
        _, swipes = reach(lambda n: n.text == "Stop Monitoring", "Stop Monitoring")
        ui_monitoring = True
    except LookupError:
        swipes, ui_monitoring = -1, False
    rot_ok = still and ui_monitoring
    rot_cell = Cell(f"{profile}:{font}:rotate:r", profile, font, "rotate", "rotation", rot_ok,
                    [] if rot_ok else [f"rotation to landscape stopped monitoring: connected={pa.is_connected(after)} "
                                       f"state={pa.pipe_state(after)!r} events {ev0} -> {ev1} ui_monitoring={ui_monitoring}"],
                    [], [], "", f"events {ev0} -> {ev1}; UI still monitoring={ui_monitoring}; "
                                f"Stop Monitoring reached after {swipes} swipe(s)")
    results.append(rot_cell)
    log(f"[{profile}:{font}] rotation: continuity={still} ui={ui_monitoring}")
    land = f"{profile}:{font}:land"
    # The landscape run uses the same cells; the UI is still monitoring, so the b/c/d steps reuse it.
    _, _ = wait_status("Live", 8.0)
    results.append(shoot_and_analyse(land, "b", f"{profile}-{font}-land-b", dpi, dialog=False, detail="after rotation"))
    try:
        tapped = open_mode_chooser()
        time.sleep(1.5)
        results.append(shoot_and_analyse(land, "c", f"{profile}-{font}-land-c", dpi, dialog=True, detail=tapped))
        tap_if_present("Cancel")
        time.sleep(1.0)
    except LookupError as exc:
        results.append(error_cell(land, "c", str(exc)))
    try:
        set_cap("privacy")
        pa.http("POST", "/mode", {"mode": "full"})
        seen, _ = pa.wait_raise_prompt("Full", 6.0)
        time.sleep(1.0)
        d = shoot_and_analyse(land, "d", f"{profile}-{font}-land-d", dpi, dialog=True,
                              detail=f"prompt seen={seen}")
        if not seen:
            d.offenders.append("approval prompt did not appear")
            d.ok = False
        results.append(d)
        if seen:
            pa.tap_dialog_button("Keep private")
        time.sleep(2.0)
    except (LookupError, RuntimeError, OSError) as exc:
        results.append(error_cell(land, "d", str(exc)))
    finally:
        try:
            set_cap("full")
        except (LookupError, RuntimeError) as exc:
            log(f"[{land}] restoring the device cap failed: {exc}")
    # Landscape idle screen (a): stop in landscape, then capture the idle layout.
    try:
        tap_reached(lambda n: n.text == "Stop Monitoring", "Stop Monitoring")
        time.sleep(2.0)
        ok, line = wait_status("Idle", 8.0)
        a = shoot_and_analyse(land, "a", f"{profile}-{font}-land-a", dpi, dialog=False,
                              detail=f"status line {line!r}, expected Idle")
        if not ok:
            a.offenders.append(f"status line is {line!r}, expected Idle")
            a.ok = False
        results.append(a)
    except LookupError as exc:
        results.append(error_cell(land, "a", str(exc)))
    adb("shell", "settings", "put", "system", "user_rotation", "0")
    time.sleep(3.0)


def station_dim_check(results: list[Cell], dpi: int, log) -> None:
    """Station Dim round trip on native, font 1.0, portrait: the overlay hides the controls, a tap wakes."""
    cfg = "native:1.0:port"
    pa.bring_app_front()
    set_display("native", "1.0", False)
    pa.force_stop()
    time.sleep(1.0)
    pa.start_monitoring()
    pa.poll(lambda: (pa.is_connected(pa.status()) and pa.pipe_state(pa.status()) == "running", None), 25.0, interval=1.0)
    time.sleep(1.5)
    pa.bring_app_front()
    try:
        tap_reached(lambda n: "Station Dim" in n.text, "Station Dim button")
    except LookupError as exc:
        results.append(Cell("native:1.0:port:dim", "native", "1.0", "port", "station-dim", False,
                            [f"Station Dim button: {exc}"], [], [], "", ""))
        return
    time.sleep(2.0)
    _, dimmed = dump("dim-on")
    png = capture_png()
    controls_hidden = not any(n.text == "Stop Monitoring" for n in dimmed)
    save_jpeg(png, "native-f1.0-port-dim-station")
    adb("shell", "input", "tap", "360", "800")
    time.sleep(2.0)
    _, woke = dump("dim-off")
    controls_back = any(n.text == "Stop Monitoring" for n in woke)
    ok = controls_hidden and controls_back
    off = [] if ok else [f"controls hidden while dimmed={controls_hidden}, back after tap={controls_back}"]
    results.append(Cell("native:1.0:port:dim", "native", "1.0", "port", "station-dim", ok, off, [], [],
                        "native-f1.0-port-dim-station.jpg",
                        f"dim hides controls={controls_hidden}; tap wakes={controls_back}"))
    log(f"[{cfg}] station dim: hidden={controls_hidden} back={controls_back}")


# ----------------------------------------------------------------------------- results and report

def load_results() -> dict[str, dict]:
    if RESULTS.exists():
        return {r["cid"]: r for r in json.loads(RESULTS.read_text(encoding="utf-8"))}
    return {}


def save_results(cells: dict[str, dict]) -> None:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(sorted(cells.values(), key=lambda r: r["cid"]), indent=1, ensure_ascii=False),
                       encoding="utf-8")


def render_report(cells: dict[str, dict], meta: dict) -> str:
    rows = sorted(cells.values(), key=lambda r: (r["profile"], float(r["font"]) if r["font"][0].isdigit() else 0,
                                                 r["orient"], r["screen"]))
    passed = sum(1 for r in rows if r["ok"])
    total_bytes = sum((SHOT_DIR / r["shot"]).stat().st_size for r in rows if r["shot"] and (SHOT_DIR / r["shot"]).exists())
    lines = [
        "# Adaptive layout on Android (2026-10-11)",
        "",
        f"Generated by `scripts/adaptive_ui_android.py` on {meta.get('when', '?')} against commit `{meta.get('commit', '?')}`.",
        f"Device: {meta.get('device', '?')}. Backend: local `WEB_PIPELINE=remote` on 127.0.0.1:8000 with `adb reverse`.",
        "",
        f"Cells: {len(rows)}; pass {passed}; fail {len(rows) - passed}; cells re-run after later UI changes: {sum(1 for r in rows if r.get('run', '').startswith('re-run'))}. Screenshots: `docs/screenshots/adaptive-android/` "
        f"({total_bytes / 1024 / 1024:.2f} MB, limit 6 MB). uiautomator dumps are kept in `/tmp/btb-adaptive-dumps/`.",
        "",
        "## Matrix",
        "",
        "Profiles: small 480x854 @240, native 720x1600 @300, large-phone 1080x2400 @420, tablet-like 1200x1920 @240.",
        "Font scales: 0.85, 1.0, 1.3, 1.5. Portrait for every profile and font; landscape for native and tablet-like at",
        "font 1.0 and 1.5 (pruned from all four fonts for the time budget; every font is covered in portrait).",
        "Screens: a idle, b monitoring, c mode control (the 'What may be shared?' chooser), d on-phone approval dialog.",
        "",
        "Assertions per cell: clickables fully inside the screen (or below the fold of a scroll container, reported as a note);",
        "clickables at least 44dp in both dimensions (fail) and the 48dp target (warning); no overlapping clickables;",
        "unreachable content with no scroll container (fail); status line matching the backend. Dialog cells (c, d) judge only",
        "the dialog panel, because the main window underneath is covered by the modal. Clipping is judged from the screenshot",
        "and recorded in the notes column (a uiautomator dump always carries the full text).",
        "",
        "## Results",
        "",
        "| Cell (profile, font, orientation, screen) | Result | Offenders | Warnings and notes | Screenshot | Run |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        cell = f"{r['profile']}, {r['font']}, {r['orient']}, {r['screen']}"
        result = "PASS" if r["ok"] else "FAIL"
        off = "; ".join(r["offenders"]) or "none"
        wn = "; ".join(r["warnings"] + r["notes"] + ([r["detail"]] if r.get("detail") else [])) or "none"
        shot = f"[{r['shot']}](screenshots/adaptive-android/{r['shot']})" if r["shot"] else "n/a"
        run = r.get("run") or "full 2026-10-11 02:55-03:20 (see Notes)"
        lines.append(f"| {cell} | {result} | {_cell(off)} | {_cell(wn)} | {shot} | {_cell(run)} |")
    lines += ["", "## Notes", ""]
    lines += [f"- {n}" for n in meta.get("notes", [])]
    for rr in meta.get("reruns", []):
        lines.append(f"- Re-run {rr['when']} against commit `{rr['commit']}` with `--only {rr['only']}`; "
                     f"rows whose Run column says re-run replace the full-matrix result for that cell.")
        lines += [f"  - {n}" for n in rr.get("notes", [])]
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma list of profile:font:port|land configurations to run (replaces their cells)")
    ap.add_argument("--report-only", action="store_true", help="render the report from results.json and exit")
    args = ap.parse_args()
    cells = load_results()
    meta_path = SHOT_DIR / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if args.report_only:
        REPORT.write_text(render_report(cells, meta), encoding="utf-8")
        print(f"report: {REPORT}")
        return 0

    def log(msg: str) -> None:
        print(msg, flush=True)

    configs = []
    for profile in PROFILES:
        for font in FONT_SCALES:
            configs.append(f"{profile}:{font}:port")  # landscape cells come from the rotation check inside it
    if args.only:
        wanted = set(args.only.split(","))
        configs = [c for c in configs if c in wanted or c.rsplit(":", 1)[0] + ":land" in wanted]
    if not configs:
        print("no configurations selected", file=sys.stderr)
        return 2

    orig = record_originals()
    log(f"originals recorded in {ORIG_ENV}: {orig}")
    started = dt.datetime.now()
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    run_stamp = f"re-run {started.strftime('%Y-%m-%d %H:%M')} @ {commit}" if args.only else \
        f"full {started.strftime('%Y-%m-%d %H:%M')} @ {commit}"
    backend = pa.Backend(Path(tempfile.mkdtemp(prefix="btb-adaptive-")))
    run_notes: list[str] = []
    try:
        pa.preflight()
        backend.start()
        backend.wait_healthy()
        pa.ensure_adb_reverse()
        for cfg in configs:
            profile, font, _ = cfg.split(":")
            dpi = PROFILES[profile][1]
            fresh: list[Cell] = []
            try:
                run_config(cfg, dpi, fresh, log)
            except Exception as exc:  # one configuration failing must not stop the matrix
                run_notes.append(f"{cfg}: aborted: {type(exc).__name__}: {exc}")
                log(f"[{cfg}] aborted: {exc}\n{traceback.format_exc(limit=2)}")
            for c in fresh:
                c.run = run_stamp
                cells[c.cid] = c.__dict__
            save_results(cells)
            log(f"[{cfg}] cells: " + ", ".join(f"{c.screen}={'PASS' if c.ok else 'FAIL'}" for c in fresh))
        # Station Dim round trip (after the matrix, once, in native portrait).
        if not args.only or "native:1.0:port" in args.only.split(","):
            dim: list[Cell] = []
            try:
                station_dim_check(dim, PROFILES["native"][1], log)
            except Exception as exc:
                dim.append(error_cell("native:1.0:port", "a", f"dim check: {exc}"))
            for c in dim:
                c.run = run_stamp
                cells[c.cid] = c.__dict__
            save_results(cells)
    finally:
        for step in (pa.force_stop, lambda: adb("reverse", "--remove-all"), backend.kill):
            try:
                step()
            except Exception as exc:
                log(f"cleanup step failed: {exc}")
        log("restore: " + restore_display())
    if args.only:
        # A pruned re-run keeps the full matrix's when/commit and records itself in reruns.
        meta.setdefault("when", started.strftime("%Y-%m-%d %H:%M"))
        meta.setdefault("commit", commit)
        meta.setdefault("device", "SM-E075F (Galaxy A07), Android 16, serial R9ZY80PKWTW; physical 720x1600 @300")
        meta.setdefault("reruns", []).append({"when": started.strftime("%Y-%m-%d %H:%M"), "commit": commit,
                                              "only": args.only, "notes": run_notes})
    else:
        meta.update({
            "when": started.strftime("%Y-%m-%d %H:%M"),
            "commit": commit,
            "device": "SM-E075F (Galaxy A07), Android 16, serial R9ZY80PKWTW; physical 720x1600 @300",
            "notes": run_notes + meta.get("notes", []),
        })
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    REPORT.write_text(render_report(cells, meta), encoding="utf-8")
    failed = [c for c in cells.values() if not c["ok"]]
    print(f"cells {len(cells)}, failed {len(failed)}; report: {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
