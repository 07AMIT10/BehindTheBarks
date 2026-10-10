"""Limited mode image processing (T3.3/T3.5).

In `limited` mode, raw camera frames never leave the server. Images are downscaled,
blurred with a large Gaussian kernel, and re-encoded at low quality to remove all
identifiable human and indoor room features while preserving gross motion and presence.
"""

from __future__ import annotations

import io
from typing import Callable
import cv2
import numpy as np


def _build_limited_slate() -> bytes:
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (320, 240), color=(30, 32, 40))
        d = ImageDraw.Draw(img)
        d.rectangle([5, 5, 314, 234], outline=(75, 85, 99), width=2)
        d.text((110, 100), "LIMITED MODE", fill=(245, 158, 11))
        d.text((70, 125), "Blurred snapshot stream", fill=(156, 163, 175))
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=60)
        return b.getvalue()
    except Exception:
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
            b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
            b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
            b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01"
            b"\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08"
            b"\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        )


def limit_jpeg(jpeg: bytes, *, width: int = 240, blur: int = 31, quality: int = 50) -> bytes:
    """Downsample, blur and compress a JPEG so no room features are discernible."""
    if not jpeg:
        return _build_limited_slate()
    try:
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return _build_limited_slate()
        h, w = img.shape[:2]
        if w > 0:
            target_h = max(1, int(h * (width / w)))
            img = cv2.resize(img, (width, target_h), interpolation=cv2.INTER_AREA)
        k = blur | 1
        img = cv2.GaussianBlur(img, (k, k), 0)
        ret, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ret:
            return buf.tobytes()
    except Exception:
        pass
    return _build_limited_slate()
