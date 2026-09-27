# UI spec: "Claude Pet" screens (source of truth for Plans 2–4)

Design source: `ui/Claude Pet.html` (a bundled Claude Design export), unpacked into `ui/screens/*.html`.
Each screen file is a template: `{{var}}` placeholders, `<sc-for list=… as=…>` loops and `<sc-if>` blocks, with mock data
and theme values in the `<script type="text/x-dc">` at the bottom. **Open the screen file when you build a
component.** This doc is the map, not a replacement.

| File | Screen |
|---|---|
| `00-dashboard-desktop.html` | Live dashboard, desktop 1440 (same component in dark and at projector 1920 via `theme`/`size` props) |
| `03-dashboard-mobile.html` | Dashboard, mobile 390 |
| `05-camera-setup.html` | `/camera` setup (permission, camera picker, feeding-zone guide) |
| `06-camera-streaming-portrait.html` / `07-…-landscape.html` | `/camera` streaming |
| `08-camera-permission-denied.html` | `/camera` blocked |
| `09-notifications.html` | Toasts + Telegram alert |
| `10-system-states.html` | Reconnecting, no dog, AI offline, demo mode, camera blocked, first load |
| `11-design-tokens.html` | Tailwind v4 `globals.css` (copy verbatim), emotion palette, icons, type scale |

## Tokens (copy from 11-design-tokens.html)
- Fonts: Hanken Grotesk (400/500/600/700) and IBM Plex Mono (400/500). Load them with `next/font/google`.
- `globals.css`: the `:root` / `.dark` variables and the `@theme inline` block exactly as shown in the tokens screen. Dark mode uses the `.dark` class (a theme toggle button in the header, persisted in localStorage, defaulting to `prefers-color-scheme`).
- Every emotion has `solid` (bars, spans), `fg` (text, icons, ≥4.5:1 contrast) and `tint` (fills), for light and dark. Tailwind usage: `bg-anxious-tint text-anxious-fg border-anxious`.
- **Emotion icons:** a 24×24 circle face (`M21 12a9 9 0 1 1-18 0a9 9 0 0 1 18 0z`) plus 3 feature paths per emotion (the `EMO` table in `00-dashboard-desktop.html`). The `unknown` circle uses `stroke-dasharray="2.2 2.4"`. Put these in one `EmotionIcon` component.
- Overlay colours (the same in both themes): keypoints `#7FE3E8`, face `#F5E6B8`, scrim `rgb(15 17 19 / .72)`, live `#FF5A4E`.
- Type scale: display-xl 104 (projector), display 64 (desktop), display-sm 44 (mobile), title 26, heading 17, body 15, small 13, micro 11 caps. Radii 6/10/14/16/20. Minimum touch target 44 px.

