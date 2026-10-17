# Multi-household WagWatch: execution pack (index)

Parent plan (evidence, decisions, risks): `../2026-10-10-multi-household-pairing-and-dashboard.md`. Read its sections 1 and 2 once. Every task file in this folder is written to be executable by a smaller agent that has NOT read the parent plan: each task states its context, exact files, ordered steps, tests with concrete assertions, commands and a definition of done.

| File | Phases | Tasks |
|---|---|---|
| `phase-0-1-env-and-quickfixes.md` | 0 environment, 1 quick fixes | T0.1-T0.4, T1.0-T1.7 |
| `phase-2-households-pairing.md` | 2 identity, pairing, isolation | T2.1-T2.10 |
| `phase-3-modes-and-antilurking.md` | 3 viewing modes | T3.1-T3.9 |
| `phase-4-5-6-grid-video-telemetry.md` | 4 multi-station, 5 video, 6 battery/debug | T4.1-T4.4, T5.1-T5.4, T6.1-T6.4 |

Phase 7 (optional hardening) is described in section 7 of the parent plan and has no task files yet; write them only after Phases 2-4 ship.

## Dependency graph

```
T0.1 -> T0.2 -> T0.3 -> T0.4
T1.0 (independent, do first)
T1.1 (needs T0.1) ; T1.2 (backend, independent) -> T1.3 (frontend, needs T1.2 types)
T1.4, T1.5, T1.6, T1.7 independent of each other (T1.6 after T1.3 to avoid merge conflicts)
Phase 2: T2.1 -> T2.2 -> T2.3 -> T2.4 -> T2.5 ; T2.4 -> T2.6 ; T2.2+T2.4 -> T2.7 (blocking suite) ; T2.8 after T2.5 ; T2.9 after T2.3
Phase 3: needs T2.2 (household context) ; T3.1 -> T3.2 -> T3.3 -> T3.4 -> T3.5 -> T3.6 -> T3.8 ; T3.7 (audio spike, needs D3) -> T3.9
Phase 4: needs T2.2 and T3.3 ; Phase 5 needs T4.3 ; Phase 6 needs T4.1
```

## Global rules (apply to every task)

1. Work in `/home/amit/hck/cc/Behind_The_Barks`. Never push, deploy, change the live Render service, or touch the phone's lock screen. Phone/adb steps need the user to have unlocked the phone and allowed USB debugging.
2. Before starting a task and again before finishing, run the baseline for the layers you touched:
   - Backend: `PYTHONPATH=. .venv/bin/pytest tests/web/ -q` (baseline 259 passed on 2026-10-10)
   - Frontend: `npm --prefix frontend test -- --run` (baseline 73 passed) and `npm --prefix frontend run lint` (baseline clean)
   - Android: `cd android && ./gradlew testDebugUnitTest` (needs JDK, see T0.1; baseline recorded by T0.2)
3. Contract changes (anything in `backend/contracts.py`, `android/app/src/main/java/com/btb/ondevice/contracts/Contracts.kt`, `frontend/lib/types.ts` or `frontend/lib/contracts.ts`) must be made in all mirrored files in the same commit. `tests/web/test_kotlin_types.py` and `tests/web/test_ts_types.py` enforce this. Pydantic event models use `extra="forbid"`; the **hello** message is parsed by hand and tolerates extra keys. Downlinks/uplink control messages (`bye`, `mode`, `viewer`, `telemetry`, `challenge`, `auth`) are plain JSON envelopes, not pydantic contracts, unless a task says otherwise.
4. Contract mirrors are generated: after editing `backend/contracts.py` run `python scripts/gen_ts_types.py` and `python scripts/gen_kotlin_types.py` (CLAUDE.md, "Commands"). Make targets that exist: `make test-web`, `make test-frontend`, `make dev-backend-remote`, `make android-build` (assembles and runs zipalign `-P 16`), `make android-test`, `make android-install`. CLAUDE.md's non-negotiable says the Android app may not change an event contract and `/ingest` + `/camera` + mock/real pipelines must keep working: this plan only adds control envelopes, so keep those paths and their tests green.
5. Update `CLAUDE.md` (contracts / protocol sections) in the same commit as a protocol change, as that file requires.
5b. `tests/web/test_isolation.py` is a blocking gate: re-run it after every task that touches
    `backend/main.py`, `backend/web/registry.py`, `hub.py`, `runtime.py` or `remote_pipeline.py`
    (T2.7). A failure there stops the task until it is fixed.
6. Match local style: backend functions are small and synchronous where possible (see `ingest_events.py` docstrings), tests are plain pytest with `TestClient`, fakes like `NoLLM`, `Clock`, `SpyPipeline` (see `tests/web/test_ingest_events.py`, `test_main.py`). Android tests are JUnit 5 + Truth + OkHttp `MockWebServer` (see `EventUploaderTest.kt`). Frontend tests are Vitest next to the file (`phone.test.ts`, `store.test.ts`).
7. One commit per task: `type(scope): summary`, body lists the tests run. End commit messages with the attribution line the harness provides.
8. When a command fails, run `agent-context log-error --cmd "<cmd>" --error "<snippet>"` (repo convention), and fix before moving on. At the end of a task run `agent-context update --agent "<name>" --status "READY_FOR_HANDOFF" --task "<summary>"`.
9. If reality contradicts a task's "Context" section, stop and report the discrepancy instead of improvising; the context was verified on 2026-10-10 against commit `a766e48`.

## Standard dev loop (verified 2026-10-10)

```bash
# backend (serves the built dashboard from frontend/out as well)
WEB_PIPELINE=remote .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
# phone (USB, screen unlocked, USB debugging authorised)
adb reverse tcp:8000 tcp:8000
adb shell am start -n com.btb.ondevice/.ui.MainActivity -a com.btb.ondevice.action.START \
   --es extra_server_url ws://127.0.0.1:8000/ingest-events --es extra_device_name DebugPhone
curl -s localhost:8000/status
# tap Stop Monitoring (find coordinates with uiautomator, do not hard-code)
adb shell uiautomator dump /sdcard/ui.xml && adb shell cat /sdcard/ui.xml   # find the "Stop Monitoring" bounds
adb shell input tap <x> <y>
# dashboard screenshot at phone width (the chrome-devtools MCP is broken on this box)
/home/amit/.local/bin/google-chrome --headless=new --no-sandbox --window-size=390,844 \
   --virtual-time-budget=8000 --screenshot=/tmp/dash.png http://localhost:8000/
# after frontend edits
npm --prefix frontend run build     # static export to frontend/out (restart uvicorn to remount)
# cleanup
adb reverse --remove-all; adb shell am force-stop com.btb.ondevice
```

Known quirks: `am start-foreground-service` on `MonitorService` is rejected (permission); use the activity intent above. The `ACTION_STOP` activity intent does not stop a session the UI does not know about, so tap the button. The simulated phone used in the backend tests is a plain `websockets` client; see `/tmp/sim_stop.py` pattern in T1.2 (recreate it under `scripts/` as part of T0.4).

## Hand-off template (what each executing agent returns)

```
Task: T<id>  Status: done | blocked
Commits: <hashes>
Tests: pytest <n passed>, vitest <n>, lint <ok>, android <n or "not run: reason">
Phone check: <what was observed, with log lines or screenshot paths>, or "n/a"
Deviations from the task text: <none | list with reasons>
Follow-ups discovered: <list>
```
