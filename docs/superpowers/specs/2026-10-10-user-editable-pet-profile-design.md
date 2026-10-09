# User-editable pet profile — design spec

Date: 2026-10-10 | Status: approved (brainstormed with the user) | Task: T1.8 in
`docs/superpowers/plans/2026-10-10-multi-household/phase-0-1-env-and-quickfixes.md` (appended).

## Goal

Anyone viewing the dashboard can set their pet's name, location and zone label — no YAML edit, no
redeploy, no `BTB_DOG_NAME` env var. Today these are config-only (T1.4 made the defaults neutral).

Decisions locked with the user:

1. **One pet per station** — extend the existing `web.profile` (`dog_name`, `location`, `zone_label`).
   Multiple switchable pets are a Phase 2 (households) concern.
2. **Edited in the dashboard only.** The phone app keeps its current setup; a phone-side editor is a
   follow-up (needs an APK release).
3. **Server-side persistence** in a JSON file, broadcast live to every open dashboard.

## Architecture

### Storage: `backend/web/profile_store.py` (new, single-purpose module)

- File: `<web.log_dir>/profile.json` (default `out/profile.json`), created on first save.
- Atomic write: `tmp` + `os.replace`, so a crash mid-write can never truncate the live file.
- `load(defaults)` → the saved profile merged over the config defaults; a missing or corrupt file
  falls back to the defaults and is logged (never raises).
- `save(profile)` → validates (`dog_name` ≤ 64, `location` ≤ 64, `zone_label` ≤ 40, all strings,
  stripped) and writes atomically.
- Precedence: saved file > `BTB_DOG_NAME`/`BTB_LOCATION` env > YAML > built-in defaults.
- Known limitation: on Render's ephemeral free disk the file is lost on redeploy and the values fall
  back to env/YAML. Documented in the onboarding guide, not solved here (D1/Postgres territory).

### Wiring

- `create_app` lifespan creates the store (`app.state.profile`) from `web["log_dir"]` and merges the
  saved file into `cfg["web"]["profile"]` at startup, so every existing reader (`Runtime.status()`,
  notification captions, the dashboard header) sees the saved values with no further changes.
- `Runtime.status()` already returns `cfg["web"]["profile"]`; the store mutates that dict in place on
  save, so status stays correct.

### API (follows the `/torch` route pattern)

- `GET /profile` → `{"dog_name": ..., "location": ..., "zone_label": ...}` (auth-verified).
- `PUT /profile` → body `{dog_name?, location?, zone_label?}`; validated, saved, `cfg["web"]["profile"]`
  updated in place, then `rt.hub.publish("status", {"profile": {...}})` so every open dashboard
  updates live through the existing status reducer. Invalid field → 400 with a clear message.
  Auth-verified + rate-limited like `/torch` (a mutating route).

### Frontend

- No read-path change: `status.profile` already flows into `state.status.profile`.
- `ProfileModal.tsx` (new) follows `PairingModal`/`AuthModal` conventions: three labelled inputs
  (Pet name, Location, Zone label), Save/Cancel, inline error line. Opened by a pencil button in the
  header next to "Pair Phone".
- `useBackend` gains `saveProfile(patch)` → `PUT /profile` with the stored Bearer token; the live
  broadcast updates state for all viewers (including the saver).
- Optimistic UI: none — the broadcast is the update, so there is one code path and no divergence.
- Failure surfaces through the existing toast system.

### Tests (TDD)

Backend (`tests/web/test_profile.py`):
- store: missing file → defaults; corrupt file → defaults; save+load roundtrip; each validation
  rule (missing key, wrong type, over-length) rejected.
- routes: `GET` returns saved values after a `PUT`; `PUT` publishes a `status` envelope with the new
  profile to a `/ws` subscriber; invalid body → 400; unauthorized → 401 when auth is enabled.

Frontend:
- reducer merges `{profile}` from a partial status envelope (the T1.3 pattern) — one vitest case.
- ProfileModal verified visually (no RTL in this repo): screenshots at 390 px and 1280 px.

### Scope guard

- `/ingest`, `/camera`, the mock/real pipelines and all event contracts are untouched.
- With Phase 2 households the profile becomes per-household; the store's location under `log_dir`
  makes that a per-household directory later.