## Dashboard, desktop (00)
Column layout, padding 32, gap 20:
1. **Header (48):** logo, "Claude Pet", divider, dog name (`profile.dog_name`) plus "Feeding area · {location}", theme toggle (44 px), and a bell with an unread badge (unread notification count, client-side).
2. **Status bar (40, role=status):** connection dot + "Connected"; the phone device label (from the `/ingest` hello `device` plus camera facing, or `pipeline_status.source`); fps (mono); "AI · {model}" with the last latency in ms (mono), or "AI offline · rules only" when `llm.online` is false or `enabled` is false; a **Live | Demo** segmented toggle (POST `/mode`).
3. **Main grid, 3fr : 2fr:**
   - **Video panel (radius 16, bg #1B1D1F):** MJPEG `/video` image plus a canvas overlay with the bbox (thin rect and thick corner brackets), skeleton lines and dots, face points, and a label chip "dog {bbox_conf}". Top-left: a "LIVE" chip with a red dot and a HH:MM:SS clock, plus a location chip. Bottom-left: "Pose {n} kp · Face {n} pts". Bottom-right: **Box / Skeleton / Face** toggle buttons (aria-pressed).
   - **Right column:**
     - **Emotion card:** a 4 px top bar in `cur.solid`; "RIGHT NOW"; a source badge (Fused / AI / Rules); "updated Ns ago"; the icon tile (88 px, tint); the label at 64 px in `fg`; "since HH:MM · N min" (time the current state began); a confidence meter (8 px, solid); the reason at 17 px.
     - **Treat button:** 60 px, accent, "Treat dropped", `T` key hint. The `T` hotkey posts `/treat` and gives instant feedback.
     - **Signals:** collapsible, default open on desktop and closed on the projector. "6 live · from pose, face and audio". A 3×2 grid: Tail height, Tail wag (Hz), Ear position, Mouth open (%), Body lowering (%), Motion energy. Each shows the value plus a sparkline of the last ~12 samples.
4. **Session timeline card:** title, "HH:MM – now · N min", and the legend (8 icons). Rows: a markers row (treat = accent, owner notified = bell, muted; dashed vertical lines), an **Emotion** row (44 px spans in tint with a 4 px solid top and the icon, selectable), an **Audio** row (chips: bark, whimper, "yip ×2", growl, howl, grouped within ~2 s), and ticks. The **selected-span detail** row shows icon, label, range, reason and "{source} · {conf}%". The last span is selected by default and its range reads "– now".
- Projector variant (`size=projector`): 1920×1080, label 104, icon 128, reason 22, signals collapsed.

## Dashboard, mobile (03)
A single column: header (dog name, "Claude Pet · {location}", bell) → a wrapping row of status chips → emotion card (display-sm 44) → video card (216 px, expand button) → Signals accordion → timeline as a horizontal swipe strip ("Swipe" hint) → a **sticky bottom "Treat dropped" bar**. Bottom padding is 104 px.

## Camera page `/camera` (05–08), dark only, mobile-first
- **Setup (05):** H1 "Use this phone as {dog}’s camera". Three step cards: (1) "Allow camera and microphone" with an **Allow access** button; (2) "Choose camera" as Back (Recommended) / Front (Selfie); (3) "Point at the food bowl" with a live preview and a dashed feeding-zone guide (`left 22% right 12% top 30% bottom 14%`, labelled "Feeding zone") and the instruction "Fit the bowl and about a metre around it inside the dashed zone. Waist height works best." Then **Start streaming** (56 px, accent). Footer: "Paired with dashboard".
- **Streaming (06 portrait / 07 landscape):** a full-bleed preview with the zone rect; a "Streaming" pill (red dot + clock); a connection-quality pill ("Good", 4 bars) from WS RTT / bufferedAmount; an fps pill; a mic meter (14 bars portrait, 10 landscape) from the AudioWorklet RMS; "Keep this screen on · plugged in"; **Stop streaming** (64 px, light) in portrait, an 88 px round **Stop** in landscape.
- **Blocked (08):** "Camera access is blocked" + 3 fix steps + **Try again** / **Continue with microphone only** ("Emotion from sounds only · lower confidence").

## Notifications (09)
- Desktop toasts: top-right, newest on top, width 380, radius 14, `--shadow-toast`. A **negative** toast (anxious, fearful, aggressive, disinterested; role=alert; 3 px top bar in the emotion solid) reads "{dog} seems {emotion}" with a time, the reason, and **View moment** / **Dismiss**. It stays until dismissed. A **positive** toast reads "{dog} is {emotion}", fades after 6 s and has no buttons. A **system** toast (e.g. "AI back online · fused readings resumed") fades after 6 s.
- Status handling: `dashboard_only` → a small "would send to owner" line; `failed` → a "Telegram failed" line plus the detail on hover.
- One toast per state change; repeats within 2 min are grouped (count badge).
- Mobile: a dark top toast with a one-line reason and a dismiss X.
- The Telegram caption (backend) matches the phone mock: "{dog} seems anxious · 77%" / reason / "{location} · {zone} · {Source} reading · HH:MM".

## System states (10)
1. **Reconnecting:** banner "Reconnecting to camera · attempt n of 5 · last frame HH:MM:SS"; the video shows a spinner, "Reconnecting…" and "The camera phone dropped off Wi‑Fi. Keep it on and near the router."; the emotion card greys out (opacity .55) as "Paused · last reading" with "Held since …". Triggers: dashboard WS disconnected, or `pipeline_status.state == "stalled"`.
2. **No dog in view:** a chip "No dog in view · N min"; the card shows the dashed unknown icon, "No dog in view", and "Last seen HH:MM · {last emotion}". The timeline gap is grey; no alert.
3. **AI offline · rules only:** a hollow dot in the status bar, a "Rules" badge, and the footer "AI is unreachable, so readings come from rules and may be less nuanced. Retrying in N s." (N = seconds until the next heartbeat).
4. **Demo mode:** a persistent banner "Demo mode · Playing a recorded clip, not the live camera" with **Go live**, and a clip listbox (name, emotion, duration, progress). Plan 4.
5. **Camera/mic blocked:** "Camera blocked on the camera phone" with **How to fix** / **Use a demo clip**; the row "Microphone off · Vocalisations aren’t analysed · audio strip hidden" with **Enable**.
6. **First load:** "Waiting for {dog}", "Readings start when a dog enters the feeding zone."; an empty timeline with "The timeline fills in as readings arrive" and "Press **Treat dropped** to mark a moment and watch the response."

## Backend fields the UI needs (beyond the contracts)
- `/status`: `profile {dog_name, location, zone_label}`, `llm {enabled, online, provider, model, last_call.latency_ms}`, `fps`, `pipeline_status {source, state, last_frame_age_s, audio_ok}`, `demo_mode`, plus (Plan 3) `phone {connected, device, facing, fps}`.
- WS envelopes: `frame` (bbox, keypoints, face, features), `audio`, `rules`, `emotion` (meta.changed), `llm` (meta.ok), `notification` (state + status), `treat`, `status`.
- `/events` returns history plus `timeline` spans for the first load.
- Out of scope (YAGNI): pairing codes, the "Mute 30 min" Telegram button, and the "attempt n of 5" counter. The dashboard reconnects forever with backoff; the counter shows the attempt number only.
