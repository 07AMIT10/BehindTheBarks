"""Revoking a viewer also kills their live audio: DELETE /viewers/{id} closes the viewer's /audio
relay with 4411 (the relay only validates its viewer at connect, so the hub gets an explicit kick).

The pairing handshake fixtures live in test_isolation; this file leans on them."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import VIEWER_COOKIE
from tests.web.test_isolation import (
    Station,
    _go_full,
    _viewer_cookie,
    app,
    as_owner,
    connect_station,
)


def test_revoking_a_viewer_closes_their_live_audio_with_4411(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Rex"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        station = Station(c, b"AUDREVMARK", "PhoneOne")
        _go_full(c, owner_cookie)
        with connect_station(c, station):
            viewer_cookie = _viewer_cookie(c, owner_cookie)
            with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: viewer_cookie}) as ws:
                assert ws.receive_json()["type"] == "audio_hello"  # a live listener on the station
                as_owner(c, owner_cookie)
                members = c.get("/households/me/members").json()
                target = [v for v in members["viewers"] if v["role"] == "viewer"][0]
                assert c.request("DELETE", f"/viewers/{target['id']}").status_code == 200
                with pytest.raises(WebSocketDisconnect) as err:
                    ws.receive_bytes()
                assert err.value.code == 4411

            # the socket is closed for good: a fresh connect with the revoked cookie is 4401
            c.cookies.clear()
            with pytest.raises(WebSocketDisconnect) as err2:
                with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: viewer_cookie}) as ws2:
                    ws2.receive_json()
            assert err2.value.code == 4401
