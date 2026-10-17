"""T3.4 Viewer tracking and anti-lurking tests:
- Opening two /video streams as two viewers yields viewers.video == 2 in the downlink queue; closing one -> 1.
- Kick -> 4411 and 60 s cool-down (injected clock).
- Cross-household isolation: kicking in H1 does not affect H2.
"""

import copy
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import (
    CLOSE_VIEWERS_KICKED,
    VIEWER_COOKIE,
    create_app,
)
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.modes import DEFAULT_PREVIEW
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def make_test_cfg(tmp_path: Path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "logs")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 10
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    return cfg


class DownlinkRecorder:
    def __init__(self):
        self.sent = []

    def post(self, msg):
        self.sent.append(msg)
        return True


def test_video_viewers_counting_in_downlink(tmp_path):
    """Opening two /video streams as two viewers yields viewers.video == 2; closing one -> 1."""
    cfg = make_test_cfg(tmp_path)
    clock_val = [1000.0]

    def mock_clock():
        return clock_val[0]

    application = create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(application) as c:
        # Create household & two viewers
        c.post("/households", json={"dog_name": "Rex"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        me = c.get("/me").json()
        household_id = me["household_id"]

        code1 = c.post("/households/me/pairing", json={"kind": "viewer", "label": "Viewer1"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code1})
        v1_cookie = c.cookies[VIEWER_COOKIE]

        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, owner_cookie)
        code2 = c.post("/households/me/pairing", json={"kind": "viewer", "label": "Viewer2"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code2})
        v2_cookie = c.cookies[VIEWER_COOKIE]

        # Retrieve household runtime & intercept pipeline downlinks
        lr = c.portal.call(application.state.registry.get, household_id)

        downlinks = DownlinkRecorder()
        lr.pipeline._post = downlinks.post
        # Set debounce to 0 so every change posts immediately
        lr.tracker.debounce_s = 0.0
        lr.tracker.clock = mock_clock

        # Ingest a fake frame so /video has something to stream
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        lr.pipeline.ingest_frame(jpeg, mock_clock())
        lr.pipeline.set_station_cap("full")
        lr.policy.household_mode = "full"

        # 1. Opening stream 1 yields viewers.video == 1 in policy downlink
        lr.tracker.enter_video("stream-1", viewer_id="viewer-1", label="Chrome on Mac")
        assert lr.tracker.viewer_counts["video"] == 1
        policy_msgs = [m for m in downlinks.sent if m.get("type") == "policy"]
        assert len(policy_msgs) >= 1
        assert policy_msgs[-1]["data"]["viewers"]["video"] == 1

        # 2. Opening stream 2 yields viewers.video == 2 in policy downlink
        lr.tracker.enter_video("stream-2", viewer_id="viewer-2", label="Firefox on Linux")
        assert lr.tracker.viewer_counts["video"] == 2
        policy_msgs = [m for m in downlinks.sent if m.get("type") == "policy"]
        assert policy_msgs[-1]["data"]["viewers"]["video"] == 2

        # 3. Closing stream 1 yields viewers.video == 1
        lr.tracker.leave_video("stream-1")
        assert lr.tracker.viewer_counts["video"] == 1
        policy_msgs = [m for m in downlinks.sent if m.get("type") == "policy"]
        assert policy_msgs[-1]["data"]["viewers"]["video"] == 1

        # 4. Closing stream 2 yields viewers.video == 0
        lr.tracker.leave_video("stream-2")
        assert lr.tracker.viewer_counts["video"] == 0
        policy_msgs = [m for m in downlinks.sent if m.get("type") == "policy"]
        assert policy_msgs[-1]["data"]["viewers"]["video"] == 0

        # 5. /video request executes and cleans up properly
        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, v1_cookie)
        res = c.get("/video?max_frames=1")
        assert res.status_code == 200
        assert lr.tracker.viewer_counts["video"] == 0


def test_kick_closes_ws_with_4411_and_60s_cooldown(tmp_path):
    """Kick -> WebSocket closed with 4411 and 60s cooldown on /video (with injected clock)."""
    cfg = make_test_cfg(tmp_path)
    now = [1000.0]

    def mock_clock():
        return now[0]

    application = create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(application) as c:
        c.post("/households", json={"dog_name": "Buddy"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        household_id = c.get("/me").json()["household_id"]

        code = c.post("/households/me/pairing", json={"kind": "viewer", "label": "Viewer"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code})
        viewer_cookie = c.cookies[VIEWER_COOKIE]

        lr = c.portal.call(application.state.registry.get, household_id)

        lr.tracker.clock = mock_clock

        # Ingest frame
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        lr.pipeline.ingest_frame(jpeg, mock_clock())

        # Connect viewer over WebSocket
        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, viewer_cookie)
        with c.websocket_connect("/ws") as ws:
            init_st = ws.receive_json()
            assert init_st["type"] == "status"

            # Owner calls POST /viewers/disconnect-all
            c.cookies.clear()
            c.cookies.set(VIEWER_COOKIE, owner_cookie)
            kick_res = c.post("/viewers/disconnect-all")
            assert kick_res.status_code == 200
            assert kick_res.json()["ok"] is True
            assert kick_res.json()["cooldown_s"] == 60

            # Assert WebSocket closes with 4411
            close_frame = ws.receive()
            assert close_frame["type"] == "websocket.close"
            assert close_frame["code"] == CLOSE_VIEWERS_KICKED

        # In cooldown period: /video must be rejected (403)
        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, viewer_cookie)
        vid_res = c.get("/video")
        assert vid_res.status_code == 403
        assert "Viewers disconnected" in vid_res.json()["detail"]

        # Advance clock by 30s: still in cooldown
        now[0] += 30.0
        vid_res2 = c.get("/video")
        assert vid_res2.status_code == 403

        # Advance clock past 60s: cooldown expired, /video accepted
        now[0] += 31.0
        vid_res3 = c.get("/video?max_frames=1")
        assert vid_res3.status_code == 200


def test_kick_isolation_between_households(tmp_path):
    """Kicking viewers in Household 1 does not affect Household 2."""
    cfg = make_test_cfg(tmp_path)

    application = create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(application) as c:
        # Household 1
        c.post("/households", json={"dog_name": "Dog 1"})
        h1_owner_cookie = c.cookies[VIEWER_COOKIE]
        code1 = c.post("/households/me/pairing", json={"kind": "viewer", "label": "V1"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code1})
        h1_v_cookie = c.cookies[VIEWER_COOKIE]

        # Household 2
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Dog 2"})
        h2_owner_cookie = c.cookies[VIEWER_COOKIE]
        code2 = c.post("/households/me/pairing", json={"kind": "viewer", "label": "V2"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code2})
        h2_v_cookie = c.cookies[VIEWER_COOKIE]

        # Ingest frames in H2 so /video can answer
        h2_id = c.get("/me").json()["household_id"]
        lr2 = c.portal.call(application.state.registry.get, h2_id)
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        lr2.pipeline.ingest_frame(jpeg, time.time())

        # Kick in H1
        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, h1_owner_cookie)
        k = c.post("/viewers/disconnect-all")
        assert k.status_code == 200

        # H1 /video rejected
        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, h1_v_cookie)
        assert c.get("/video").status_code == 403

        # H2 /video NOT affected!
        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, h2_v_cookie)
        assert c.get("/video?max_frames=1").status_code == 200
