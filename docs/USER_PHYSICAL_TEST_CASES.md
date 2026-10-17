# 🐾 WagWatch Physical Hardware & Pet Edge-Case Test Guide

This document defines physical, environmental, and behavioral edge-case test protocols for **WagWatch** running on the Android Station phone and the Dell Inspiron self-hosted dashboard ([watch.furryfolks.co](https://watch.furryfolks.co)).

These edge cases cannot be fully simulated in synthetic unit tests because they involve physical acoustics, biological pet behavior, room lighting physics, thermal throttling, and hardware sensors.

---

## 📋 Test Matrix Overview

| Test ID | Category | Objective | Target Edge Condition |
| :--- | :--- | :--- | :--- |
| **TC-01** | **Pairing & Onboarding** | Single-Action QR Pairing | Pair station in < 5 seconds; verify absence of legacy options |
| **TC-02** | **Bioacoustics & Privacy** | Real Bark vs. Background Noise | Authenticate dog bark vs human conversation / TV dog noise |
| **TC-03** | **Audio Snippet Playback** | 5-Second Rolling Buffer Capture | Capture 2s pre-trigger + 3s post-trigger PCM audio on bark |
| **TC-04** | **Multi-Sensory Cues** | Squeak + Flashlight Pulse Sync | Trigger soundboard chime with synchronized LED double-blink |
| **TC-05** | **Lighting Physics** | Low-Light & Night Vision Shader | Transition room from bright daylight to pitch black (<15 lux) |
| **TC-06** | **Physical Station Rig** | Kickstand Gravity & Lens Knock-over | Screen flip stability, camera occlusion, and screen dimming |
| **TC-07** | **Power & Thermals** | 24/7 Continuous AC Unplug | Battery drop alert over WebSocket, thermal throttling under load |
| **TC-08** | **Network Resilience** | Wi-Fi Drop & Auto-Reconnect | Router power cycle / Wi-Fi range drop with zero manual intervention |
| **TC-09** | **Mobile PWA** | Standalone Fullscreen Dashboard | iOS Safari & Android Chrome "Add to Home Screen" experience |

---

## 🧪 Detailed Test Execution Protocols

### TC-01: Single-Action QR Pairing
- **Prerequisites**: Android app clean install or after tapping `Unpair Station`.
- **Test Steps**:
  1. Open WagWatch app on Android station phone.
  2. Verify the screen is clean: only **"🔗 Pair by QR"** is shown as the primary button. The old buttons (*Scan QR*, *Paste*, *Cloud WSS*, *Local Wi-Fi*) must NOT be visible.
  3. On your laptop/phone, open `https://watch.furryfolks.co` and tap **"Pair Station"** to display the QR code.
  4. Tap **"🔗 Pair by QR"** on the Android phone.
  5. Scan the QR code from the screen.
- **Pass Criteria**:
  - [ ] App immediately registers ECDSA key, receives token, displays household name and `Paired · Ready`.
  - [ ] Web dashboard automatically dismisses pairing modal and displays live station tile.
  - [ ] Debug builds display only a subtle `🛠️ USB Test` preset.

---

### TC-02: Bioacoustics Isolation (Real Bark vs. False Positives)
- **Prerequisites**: Station placed on kickstand in living room, monitoring active.
- **Test Scenarios**:
  1. **Positive Trigger**: Have dog bark (or play high-fidelity recorded canine bark at normal room volume).
  2. **Negative Rejection (Human Speech)**: Hold an animated human conversation right next to the phone.
  3. **Negative Rejection (Household Noise)**: Drop a metal spoon on tile or play TV background dialogue.
  4. **Negative Rejection (TV Dog Sounds)**: Play a TV show containing low-fidelity background dog barking.
- **Pass Criteria**:
  - [ ] Real canine bark triggers an instantaneous `Bark` event with confidence > 75%.
  - [ ] Human speech and household noise are classified as `Normal` / `Silence` with zero false bark alerts.
  - [ ] Optical and acoustic frame privacy shields never broadcast raw audio during non-bark periods.

---

### TC-03: Audio Snippet Playback & Instant Replay
- **Prerequisites**: Dashboard open on phone or desktop; dog station monitoring.
- **Test Steps**:
  1. Wait for dog to bark or make a whimper/howl.
  2. Observe the event pill appear in the dashboard activity timeline.
  3. Verify a playable pill `▶ [Listen 0:05]` appears next to the bark event.
  4. Tap the audio pill.
- **Pass Criteria**:
  - [ ] Audio snippet plays smoothly with audible pre-trigger sound (you can hear what prompted the bark).
  - [ ] Audio is clean 16kHz PCM/WAV without digital clipping or buffering stutter.
  - [ ] Snippet file is stored securely on the Dell SSD without leaking private audio.

---

### TC-04: Multi-Sensory Cue (Squeak / Whistle + Flashlight Pulse)
- **Prerequisites**: Dog is resting or looking away from the camera in a dim or normal room.
- **Test Steps**:
  1. In the web dashboard, open the **Soundboard & Light Cue** drawer.
  2. Tap **"Attention Squeak + Light Beacon"**.
  3. Observe the station phone and dog's reaction.
- **Pass Criteria**:
  - [ ] Phone emits squeaky toy chime through device speaker.
  - [ ] Camera LED flash pulses simultaneously (e.g. 2 short 150ms pulses) without interrupting CameraX video streaming.
  - [ ] Camera stream does NOT freeze or stutter during flash pulse.
  - [ ] Dog perks ears or looks towards the camera lens.

---

### TC-05: Ambient Lighting Physics & Night Vision Shader
- **Prerequisites**: Evening or dark room test.
- **Test Steps**:
  1. Place station in room and turn off all overhead room lights (pitch dark / low moonlight).
  2. Check the live video stream on the dashboard.
  3. Toggle **"🌙 Night Vision"** mode on the video panel.
  4. If room is too dark (< 5 lux), tap **"💡 Soft Torch"** on the dashboard.
- **Pass Criteria**:
  - [ ] When room darkens, Night Vision shader boosts midtone luminance and edge contrast, allowing pet posture to remain identifiable.
  - [ ] Soft Torch engages camera torch at low intensity without blinding the pet or blowing out the sensor white balance.

---

### TC-06: Kickstand Orientation, Gravity Sensor, & OLED Screen Dimming
- **Prerequisites**: Android station on battery or charger.
- **Test Steps**:
  1. Place phone in landscape orientation on a kickstand or against a wall.
  2. Rotate phone 180 degrees (charging port on opposite side).
  3. Leave app running continuously for 45 seconds without touching the screen.
- **Pass Criteria**:
  - [ ] Video stream maintains upright orientation without spinning or inversion.
  - [ ] After 30 seconds of inactivity, phone display dims to 0.01 brightness (black screen saver) to eliminate battery consumption and OLED burn-in.
  - [ ] Tapping anywhere on the screen instantly restores UI controls.

---

### TC-07: 24/7 Continuous Power Loss & Thermal Stress
- **Prerequisites**: Station phone connected to AC wall charger for > 2 hours.
- **Test Steps**:
  1. Check Dell server health monitor (`btb-health`): ensure CPU temperature < 70°C and RAM usage stable.
  2. Physically unplug the USB charging cable from the Android station phone.
  3. Observe dashboard status card.
- **Pass Criteria**:
  - [ ] Station telemetry immediately reflects `Battery: discharging` and battery percentage drop.
  - [ ] When battery falls below 20%, dashboard displays low battery warning badge.
  - [ ] Station phone remains cool to the touch (< 42°C battery temp) under continuous video streaming.

---

### TC-08: Wi-Fi Drop & Background Auto-Reconnect
- **Prerequisites**: Active live stream running between phone and server.
- **Test Steps**:
  1. Turn off home Wi-Fi router (or toggle Android Wi-Fi off for 60 seconds, then back on).
  2. Observe station phone UI and web dashboard.
- **Pass Criteria**:
  - [ ] Dashboard displays `Station Offline / Reconnecting...` without crashing.
  - [ ] Android Station enters exponential backoff (1s, 2s, 4s, 8s) and reconnects automatically when Wi-Fi is restored.
  - [ ] Live MJPEG stream and WebSocket telemetry resume automatically within 3 seconds of reconnection without requiring any app restart.

---

### TC-09: Mobile PWA Standalone Experience
- **Prerequisites**: Mobile browser (iOS Safari or Android Chrome) navigated to `https://watch.furryfolks.co`.
- **Test Steps**:
  1. On Safari iOS: Tap **Share** ➔ **"Add to Home Screen"**.
  2. On Android Chrome: Tap **Menu** ➔ **"Install App"** / **"Add to Home screen"**.
  3. Close browser and tap the new **WagWatch** icon on your home screen.
- **Pass Criteria**:
  - [ ] App launches in standalone mode (no browser address bar, no navigation buttons).
  - [ ] Status bar is styled with dark theme `#0B0F19`.
  - [ ] App is fast, responsive, and looks indistinguishable from a native mobile application.
