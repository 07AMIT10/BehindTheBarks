"""Response headers, the dashboard CSP, the households Origin check and TLS-only mode (H2/H3).

The browser-level CSP check (no violations in Chrome) is scripts/csp_check.mjs; these tests pin the
server side: what is sent, which hashes, and which requests are refused.
"""

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.security import build_csp, inline_script_hashes
from backend.web.settings import load_config

VIEWER_COOKIE = "btb_session"
BASE_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "permissions-policy": "camera=(self), microphone=(self), geolocation=()",
}


class NoLLM:
    enabled = False
    last_call = None


def _app(tmp_path, *, households=False, **web):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["pipeline"] = "remote"
    if households:
        cfg["web"]["auth"]["mode"] = "households"
    for k, v in web.items():
        cfg["web"][k] = v
    return create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def _assert_base(headers):
    for name, value in BASE_HEADERS.items():
        assert headers.get(name) == value, name


# -- headers ----------------------------------------------------------------------------------------
def test_api_responses_carry_the_security_headers(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/health")
        assert r.status_code == 200
        _assert_base(r.headers)
        assert "strict-transport-security" not in r.headers  # HTTPS-only mode is off by default
        _assert_base(c.get("/no-such-api-route").headers)  # error responses too


def test_static_responses_carry_the_headers_and_the_csp(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/camera.html")
        if r.status_code != 200:
            pytest.skip("frontend/out is not built in this checkout")
        _assert_base(r.headers)
        csp = r.headers["content-security-policy"]
        assert "script-src 'self' 'sha256-" in csp
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]  # never for scripts
        assert "object-src 'none'" in csp and "frame-ancestors 'none'" in csp


def test_csp_can_be_switched_off(tmp_path):
    with TestClient(_app(tmp_path, csp={"enabled": False, "extra_connect_src": []})) as c:
        r = c.get("/health")
        assert "content-security-policy" not in r.headers
        _assert_base(r.headers)  # the other headers do not depend on the CSP switch


# -- CSP hashes -----------------------------------------------------------------------------------
FIXTURE_HTML = """<!DOCTYPE html><html><head>
<script src="/_next/static/chunks/app.js" async></script>
<script>self.__next_f=self.__next_f||[];self.__next_f.push([1,"hello"])</script>
<script type="module">console.log("module inline")</script>
</head><body><script id="x" src="/x.js"></script><script>window.z = 1;</script></body></html>"""


def test_inline_script_hashes_match_the_inline_blocks_of_a_fixture(tmp_path):
    out = tmp_path / "out"
    (out / "sub").mkdir(parents=True)
    (out / "index.html").write_text(FIXTURE_HTML, encoding="utf-8")
    (out / "sub" / "page.html").write_text("<html><script>window.z = 1;</script></html>", encoding="utf-8")
    bodies = [
        'self.__next_f=self.__next_f||[];self.__next_f.push([1,"hello"])',
        'console.log("module inline")',
        "window.z = 1;",
    ]
    expected = sorted({"sha256-" + base64.b64encode(hashlib.sha256(b.encode()).digest()).decode() for b in bodies})
    assert inline_script_hashes(out) == expected  # external <script src> blocks are not hashed


def test_csp_script_src_lists_only_hashes_and_self(tmp_path):
    csp = build_csp(["sha256-AAA=", "sha256-BBB="], connect_extra=["http://localhost:3000"])
    directives = {d.split(" ")[0]: d for d in (p.strip() for p in csp.split(";"))}
    assert directives["script-src"] == "script-src 'self' 'sha256-AAA=' 'sha256-BBB='"
    assert directives["connect-src"] == "connect-src 'self' ws: wss: http://localhost:3000"
    assert directives["style-src"] == "style-src 'self' 'unsafe-inline'"
    assert directives["img-src"] == "img-src 'self' data: blob:"
    assert directives["media-src"] == "media-src 'self' blob:"
    assert directives["worker-src"] == "worker-src 'self' blob:"
    assert directives["base-uri"] == "base-uri 'self'"
    assert directives["form-action"] == "form-action 'self'"
    assert directives["default-src"] == "default-src 'self'"


def test_cors_origins_and_extra_connect_src_reach_connect_src(tmp_path):
    app_ = _app(tmp_path, cors_origins=["http://localhost:3000/"],
                csp={"enabled": True, "extra_connect_src": ["https://dev.example"]})
    with TestClient(app_) as c:
        csp = c.get("/health").headers["content-security-policy"]
    connect = [d for d in csp.split("; ") if d.startswith("connect-src")][0]
    assert "http://localhost:3000" in connect and "https://dev.example" in connect


# -- Origin check (households mode) -------------------------------------------------------------
def _households(tmp_path, cors=(), **web):
    return _app(tmp_path, households=True, cors_origins=list(cors), **web)


@pytest.mark.parametrize("origin,expected", [
    ("http://testserver", 200),  # same origin (the TestClient's own scheme and host)
    ("https://evil.example", 403),  # foreign
    ("null", 403),  # opaque origins are foreign
    ("http://localhost:3000", 403),  # not allow-listed in this matrix row
])
def test_origin_check_on_a_mutating_request(tmp_path, origin, expected):
    with TestClient(_households(tmp_path)) as c:
        r = c.post("/households", json={}, headers={"Origin": origin})
        assert r.status_code == expected
        if expected == 403:
            assert r.json() == {"detail": "forbidden origin"}


def test_absent_origin_is_allowed_for_non_browser_clients(tmp_path):
    with TestClient(_households(tmp_path)) as c:
        assert c.post("/households", json={}).status_code == 200


def test_allow_listed_origin_passes(tmp_path):
    with TestClient(_households(tmp_path, cors=["http://localhost:3000/"])) as c:
        assert c.post("/households", json={}, headers={"Origin": "http://localhost:3000"}).status_code == 200


def test_forwarded_scheme_and_host_make_an_origin_same_origin(tmp_path):
    with TestClient(_households(tmp_path)) as c:
        r = c.post("/households", json={}, headers={
            "Origin": "https://dog.example", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "dog.example"})
        assert r.status_code == 200
        r = c.post("/households", json={}, headers={"Origin": "https://dog.example", "Host": "dog.example"})
        assert r.status_code == 403  # the scheme does not match (http request)


def test_pair_is_exempt_from_the_origin_check(tmp_path):
    with TestClient(_households(tmp_path)) as c:
        r = c.post("/pair", json={"code": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 400  # the pairing answer, not the origin refusal


def test_get_requests_are_not_origin_checked(tmp_path):
    with TestClient(_households(tmp_path)) as c:
        assert c.get("/health", headers={"Origin": "https://evil.example"}).status_code == 200


def test_pin_mode_has_no_origin_check(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/treat", json={}, headers={"Origin": "https://evil.example"})
        assert r.status_code != 403 or r.json() != {"detail": "forbidden origin"}


# -- TLS-only mode (H2) ---------------------------------------------------------------------------
def test_force_https_is_off_by_default(tmp_path):
    with TestClient(_households(tmp_path)) as c:
        assert c.get("/auth/status").status_code == 200
        r = c.post("/households", json={})
        assert r.status_code == 200
        assert "secure" not in r.headers["set-cookie"].lower()


def test_force_https_redirects_gets_and_refuses_other_methods(tmp_path):
    with TestClient(_households(tmp_path, force_https=True)) as c:
        r = c.get("/auth/status", follow_redirects=False)
        assert r.status_code == 308
        assert r.headers["location"] == "https://testserver/auth/status"
        r = c.post("/households", json={}, follow_redirects=False)
        assert r.status_code == 403 and r.json() == {"detail": "https required"}


def test_force_https_exempts_health_and_passes_forwarded_https(tmp_path):
    with TestClient(_households(tmp_path, force_https=True)) as c:
        assert c.get("/health").status_code == 200  # exempt, even over plain http
        r = c.get("/auth/status", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
        assert r.status_code == 200
        assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
        _assert_base(r.headers)


def test_force_https_adds_hsts_only_to_secure_responses(tmp_path):
    with TestClient(_households(tmp_path, force_https=True)) as c:
        r = c.get("/health")  # exempt, but not a secure request
        assert "strict-transport-security" not in r.headers


def test_force_https_forces_the_session_cookie_secure(tmp_path):
    with TestClient(_households(tmp_path, force_https=True)) as c:
        r = c.post("/households", json={}, headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 200
        assert "secure" in r.headers["set-cookie"].lower()


def test_force_https_closes_a_plain_websocket_with_4403(tmp_path):
    with TestClient(_households(tmp_path, force_https=True)) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/ws"):
                pass
        assert exc.value.code == 4403
        assert exc.value.reason == "https required"


def test_force_https_lets_a_forwarded_https_websocket_through(tmp_path):
    with TestClient(_households(tmp_path, force_https=True)) as c:
        with c.websocket_connect("/ws", headers={"X-Forwarded-Proto": "https"}) as ws:
            # no session: the route closes with 4401 (not HTTPS mode's 4403) after accepting
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401


def test_env_switch_turns_force_https_on(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={"BTB_FORCE_HTTPS": "1"})
    assert cfg["web"]["force_https"] is True
    assert load_config(tmp_path / "none.yaml", env={})["web"]["force_https"] is False
