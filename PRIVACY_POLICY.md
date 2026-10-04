# Privacy Policy — WagWatch (Behind The Barks)

**Effective Date:** October 4, 2026  
**Last Updated:** October 4, 2026

**Behind The Barks / WagWatch** ("we", "our", or "the App") is an open-source, non-invasive, multimodal dog emotional perception and well-being monitoring system. This Privacy Policy details how WagWatch handles permissions, camera feeds, audio recordings, and device data when you use the Android perception station and web dashboard.

---

## 1. Core Principle: 100% On-Device Edge Perception

WagWatch is designed from the ground up on a **Zero-Cloud-Perception** architecture:
- **No Wearables or Collars**: The system operates entirely contactless using ambient computer vision and acoustic perception.
- **Edge ML Execution**: Bounding box dog detection (YOLO26), multi-point skeletal pose estimation (RTMPose AP-10K), facial landmark tracking (DogFLW), and acoustic vocalization classification (Google YAMNet) execute **100% locally on your smartphone’s processor** via Google LiteRT (TensorFlow Lite).
- **No Remote AI Video Processing**: Your dog's live video and microphone streams are never uploaded to remote third-party computer vision cloud APIs.

---

## 2. Permissions & Data Access

The App requests the following Android runtime permissions strictly to perform real-time canine perception:

### A. Camera (`android.permission.CAMERA`)
- **Purpose**: Captures 15 FPS video frames to track canine posture (tail height, wag cadence, spine curvature), facial expressions (mouth open ratio, ear orientation), and spatial location.
- **Data Handling**: Video frames are analyzed transiently in volatile RAM and immediately discarded. Continuous raw video is **never written to device storage** and **never sent to a remote company server**.
- **Live Preview Streaming**: If you choose to connect the phone to your private WagWatch dashboard, momentary MJPEG preview frames are streamed directly point-to-point over your local network or your end-to-end encrypted private tunnel. If Privacy Mode is enabled, the camera stream is instantly replaced with a solid black privacy slate.

### B. Microphone (`android.permission.RECORD_AUDIO`)
- **Purpose**: Samples 16 kHz mono audio to classify canine vocalizations (barks, whimpers, yips, howls, growls).
- **Data Handling**: Audio is processed in sliding in-memory buffers through YAMNet. **Raw audio is never recorded, never saved to disk, and never transmitted over the network.** Only classified event labels and confidence scores (e.g. `{"vocalization": "whimper", "score": 0.82}`) are passed to the rules engine.

### C. Foreground Services (`FOREGROUND_SERVICE_CAMERA`, `FOREGROUND_SERVICE_MICROPHONE`)
- **Purpose**: Ensures the Android OS does not terminate perception when the phone is positioned on a stand or when using **Station Dim Mode**.
- **User Control**: A persistent, user-visible system notification is displayed at all times when monitoring is active, allowing you to stop perception with a single tap.

### D. Network (`android.permission.INTERNET`, `android.permission.ACCESS_NETWORK_STATE`)
- **Purpose**: Transmits lightweight structured JSON telemetry envelopes (derived emotion state, pose keypoint coordinates, acoustic event classifications) to your designated private dashboard.
- **Zero Third-Party Telemetry**: We do not send your IP address, device telemetry, or usage analytics to any cloud marketing or analytics provider.

---

## 3. Station Dim Mode & Device Health

To avoid Android Camera HAL sleep states (which close the camera session when the hardware power button is pressed), WagWatch provides **Station Dim Mode**:
- Sets display brightness to minimum (0.01) with an energy-efficient black screen overlay while maintaining `FLAG_KEEP_SCREEN_ON`.
- Prevents AMOLED screen burn-in and keeps thermal throttling to a minimum.
- No sensor data or background metrics are gathered during Station Dim beyond the explicit canine perception pipeline.

---

## 4. Notifications & Third-Party Messaging (Telegram & WhatsApp)

WagWatch allows you to configure push alerts for negative or high-stress canine states (e.g., severe whining, anxiety, pacing):
- **User-Owned Credentials**: Alerts use your own private Telegram Bot token or Meta WhatsApp Cloud API credentials configured in your local environment.
- **Alert Contents**: Notifications consist solely of a concise text summary (e.g., `😟 Bruno seems anxious · 82%`) and an optional snapshot photo captured at the precise moment of the event.
- **No Middleman**: Alert payloads travel directly from your private backend instance to Telegram’s or Meta’s official APIs without passing through any intermediary Behind The Barks servers.

---

## 5. Third-Party Trackers and Analytics

- **No Ad Networks**: The App contains zero advertising libraries or promotional trackers.
- **No Third-Party Analytics SDKs**: We do not embed Google Analytics for Firebase, Adjust, AppsFlyer, Mixpanel, Meta Pixel, or any behavioral telemetry frameworks.
- **No Data Brokering**: We do not sell, rent, trade, or monetize your dog’s behavioral or biometric data under any circumstances.

---

## 6. Data Storage & Retention

- **Ephemeral Storage**: All high-bandwidth image frames and audio buffers are processed in volatile device memory (RAM) and freed immediately after feature extraction.
- **Local Spooling**: In the event of temporary Wi-Fi disconnection, an encrypted circular buffer (`EventSpool`) stores up to 256 lightweight JSON telemetry events locally in app-private sandbox storage. These are flushed upon reconnection and automatically overwritten if capacity is reached.
- **Right to Delete**: Uninstalling the app completely purges all local sandbox files and cached settings.

---

## 7. Children's Privacy (COPPA Compliance)

WagWatch is a pet well-being monitoring application and is not directed at children under the age of 13. We do not knowingly collect or solicit personal information from children.

---

## 8. Open Source & Independent Audit

Behind The Barks is open source. All native Kotlin perception code, LiteRT neural models, backend state fusion algorithms, and web dashboards are publicly inspectable and auditable in the project repository:
[https://github.com/BehindTheBarks/Behind_The_Barks](https://github.com/BehindTheBarks/Behind_The_Barks)

---

## 9. Contact Us

If you have questions, feedback, or security inquiries regarding this Privacy Policy, please open an issue on GitHub or reach out to:

**Behind The Barks Team**  
Email: `privacy@behindthebarks.org`  
GitHub: [https://github.com/BehindTheBarks/Behind_The_Barks/issues](https://github.com/BehindTheBarks/Behind_The_Barks/issues)
