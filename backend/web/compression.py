"""gzip for text responses (JSON, JS, CSS, HTML), switchable at runtime through the admin tuning.

Never compressed: MJPEG /video (JPEG is already compressed, and gzip would hold frames back),
WebSockets, the phone ingest sockets, and the demo clips (already H.264). Those skip the gzip layer
entirely, so a toggle can never stall a live stream.
"""

from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

SKIP_PREFIXES = ("/video", "/ws", "/ingest", "/demo/clips")


class ToggleGzip:
    def __init__(self, app: ASGIApp, *, tuning) -> None:
        self.app = app
        self.tuning = tuning
        # level 6: most of gzip-9's saving at a fraction of the CPU on the free instance
        self._gzip = GZipMiddleware(app, compresslevel=6)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.tuning.get("gzip_enabled") or scope["path"].startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        # Starlette reads minimum_size on every request, so an admin change applies to the next response.
        self._gzip.minimum_size = int(self.tuning.get("gzip_min_size"))
        await self._gzip(scope, receive, send)
