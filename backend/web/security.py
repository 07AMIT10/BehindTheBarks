"""Response security headers, the dashboard CSP, the households Origin check and TLS-only mode (H2/H3).

One pure ASGI middleware does all four, so it covers API, static and WebSocket traffic alike and never
buffers a streaming response (the MJPEG preview).

- Every HTTP response: nosniff, no-referrer, frame DENY, COOP same-origin, a Permissions-Policy that
  allows camera and microphone to this origin only, and (when enabled) the Content-Security-Policy.
- CSP: `script-src` allows only `'self'` and the sha256 hashes of the inline `<script>` blocks found in
  `frontend/out/**/*.html` at startup. There is no `'unsafe-inline'` for scripts.
- Origin check (households mode): a mutating request (POST, PUT, PATCH, DELETE) that carries an `Origin`
  header must be same-origin or allow-listed in `web.cors_origins`. Absent Origin is allowed.
  `/pair` and `/health` are exempt.
- TLS-only (`web.force_https`, env `BTB_FORCE_HTTPS=1`): a request that is not secure gets a 308 redirect
  (GET/HEAD) or a 403 `{"detail": "https required"}`; a WebSocket is closed with 4403. Secure responses
  carry Strict-Transport-Security. `/health` is exempt.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ORIGIN_EXEMPT_PATHS = frozenset({"/pair", "/health"})
HTTPS_EXEMPT_PATHS = frozenset({"/health"})
HSTS = b"max-age=31536000; includeSubDomains"
BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(self), microphone=(self), geolocation=()"),
)

_INLINE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)


def inline_script_hashes(out_dir: str | Path) -> list[str]:
    """sha256 (base64) of every inline `<script>` body in the static export, sorted and unique."""
    root = Path(out_dir)
    found: set[str] = set()
    if root.is_dir():
        for html in sorted(root.rglob("*.html")):
            text = html.read_text(encoding="utf-8", errors="replace")
            for attrs, body in _INLINE_SCRIPT.findall(text):
                if re.search(r"\bsrc\s*=", attrs, re.IGNORECASE):
                    continue  # external scripts are covered by 'self'
                digest = hashlib.sha256(body.encode("utf-8")).digest()
                found.add("sha256-" + base64.b64encode(digest).decode("ascii"))
    return sorted(found)


def build_csp(script_hashes: Iterable[str], connect_extra: Iterable[str] = (),
              media_extra: Iterable[str] = ()) -> str:
    """The dashboard policy. `connect_extra` carries web.cors_origins and web.csp.extra_connect_src;
    `media_extra` carries the object-storage host the demo-video redirect points to (present only
    when demo.video_source is r2): the browser follows that 302 for the <video> element, so an R2
    bucket would otherwise be playable never."""
    scripts = " ".join(f"'sha256-{h.removeprefix('sha256-')}'" for h in script_hashes)
    connect = " ".join(["'self'", "ws:", "wss:", *dict.fromkeys(o for o in connect_extra if o)])
    media = " ".join(["'self'", "blob:", *dict.fromkeys(o for o in media_extra if o)])
    directives = [
        "default-src 'self'",
        ("script-src 'self' " + scripts).strip(),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        f"media-src {media}",
        "worker-src 'self' blob:",
        f"connect-src {connect}",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
    return "; ".join(directives)


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def is_secure(scope: Scope) -> bool:
    """https/wss on the connection, or a proxy that says so with X-Forwarded-Proto."""
    if scope.get("scheme") in ("https", "wss"):
        return True
    return _first(Headers(scope=scope).get("x-forwarded-proto")).lower() == "https"


def _https_url(scope: Scope) -> str:
    headers = Headers(scope=scope)
    host = _first(headers.get("x-forwarded-host")) or headers.get("host", "")
    path = scope.get("root_path", "") + scope.get("path", "/")
    query = scope.get("query_string", b"").decode("latin-1")
    return f"https://{host}{path}" + (f"?{query}" if query else "")


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, csp: str | None, force_https: bool, origin_check: bool,
                 allowed_origins: Iterable[str] = ()) -> None:
        self.app = app
        self.headers: tuple[tuple[bytes, bytes], ...] = BASE_HEADERS + (
            ((b"content-security-policy", csp.encode("latin-1")),) if csp else ())
        self.force_https = force_https
        self.origin_check = origin_check
        self.allowed_origins = frozenset(o.strip().rstrip("/").lower() for o in allowed_origins if o.strip())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            if self.force_https and scope.get("path") not in HTTPS_EXEMPT_PATHS and not is_secure(scope):
                await send({"type": "websocket.close", "code": 4403, "reason": "https required"})
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        secure = is_secure(scope)
        path, method = scope.get("path", "/"), scope.get("method", "GET")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = list(message.get("headers", []))
                present = {name for name, _ in out}
                for name, value in self.headers:
                    if name not in present:
                        out.append((name, value))
                if self.force_https and secure:
                    out.append((b"strict-transport-security", HSTS))
                message = {**message, "headers": out}
            await send(message)

        if self.force_https and path not in HTTPS_EXEMPT_PATHS and not secure:
            if method in ("GET", "HEAD"):
                refusal: Response = Response(status_code=308, headers={"location": _https_url(scope)})
            else:
                refusal = JSONResponse({"detail": "https required"}, status_code=403)
            await refusal(scope, receive, send_with_headers)
            return

        if self.origin_check and method in MUTATING_METHODS and path not in ORIGIN_EXEMPT_PATHS:
            origin = Headers(scope=scope).get("origin")
            if origin is not None and not self._origin_allowed(scope, origin):
                refusal = JSONResponse({"detail": "forbidden origin"}, status_code=403)
                await refusal(scope, receive, send_with_headers)
                return

        await self.app(scope, receive, send_with_headers)

    def _origin_allowed(self, scope: Scope, origin: str) -> bool:
        origin = origin.strip().rstrip("/").lower()
        if origin in self.allowed_origins:
            return True
        headers = Headers(scope=scope)
        scheme = _first(headers.get("x-forwarded-proto")).lower() or (
            "https" if scope.get("scheme") in ("https", "wss") else "http")
        host = (_first(headers.get("x-forwarded-host")) or headers.get("host", "")).lower()
        parsed = urlparse(origin)
        return bool(host) and parsed.scheme == scheme and parsed.netloc == host
