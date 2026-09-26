import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from backend import sources
from backend.sources import AUDIO_SR, MediaSources

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """2 s, 25 fps test video with a 440 Hz tone."""
    path = tmp_path_factory.mktemp("clips") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="module")
def silent_clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("silent") / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=25",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def test_fast_video_downsampled_and_monotonic(clip):
    with MediaSources(type="file", path=str(clip), fps=5, fast=True) as src:
        items = list(src.video())
    assert 9 <= len(items) <= 11  # 2 s at 5 fps
    ts = [t for t, _ in items]
    assert ts == sorted(ts)
    assert 0.15 <= np.diff(ts).mean() <= 0.25
    assert items[0][1].shape == (120, 160, 3) and items[0][1].dtype == np.uint8


def test_fast_audio_format_and_length(clip):
    with MediaSources(type="file", path=str(clip), fast=True, chunk_s=0.25) as src:
        items = list(src.audio())
    assert all(c.dtype == np.float32 and c.ndim == 1 for _, c in items)
    total = sum(len(c) for _, c in items)
    assert abs(total - 2 * AUDIO_SR) < 0.1 * AUDIO_SR
    assert np.sqrt(np.mean(np.concatenate([c for _, c in items]) ** 2)) > 0.05  # the tone is there


def test_video_and_audio_share_a_clock(clip):
    with MediaSources(type="file", path=str(clip), fps=5, fast=True) as src:
        v = list(src.video())
        a = list(src.audio())
    assert abs(v[0][0] - a[0][0]) < 0.3
    assert abs(v[-1][0] - a[-1][0]) < 0.6


def test_audio_override(clip, silent_clip):
    with MediaSources(type="file", path=str(silent_clip), audio=str(clip), fast=True) as src:
        assert sum(len(c) for _, c in src.audio()) > AUDIO_SR


def test_missing_embedded_audio_yields_nothing(silent_clip):
    with MediaSources(type="file", path=str(silent_clip), fast=True) as src:
        assert list(src.audio()) == []


def test_bad_audio_override_raises(clip, tmp_path):
    bad = tmp_path / "nope.wav"
    bad.write_text("not audio")
    with MediaSources(type="file", path=str(clip), audio=str(bad), fast=True) as src:
        with pytest.raises(RuntimeError):
            list(src.audio())


def test_realtime_playback_is_paced(clip):
    start = time.time()
    with MediaSources(type="file", path=str(clip), fps=10) as src:
        n = sum(1 for _ in src.video())
    elapsed = time.time() - start
    assert 1.7 <= elapsed <= 2.6 and n >= 15


def test_realtime_drops_frames_when_consumer_is_slow(clip):
    with MediaSources(type="file", path=str(clip), fps=10) as src:
        n = 0
        for _ in src.video():
            n += 1
            time.sleep(0.35)
    assert n < 12  # would be ~20 without dropping


def test_loop_keeps_timestamps_monotonic_and_aligned(clip):
    with MediaSources(type="file", path=str(clip), fps=5, loop=True) as src:
        vit, ait = src.video(), src.audio()
        v = [next(vit) for _ in range(25)]  # > 2 passes of a 2 s clip
        a = [next(ait) for _ in range(40)]
        src.close()
    vts = [t for t, _ in v]
    assert vts == sorted(vts) and vts[-1] - vts[0] > 4.0
    ats = [t for t, _ in a]
    assert ats == sorted(ats)


def test_fast_ignores_loop(clip):
    with MediaSources(type="file", path=str(clip), fps=5, fast=True, loop=True) as src:
        assert len(list(src.video())) < 15


def test_close_stops_realtime_iterator_promptly(clip):
    src = MediaSources(type="file", path=str(clip), fps=5, loop=True)
    seen = []

    def consume():
        for item in src.video():
            seen.append(item)

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.6)
    src.close()
    t.join(timeout=2.0)
    assert not t.is_alive()


class FakeCap:
    """Delivers a frame every 10 ms until released."""

    def __init__(self):
        self.n = 0
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        time.sleep(0.01)
        self.n += 1
        return True, np.full((4, 4, 3), self.n % 255, np.uint8)

    def release(self):
        self.released = True


def test_live_reader_keeps_newest_frame_and_shuts_down(monkeypatch):
    cap = FakeCap()
    monkeypatch.setattr(sources.cv2, "VideoCapture", lambda *_: cap)
    src = MediaSources(type="webcam", fps=10)
    items = []
    for item in src.video():
        items.append(item)
        if len(items) == 6:
            break
    ts = [t for t, _ in items]
    assert np.diff(ts).mean() == pytest.approx(0.1, abs=0.04)  # downsampled from ~100 fps
    assert cap.n > len(items) * 3  # reader ran far ahead; old frames were dropped, not queued
    assert time.time() - ts[-1] < 0.2  # newest frame, not a stale one
    src.close()
    assert cap.released and not src._reader.is_alive()


def test_config_wiring(clip):
    cfg = {"data": {"fps": 6, "source": {"type": "file", "path": str(clip), "device": 0, "audio": None, "loop": False}}}
    src = MediaSources.from_config(cfg, fast=True)
    assert src.fps == 6 and src.fast
    with pytest.raises(ValueError):
        MediaSources(type="stream", path=None)
    with pytest.raises(ValueError):
        MediaSources(type="usb")
