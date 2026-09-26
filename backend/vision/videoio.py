"""Debug-video writer that macOS QuickTime can actually play.

OpenCV's `mp4v` output (MPEG-4 Part 2) shows as a solid green screen in QuickTime, even though the
frames are fine. We pipe raw frames to ffmpeg for H.264 (yuv420p, faststart) instead, and fall back to
OpenCV's `mp4v` only when ffmpeg is not installed.

    w = DebugVideoWriter("out/debug.mp4", fps=8)
    w.write(frame_bgr)      # size is taken from the first frame
    w.release()
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np


class DebugVideoWriter:
    def __init__(self, path: str | Path, fps: float):
        self.path, self.fps = str(path), float(fps)
        self._proc: subprocess.Popen | None = None
        self._cv: cv2.VideoWriter | None = None
        self._size: tuple[int, int] | None = None  # (w, h)

    def _start(self, w: int, h: int) -> None:
        self._size = (w, h)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            self._proc = subprocess.Popen(
                [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                 "-r", str(self.fps), "-i", "-", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",  # yuv420p needs even sides
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", self.path],
                stdin=subprocess.PIPE,
            )
        else:
            self._cv = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))

    def write(self, frame: np.ndarray) -> None:
        if self._size is None:
            self._start(frame.shape[1], frame.shape[0])
        if (frame.shape[1], frame.shape[0]) != self._size:
            frame = cv2.resize(frame, self._size)
        if self._proc is not None:
            self._proc.stdin.write(np.ascontiguousarray(frame).tobytes())
        else:
            self._cv.write(frame)

    def release(self) -> None:
        if self._proc is not None:
            self._proc.stdin.close()
            self._proc.wait()
            self._proc = None
        if self._cv is not None:
            self._cv.release()
            self._cv = None
