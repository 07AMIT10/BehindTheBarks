"""Per-request auth context (T2.2): who is calling, and which household's runtime they reach.

In `pin` mode (the default) one shared `legacy` household serves all traffic and today's
`verify_request_auth` semantics decide who gets in; `households` mode (T2.3) derives the household
from the credential instead. No route ever reads a household id from the query, body or path — that
is the isolation rule the Phase 2 suite enforces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.web.hub import Hub
from backend.web.runtime import Runtime

LEGACY = "legacy"


@dataclass
class Ctx:
    household_id: str
    role: str
    viewer_id: str | None
    runtime: Runtime
    hub: Hub
    pipeline: Any
