# Demo runbook

Start-up order, env checklist, how to switch sources, and what to say if something fails.
(Plan 4 will add the "Offline fallback demo" section.)

## Start-up order

1. Backend: `make dev-backend` (http://localhost:8000). Check `curl -s localhost:8000/health` → `{"ok": true}`.
2. Frontend: `cd frontend && npm run dev` (http://localhost:3000). The status bar should read "Connected".
3. Phone (if using the live camera): see "Phone camera" below.

## Env checklist (`.env`, see `.env.example`)

| Variable | Needed for |
|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | LLM interpretations (without them: rules-only labels, "Rules only" in the status bar) |
| `LLM_PROVIDER`, `LLM_VISION` | provider label / whether frames are sent |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | owner notifications (without them: dashboard-only toasts) |
| `NOTIFY_MODE=telegram` | actually send Telegram messages (default is dashboard-only) |

Test each live dependency before the demo: `python scripts/llm_smoke_test.py` and `python scripts/telegram_test.py`.

## Native Android On-Device Perception App (`com.btb.ondevice`)

The native Android app executes the entire perception stack on-device (YOLO26 dog detection, RTMPose AP-10K keypoints, FaceLandmarker, YAMNet audio, and the deterministic rules engine) using LiteRT CPU/XNNPACK, uploading pre-fused `FrameEvent`, `AudioEvent`, and `RulesLabel` envelopes to `/ingest-events`:

1. **Start Remote Backend & Tunnels**:
   ```bash
   make dev-backend-remote  # WEB_PIPELINE=remote
   make tunnel-backend      # cloudflared tunnel --url http://localhost:8000 -> https://<X>.trycloudflare.com
   make tunnel-frontend     # cloudflared tunnel --url http://localhost:3000 -> https://<Y>.trycloudflare.com
   ```
2. **Build and Install App**:
   ```bash
   make android-build android-install
   ```
3. **Run on Phone**:
   - Open the **Behind The Barks** app.
   - Tap **Cloud WSS** (populates `wss://<X>.trycloudflare.com/ingest-events`).
   - Tap **Start Monitoring**.
   - Point the camera at Bruno. The on-screen debug HUD displays the real-time bounding box, body skeleton, and rules emotion badge.
4. **View Live Dashboard**:
   - Open `https://<Y>.trycloudflare.com/?backend=https://<X>.trycloudflare.com` on any laptop/tablet.
   - Status bar shows `Galaxy-A07 · Remote · Live`.
   - Live MJPEG preview streams at ~2 FPS.
   - Bounding boxes, emotion timeline spans, and audio classifications update live.
   - Tap **Give Treat** on the dashboard to test the downlink WebSocket command to the phone.

Troubleshooting:
- If the screen turns off, the camera capture session closes. Keep the phone plugged in (`adb shell svc power stayon true`).
- If you see "No dog visible for more than 2 s", ensure Bruno or a dog photo/video is well-lit and within frame (portrait orientation is automatically rotated upright in memory).

## Browser Phone Camera (Web Fallback)

Browsers only allow camera/mic on HTTPS (or localhost). For the web demo, expose both servers with
quick tunnels, then open the camera page with the backend URL as a parameter:

```bash
make tunnel-backend    # cloudflared tunnel --url http://localhost:8000  -> https://<X>.trycloudflare.com
make tunnel-frontend   # cloudflared tunnel --url http://localhost:3000  -> https://<Y>.trycloudflare.com
```

On the phone, open `https://<Y>.trycloudflare.com/camera?backend=wss://<X>.trycloudflare.com` and follow
the setup: Allow access → Back camera → point at the bowl → Start streaming. The dashboard status bar
should show the phone's device name within ~2 s. No phone handy?
`python scripts/ingest_client.py --duration 60` streams synthetic frames over the same socket.

Troubleshooting:

- "Camera access is blocked": on the phone, tap the site-settings icon next to the address, set Camera
  and Microphone to Allow, come back and tap Try again. Or continue with microphone only.
- Dashboard shows "Reconnecting to camera": the phone dropped off Wi-Fi. Keep it on, plugged in and near
  the router; everything recovers automatically when frames resume. No restart needed.
- Second phone rejected ("Another phone is already streaming"): one phone at a time. Stop the first
  phone's stream first.

## If something fails mid-demo

- LLM errors: the dashboard keeps running on rules-only labels ("AI offline · rules only"). Say:
  "The AI reader dropped out, so you're seeing the on-device rules engine — same signals, less nuance."
- Backend down: the dashboard shows "Reconnecting to Claude Pet · attempt n". Restart the backend;
  the page recovers without a reload.
- Camera dead: switch to the fallback path (Plan 4: demo clips). Until then, restart the phone page.

## Offline fallback demo

1. While online: `.venv/bin/python scripts/precompute_demo.py` (commits fresh timelines), then
   `cd frontend && npm run build` (fonts + pages baked in).
2. Switch the laptop to airplane mode. Start the backend: `DEMO_MODE=1 make dev-backend`.
   Start the frontend: `cd frontend && npm run start` (NOT `dev` — dev mode needs network for HMR).
3. Open http://localhost:3000, flip to **Demo**, pick a clip. Video, emotions, toasts and timeline
   all run from localhost. Full test: airplane mode, full run-through of one clip.

## Failure drills (rehearse once before the demo)

- Kill the network mid-session: dashboard shows "Reconnecting…", keeps the last state greyed, recovers
  without reload. LLM calls fail → "AI offline · rules only".
- Revoke the API key (bad `LLM_API_KEY`): same rules-only fallback; check `llm_smoke_test.py` reports `error`.
- Stop the pipeline / kill the backend: banner + paused card; restart, everything resumes.
- Disconnect the phone page: camera banner appears; reopen the page, streaming resumes.
- WebSocket hard refresh mid-demo: `/events` bootstrap restores timeline + last state.
