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

## Phone camera

Browsers only allow camera/mic on HTTPS (or localhost). For the demo, expose both servers with
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
