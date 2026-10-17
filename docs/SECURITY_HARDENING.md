# Security hardening: settings, env vars and how to verify

Covers H2 (TLS-only), H3 (response headers, CSP, Origin check) and H4 (one rate limiter). Code:
`backend/web/security.py`, `backend/web/limits.py`, `backend/main.py`, `backend/web/settings.py`.
Dependency audit: `docs/SECURITY_AUDIT_2026-10-11.md`.

## Settings and env vars

| Setting | Env var | Default | Meaning |
|---|---|---|---|
| `web.force_https` | `BTB_FORCE_HTTPS=1` | off | TLS-only mode (below). Off keeps localhost flows working. |
| `web.csp.enabled` | none | `true` | Send the dashboard Content-Security-Policy. |
| `web.csp.extra_connect_src` | none | `[]` | Extra origins for `connect-src`, for the cross-origin dev override (`?backend=`). Example: `["https://dev.example"]`. |
| `web.cors_origins` | none | `[]` | Existing setting. Each entry is also added to `connect-src` and is accepted by the households Origin check. |
| `web.auth.household_creates_per_hour` | `BTB_HOUSEHOLD_CREATES_PER_HOUR` | `5` | Existing. Households created per client IP per hour (`POST /households`). |
| `web.rate_limits.enabled` | none | follows `web.auth.enabled` | Existing. Gates the minimum-interval limits on `/join` (2 s), `/auth/recover` (5 s) and the treat, torch, profile and flip actions. |

The limiter constants are in `backend/web/limits.py` and are not settings:

- `MAX_KEYS = 4096`: the key table is pruned past this many keys (expired entries first, then the ones that expire soonest).
- Failure lockout (per client IP, separately for `/pair`, `/auth/recover` and `/join`): 10 failures within 600 s lock the IP for 300 s. Each further lockout doubles the length, up to 3600 s. A successful call from that IP clears its failures and its doubling.
- `/pair` also has a sliding window of 10 attempts per minute per IP and 100 per hour globally (every attempt counts, valid or not).

Refusals: a lockout returns HTTP 429 with a `Retry-After` header in seconds and the generic detail `too many failed attempts; try again later`, whatever the code or key was. The window and interval limits keep their existing messages.

## H2: TLS-only mode (`BTB_FORCE_HTTPS=1`)

Applies to every request except `GET /health`, when the request is not secure. A request is secure when its scheme is https or wss, or when `X-Forwarded-Proto` says https.

- GET and HEAD: 308 to the same path on `https://<X-Forwarded-Host or Host>`.
- Other methods: 403 `{"detail": "https required"}`.
- WebSocket: closed with code 4403, reason `https required`, before the upgrade completes.
- Secure responses carry `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
- The session cookie is always `Secure` in this mode.

`render.yaml` sets `BTB_FORCE_HTTPS: "1"` as a placeholder. It holds no secret. Render terminates TLS and sets `X-Forwarded-Proto`, so the deployed service is unaffected by the switch. The change takes effect on the next deploy, which happens only after the owner merges to `main`.

## H3: response headers and CSP

Every HTTP response, including errors and static files, carries:

- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `X-Frame-Options: DENY`
- `Cross-Origin-Opener-Policy: same-origin`
- `Permissions-Policy: camera=(self), microphone=(self), geolocation=()` (the `/camera` page needs camera and microphone)
- `Content-Security-Policy` when `web.csp.enabled` is true

The CSP is built at startup from `frontend/out/**/*.html`. It hashes each inline `<script>` body (external scripts are covered by `'self'`):

```
default-src 'self'; script-src 'self' 'sha256-…' …; style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:;
connect-src 'self' ws: wss: <web.cors_origins> <web.csp.extra_connect_src>;
object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
```

Rebuilding the frontend changes the hashes. Restart the backend afterwards, because they are computed at startup.

### Origin check (households mode only)

A request with method POST, PUT, PATCH or DELETE that carries an `Origin` header must match the request's scheme and host (`X-Forwarded-Proto` and `X-Forwarded-Host` are honoured), or be listed in `web.cors_origins`. Otherwise 403 `{"detail": "forbidden origin"}`. A request without `Origin` is allowed (non-browser clients). `/pair` and `/health` are exempt. `GET` is never checked. WebSocket routes keep their own Origin check.

### `/debug/metrics`

The response no longer has the server-wide `households` count.

## Verify

```
# backends (pick free ports; 8020-8029 are reserved for this check here)
WEB_PIPELINE=remote .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8020
WEB_PIPELINE=remote BTB_AUTH_MODE=households BTB_DATABASE_URL=sqlite:////tmp/btb-csp/btb.db \
  .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8021
node scripts/csp_check.mjs --pin http://127.0.0.1:8020 --hh http://127.0.0.1:8021   # exit 0 = no CSP messages
```

`scripts/csp_check.mjs` uses the system Chrome (`BTB_CHROME`, default `/home/amit/.local/bin/google-chrome`) and playwright-core (`BTB_PLAYWRIGHT`, default `/tmp/pptr/node_modules/playwright-core/index.mjs`). It checks `/` and `/camera.html` in both modes, and fails on any console message or page error mentioning "Content Security Policy", on a missing policy header, or on missing main text. Stop the backends afterwards.

Known gap, not changed here: `/camera` (without `.html`) answers 404 from the static mount (`StaticFiles(html=True)` does not map `/camera` to `camera.html` in this Starlette version). `/camera.html` serves the page. Pre-existing; the check uses `/camera.html`.

Tests: `tests/web/test_security.py` (headers, hashes against a fixture, CSP content, Origin matrix, TLS-only, cookie flag) and `tests/web/test_limits.py` (limiter with a fake clock, and HTTP lockout on `/pair`, `/auth/recover` and `/join`).
