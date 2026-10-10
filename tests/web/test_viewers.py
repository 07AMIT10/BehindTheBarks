"""Viewer invites and owner controls (T2.6): a code joins a viewer, owners list and revoke."""

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def spki_b64() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.public_key().public_bytes(serialization.Encoding.DER,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


@pytest.fixture()
def owner_and_viewer(tmp_path):
    """One client, two sessions: the owner's cookie and the joined viewer's cookie are swapped in.

    A second TestClient would run the app's lifespan twice (two runtimes, two loops), so the fixture
    swaps `c.cookies` instead."""
    with TestClient(app(tmp_path)) as c:
        c.post("/households", json={"dog_name": "Rex"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        code = c.post("/households/me/pairing", json={"kind": "viewer", "label": "Aunt May"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code, "label": "Aunt May"})
        viewer_cookie = c.cookies[VIEWER_COOKIE]
        c.cookies.clear()
        yield c, owner_cookie, viewer_cookie


def test_join_sets_a_cookie_and_records_the_ceiling(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        code = c.post("/households/me/pairing", json={"kind": "viewer", "mode_ceiling": "limited"}).json()["code"]
        c.cookies.clear()
        r = c.post("/join", json={"code": code, "label": "Aunt May"})
        assert r.status_code == 200 and VIEWER_COOKIE in r.headers.get("set-cookie", "")
        me = c.get("/me").json()
        assert me["role"] == "viewer" and me["mode_ceiling"] == "limited"
        # the code is spent
        assert c.post("/join", json={"code": code, "label": "again"}).status_code == 400


def test_a_viewer_code_cannot_pair_a_station(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={})
        code = c.post("/households/me/pairing", json={"kind": "viewer"}).json()["code"]
        r = c.post("/pair", json={"code": code, "pubkey_spki_b64": spki_b64(), "device_name": "Pixel 8"})
        assert r.status_code == 400  # the kind must match


def test_viewer_cannot_reach_owner_endpoints(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={})
        code = c.post("/households/me/pairing", json={"kind": "viewer"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code, "label": "v"})
        assert c.post("/households/me/pairing", json={"kind": "station"}).status_code == 403
        assert c.get("/households/me/members").status_code == 200  # any member may list


def test_members_list_shows_stations(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={})
        code = c.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        r = c.post("/pair", json={"code": code, "pubkey_spki_b64": spki_b64(), "device_name": "Pixel 8"})
        assert r.status_code == 200
        members = c.get("/households/me/members").json()
        assert any(s["name"] == "Pixel 8" for s in members["stations"])


def test_owner_cannot_delete_themselves(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={})
        members = c.get("/households/me/members").json()
        owner_row = [v for v in members["viewers"] if v["role"] == "owner"][0]
        assert c.request("DELETE", f"/viewers/{owner_row['id']}").status_code == 400
        assert c.get("/me").json()["role"] == "owner"


def test_owner_revokes_a_viewer_and_their_socket_closes(owner_and_viewer):
    c, owner_cookie, viewer_cookie = owner_and_viewer
    c.cookies.set(VIEWER_COOKIE, owner_cookie)
    members = c.get("/households/me/members").json()
    target = [v for v in members["viewers"] if v["role"] == "viewer"][0]

    ws = c.websocket_connect("/ws", cookies={VIEWER_COOKIE: viewer_cookie})
    with ws:
        assert ws.receive_json()["type"] == "status"
        assert c.request("DELETE", f"/viewers/{target['id']}").status_code == 200
        assert ws.receive().get("code") == 4410  # the revoked viewer's socket is closed
    c.cookies.set(VIEWER_COOKIE, viewer_cookie)
    assert c.get("/me").status_code == 401  # and that cookie no longer authenticates


def test_viewer_cannot_switch_the_household_to_demo(owner_and_viewer):
    """POST /mode live|demo is a household-wide switch: owners only, like every other mode change."""
    c, owner_cookie, viewer_cookie = owner_and_viewer
    c.cookies.set(VIEWER_COOKIE, viewer_cookie)
    assert c.post("/mode", json={"mode": "demo"}).status_code == 403
    c.cookies.set(VIEWER_COOKIE, owner_cookie)
    # the owner gets past the role check (a demo-less test household answers 400, not 403)
    assert c.post("/mode", json={"mode": "demo"}).status_code != 403


def test_viewer_cannot_rename_the_dog(owner_and_viewer):
    """The household profile is owner-only (T2.3): a viewer's PUT must be refused and change nothing."""
    c, owner_cookie, viewer_cookie = owner_and_viewer
    c.cookies.set(VIEWER_COOKIE, viewer_cookie)
    assert c.put("/households/me/profile", json={"dog_name": "Hacked", "location": ""}).status_code == 403
    c.cookies.set(VIEWER_COOKIE, owner_cookie)
    assert c.get("/status").json()["profile"]["dog_name"] == "Rex"
