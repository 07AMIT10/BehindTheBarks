# AGENTS.md — Behind The Barks (WagWatch)

This is the developer and coding agent guide for the Behind The Barks repository.

## 1. Project Overview

Behind The Barks (WagWatch) is a real-time, multimodal dog emotional perception and well-being monitoring system. It operates with zero wearables or intrusive collars on the dog.

### Architecture
1. **Android On-Device Perception Station (`android/`)**:
   - Native Kotlin Android app (`com.btb.ondevice`).
   - CameraX at 15 FPS + AudioRecord at 16 kHz.
   - On-device edge ML via Google LiteRT (TFLite):
     - Dog Detection: YOLO26n 320x320 INT8/FP32.
     - Pose Estimation: RTMPose AP-10K (12-17 keypoints).
     - Facial Landmarks: DogFLW landmark predictor.
     - Audio Classifier: YAMNet (bark, whimper, howl, yip, growl).
   - Rules Engine + Feature Extraction running locally on the phone.
   - Pure Java ZXing QR decoder for 1-second camera pairing to the dashboard.
   - Event spooling (`EventSpool`) for offline resilience.
   - Station Mode (`Station Dim`) with `FLAG_KEEP_SCREEN_ON` at 0.01 display brightness to prevent Android Camera HAL sleep while keeping thermals cool.

2. **Python Backend (`backend/`)**:
   - FastAPI server with high-performance WebSocket ingestion (`/ingest-events`).
   - Session & clock-skew compensation manager (`RemoteSession`).
   - Downlink dispatcher (`/torch`, `/camera/flip`, `/privacy`, `/treat`).
   - Multi-tier state fusion (`FusionState`) & optional LLM interpreter.
   - Live MJPEG video stream generation (`/video`).

3. **Web Dashboard (`frontend/`)**:
   - Next.js 16 (App Router) + React 19 + Tailwind CSS + Lucide Icons.
   - Live stream panel with real-time pose and bounding box overlays.
   - Temporal emotion timeline, signal sparklines, and treat dispensing button.
   - Glassmorphic QR pairing modal (`PairingModal`) for zero-configuration phone pairing.

---

## 2. Development & Verification Commands

### Python Backend
```bash
# Run remote backend (for on-device Android phone ingestion)
WEB_PIPELINE=remote .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Run backend unit & contract tests
PYTHONPATH=. .venv/bin/pytest tests/web/
```

### Next.js Frontend
```bash
# Start frontend development server
npm --prefix frontend run dev -- -H 0.0.0.0 -p 3000

# Run frontend test suite (Vitest)
npm --prefix frontend test -- --run

# Run ESLint check
npm --prefix frontend run lint

# Build production bundle
npm --prefix frontend run build
```

### Android App
```bash
# Set required environment paths
export JAVA_HOME=/tmp/opencode/jdk
export PATH="/tmp/opencode/jdk/bin:$PATH"
export ANDROID_HOME=$HOME/Android/Sdk

# Run all 136 unit tests
cd android && ./gradlew testDebugUnitTest

# Build debug APK & verify 16KB ELF alignment
cd android && ./gradlew assembleDebug
$ANDROID_HOME/build-tools/35.0.0/zipalign -c -P 16 4 app/build/outputs/apk/debug/app-debug.apk

# Build production release APK & verify 16KB alignment
cd android && ./gradlew assembleRelease
$ANDROID_HOME/build-tools/35.0.0/zipalign -c -P 16 4 app/build/outputs/apk/release/app-release.apk

# Install to connected phone via ADB
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

---

## 3. Critical Invariants & Rules

1. **JSON Contracts (`backend/contracts.py`)**:
   - All Pydantic models strictly enforce `extra="forbid"`.
   - Never add or remove fields without synchronizing Kotlin types (`android/app/src/main/java/com/btb/ondevice/contracts/Contracts.kt`) and TypeScript types (`frontend/lib/types.ts`).
2. **16KB Page Alignment (Android 15/16)**:
   - Native libraries (`.so`) must remain uncompressed and 16KB page-aligned in the APK (`useLegacyPackaging = false`).
   - Every APK build must be validated with `$ANDROID_HOME/build-tools/35.0.0/zipalign -c -P 16 4 <apk>`.
3. **WebSocket Single-Flight Reconnection**:
   - `EventUploader.connect()` is guarded against concurrent invocations (`CONNECTING` / `CONNECTED`).
   - Android's `ConnectivityManager.NetworkCallback.onAvailable()` fires immediately upon registration; never launch duplicate sockets.
4. **Android Camera HAL Sleep Traps**:
   - When the phone's physical power button is pressed, Android CameraService HAL forcefully closes the camera session even for foreground services.
   - Always instruct users to use **Station Dim** (`🌙 Station Dim`), which turns the display black (0.01 brightness) while maintaining `FLAG_KEEP_SCREEN_ON`.

---

## 4. In-Home Privacy & Anti-Lurking Architecture

See detailed specification in `docs/privacy_architecture_and_threat_model.md`.

1. **Zero Silent Watching (Anti-Lurking Invariant)**:
   - A remote browser must never stream `/video` silently. The on-device Android station plays an audible chime on viewer connection and displays active viewer count (`👁️ N Viewers Active`).
2. **Kinematic-First Modality Separation**:
   - Remote access defaults to behavioral telemetry (emotion state, tail wag frequency, meal events, and skeletal wireframe). Raw optical room video is a privileged secondary tier requiring explicit time-bounded access.
3. **Session Inactivity Watchdog**:
   - All active video streams automatically pause after 180 seconds of continuous viewing.
4. **Local Hardware Shutter**:
   - The station phone possesses absolute sovereignty over media delivery. Activating "Family Privacy" immediately mutes the camera and audio buffers at the local hardware level, returning a privacy slate to remote viewers.

