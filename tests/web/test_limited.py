"""Tests for T3.5 Limited mode image processing and Peek.

Covers:
- limit_jpeg downscaling and Laplacian variance reduction (high-frequency blur).
- Limited frame cache: returns identical bytes within 10s and refreshes after.
- POST /peek endpoint: 15s window when station cap is full.
- POST /peek denied with 409 when station cap is privacy or blackout.
- /video serves raw frames during peek window, blurred frames when expired.
- mode_ceiling enforcement during peek.
"""

from __future__ import annotations

import io
import time
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import VIEWER_COOKIE, create_app
from backend.store import repo as store_repo
from backend.web.limited import Limited, limit_jpeg


def _make_noisy_jpeg(w: int = 640, h: int = 480) -> bytes:
    """Generate a synthetic noisy image with high-frequency edges/patterns."""
    np.random.seed(42)
    img = np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)
    # Add high-contrast grid lines
    img[::20, :, :] = 255
    img[:, ::20, :] = 0
    success, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    assert success
    return buf.tobytes()


def test_limit_jpeg_shrinks_width_and_reduces_laplacian_variance():
    raw_jpeg = _make_noisy_jpeg(640, 480)
    raw_arr = cv2.imdecode(np.frombuffer(raw_jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert raw_arr.shape[1] == 640

    raw_gray = cv2.cvtColor(raw_arr, cv2.COLOR_BGR2GRAY)
    raw_var = cv2.Laplacian(raw_gray, cv2.CV_64F).var()

    limited = limit_jpeg(raw_jpeg, width=240, blur=31, quality=50)
    lim_arr = cv2.imdecode(np.frombuffer(limited, np.uint8), cv2.IMREAD_COLOR)
    assert lim_arr is not None
    assert lim_arr.shape[1] == 240

    lim_gray = cv2.cvtColor(lim_arr, cv2.COLOR_BGR2GRAY)
    lim_var = cv2.Laplacian(lim_gray, cv2.CV_64F).var()

    # High frequency energy must be significantly attenuated by Gaussian blur
    assert lim_var < raw_var
    assert lim_var < raw_var * 0.1


def test_limited_cache_reuses_within_10s_and_refreshes():
    t = [100.0]

    def mock_clock() -> float:
        return t[0]

    cache = Limited(cache_s=10.0, clock=mock_clock)

    jpeg_a = _make_noisy_jpeg(640, 480)
    jpeg_b = _make_noisy_jpeg(320, 240)

    # First call at t=100: processes jpeg_a
    out_1 = cache.process(jpeg_a)
    assert out_1 is not None

    # Call at t=105 with different image: returns cached out_1
    t[0] = 105.0
    out_2 = cache.process(jpeg_b)
    assert out_2 == out_1

    # Call at t=111 (> 10s): refreshes with jpeg_b
    t[0] = 111.0
    out_3 = cache.process(jpeg_b)
    assert out_3 != out_1


def _make_app(tmp_path):
    from backend.web.settings import load_config
    from backend.notify.dashboard import DashboardOnlyNotifier

    class NoLLM:
        enabled = False
        last_call = None

    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 20
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def test_peek_endpoint_and_cap_rejection(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        # Create household
        r_create = client.post("/households", json={"dog_name": "Rover"})
        assert r_create.status_code == 200
        hid = r_create.json()["household_id"]
        owner_tok = client.cookies.get(VIEWER_COOKIE)

        # Station cap is full by default: POST /peek succeeds
        r_peek = client.post("/peek")
        assert r_peek.status_code == 200
        data = r_peek.json()
        assert data["ok"] is True
        assert data["status"] == "peeking"
        assert data["duration_s"] == 15.0

        # Set station cap to privacy
        lr = app.state.registry.peek(hid)
        assert lr is not None
        lr.pipeline.set_station_cap("privacy")

        # POST /peek must fail with 409
        r_peek_denied = client.post("/peek")
        assert r_peek_denied.status_code == 409
        assert r_peek_denied.json()["detail"]["error"] == "station_cap"

        # Set station cap to blackout
        lr.pipeline.set_station_cap("blackout")
        r_peek_denied_blackout = client.post("/peek")
        assert r_peek_denied_blackout.status_code == 409
        assert r_peek_denied_blackout.json()["detail"]["error"] == "station_cap"


def test_peek_promotes_limited_stream_and_expires(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        # Create household
        r_create = client.post("/households", json={"dog_name": "Rover"})
        assert r_create.status_code == 200
        hid = r_create.json()["household_id"]

        # Trigger runtime initialization
        client.get("/mode")
        lr = app.state.registry.peek(hid)
        assert lr is not None

        # Inject mock clock
        sim_time = [1000.0]
        lr.limited.clock = lambda: sim_time[0]

        # Ingest a raw test frame
        raw_jpeg = _make_noisy_jpeg(640, 480)
        lr.pipeline.ingest_frame(raw_jpeg, 1000.0)

        # Set household mode to limited
        lr.policy.set_mode("limited")
        assert lr.policy.household_mode == "limited"

        # In limited mode, /mode returns effective=limited
        r_mode = client.get("/mode")
        assert r_mode.json()["effective"] == "limited"

        # Start peek at t=1000
        r_peek = client.post("/peek")
        assert r_peek.status_code == 200

        # During peek at t=1005: effective mode is full and /video delivers raw frame
        sim_time[0] = 1005.0
        r_mode_peeking = client.get("/mode")
        assert r_mode_peeking.json()["effective"] == "full"
        peeking_video = client.get("/video?max_frames=1").content
        assert raw_jpeg in peeking_video

        # When peek expires at t=1020 (> 15s): effective mode reverts to limited and /video is blurred
        sim_time[0] = 1020.0
        r_mode_expired = client.get("/mode")
        assert r_mode_expired.json()["effective"] == "limited"
        expired_video = client.get("/video?max_frames=1").content
        assert raw_jpeg not in expired_video
        assert b"--frame" in expired_video


def test_peek_respects_viewer_mode_ceiling(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        # Create household
        r_create = client.post("/households", json={"dog_name": "Rover"})
        hid = r_create.json()["household_id"]
        owner_cookie = client.cookies[VIEWER_COOKIE]

        client.get("/mode")
        lr = app.state.registry.peek(hid)
        assert lr is not None
        lr.policy.set_mode("limited")

        # Create pairing for limited viewer
        code = client.post("/households/me/pairing", json={"kind": "viewer", "mode_ceiling": "limited"}).json()["code"]

        # Guest joins
        client.cookies.clear()
        client.post("/join", json={"code": code, "label": "LimitedGuest"})

        # Guest peeks
        r_peek = client.post("/peek")
        assert r_peek.status_code == 200

        # But because ceiling is limited, effective mode remains limited
        r_mode = client.get("/mode")
        assert r_mode.json()["effective"] == "limited"

