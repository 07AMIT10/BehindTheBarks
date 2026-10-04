# 🐾 WagWatch — User Onboarding & Setup Guide

Welcome to **WagWatch (Behind The Barks)**! WagWatch transforms any spare Android phone into an intelligent, contactless canine emotional perception and well-being monitor.

This guide walks you through everything from the moment you install the app to 24/7 autonomous monitoring.

---

## ⚡ Quick Start (3-Minute Setup)

1. **Install**: Open `app-release.apk` on your Android phone and grant **Camera** and **Microphone** permissions.
2. **Position**: Set the phone on a stand or prop it up near your dog's bed, feeding bowl, or play area. Connect it to a charger.
3. **Open Dashboard**: On your laptop, tablet, or another phone, open your WagWatch web dashboard (e.g. `http://<your-ip>:3000`).
4. **Pair via QR Code**: Tap **📷 Scan QR** in the Android app and point the camera at the dashboard screen. It connects in under 1 second!
5. **Start Station**: Tap **Start Monitoring**, then tap **🌙 Station Dim** to turn the screen pitch black while perception runs in the background.

---

## 📱 Step 1: Installation & Permissions

### Installing the App
1. Download `app-release.apk` from the [Latest GitHub Release](https://github.com/07AMIT10/BehindTheBarks/releases).
2. Tap the downloaded file. If Android prompts `Install unknown apps`, toggle **Allow from this source**.
3. Tap **Install** and open **Behind The Barks**.

### Required Permissions & Why We Need Them
* **Camera (`android.permission.CAMERA`)**:
  * Runs real-time dog posture tracking (YOLO26 bounding box, RTMPose skeletal keypoints, DogFLW facial expressions).
  * **Privacy Guarantee**: All AI models execute **100% locally in device RAM**. No video is ever stored to disk or uploaded to any third-party cloud.
* **Microphone (`android.permission.RECORD_AUDIO`)**:
  * Analyzes sound bursts (16 kHz) through Google YAMNet to classify dog vocalizations (barks, whimpers, yips, howls, growls).
  * **Privacy Guarantee**: Raw audio is never recorded, saved, or transmitted across the network. Only classification labels are processed.
* **Notifications & Foreground Service**:
  * Keeps the perception station alive when you leave the phone unattended. A persistent system notification confirms the camera is active.

---

## 📐 Step 2: Placement & Room Setup

For the highest perception accuracy:
1. **Distance**: Place the phone **1.5 to 3.5 meters (5 to 12 feet)** away from your dog's primary resting or eating spot.
2. **Elevation**: Position the phone around dog-chest or head height (30 to 60 cm off the floor) angled slightly downward. A cheap phone tripod, kickstand case, or heavy mug works great.
3. **Lighting**: Ensure moderate ambient lighting. In low light or nighttime conditions, you can remotely toggle the phone's **Flashlight/Torch** from the dashboard.
4. **Power**: Keep the phone plugged into its wall charger for continuous operation.

---

## 🔗 Step 3: Pairing with Your Dashboard

WagWatch uses high-speed WebSocket telemetry. There are three easy ways to pair your phone to the dashboard:

### Method A: 1-Second QR Code Pairing (Easiest)
1. On your computer or tablet, open your WagWatch dashboard (`http://localhost:3000` or your network address).
2. Click the **📱 Pair Phone** button in the top navigation bar. A QR code will pop up.
3. In the Android app, tap the green **📷 Scan QR** button.
4. Point your phone at the computer screen. The URL will instantly fill in, and a success confirmation will appear.
5. Tap **Start Monitoring**!

### Method B: Local Wi-Fi Pairing
If your computer and phone are connected to the same home Wi-Fi network:
1. Find your computer's local IP address (e.g. `192.168.1.50`).
2. In the Android app, tap **Local Wi-Fi**.
3. Type in your computer's IP address and tap **Set URL**.
4. The URL field will update to `ws://192.168.1.50:8000/ingest-events`.
5. Tap **Start Monitoring**.

### Method C: Remote / Cloud Access (When You're Away From Home)
If you are running the backend behind a Cloudflare Tunnel or Tailscale:
1. Tap the blue **Cloud WSS** button.
2. Enter your tunnel domain (e.g. `your-tunnel.trycloudflare.com`).
3. Tap **Set URL**. The app will configure `wss://your-tunnel.trycloudflare.com/ingest-events`.

---

## 🌙 Step 4: Station Mode & "Station Dim" (CRITICAL)

> [!WARNING]
> **Do NOT press the phone's physical hardware power button to turn off the screen.**
> 
> In Android, pressing the physical power button triggers the system Camera HAL to forcefully shut down the camera sensor—even if a foreground service is active.

### How to use Station Dim Mode:
1. Once monitoring is active, tap the **🌙 Station Dim** button.
2. The phone display will immediately turn **pitch black** at minimum brightness (0.01).
3. The phone activates `FLAG_KEEP_SCREEN_ON`, keeping CameraX, LiteRT neural models, and audio classification running at full speed without sleeping.
4. **Benefits**:
   * **Zero screen burn-in** on AMOLED displays.
   * **Low thermals**: CPU/GPU runs cool because screen rendering is paused.
   * **Battery preservation**: Uses minimal power while plugged in.
5. **To exit Station Dim**: Simply double-tap anywhere on the black screen to bring back the controls and live camera viewfinder.

---

## 📊 Step 5: Reading the Live Screen & Overlays

When controls are expanded, the on-screen heads-up display shows real-time diagnostics:

* **Green Bounding Box**: YOLO26 dog detection confidence score (e.g. `Dog: 94%`).
* **Skeletal Points & Bones**: RTMPose 17 keypoints tracking spine alignment, tail base, tail tip, paws, and ears.
* **Facial Dots**: DogFLW 46-point landmark mesh mapping muzzle position, mouth opening, and ear posture.
* **Floating Top Pill**:
  * Tap **🐾 Behind The Barks · Tap to expand** at any time to collapse the controls for a clean kiosk view.
* **Telemetry HUD**:
  * **FPS**: Current camera analysis frame rate (~12–15 FPS).
  * **Latency**: Time in milliseconds for complete on-device neural inference (~45–60 ms).
  * **Spool**: Indicates whether events are spooling to local RAM during temporary network disconnects (`0` = healthy real-time streaming).

---

## 🎮 Step 6: Remote Downlinks from the Web Dashboard

While you are away or on the couch, the web dashboard gives you full remote control over the phone station:

| Control Button | Action | Real-World Use Case |
| :--- | :--- | :--- |
| 🔦 **Torch** | Turns the phone's rear LED flash ON / OFF | Illuminate a dark room at night to check on your dog without turning on room lights. |
| 🔄 **Flip Camera** | Swaps between rear camera and front selfie camera | Useful if your phone stand requires placing the screen facing the dog. |
| 🛡️ **Privacy Mode** | Instantly mutes camera stream with black slate | Mute the video feed when family members or house guests enter the room. |
| 🍖 **Dispense Treat** | Plays an acoustic treat chime on the phone | Alert your dog to an automatic feeder or reinforce calm behavior remotely. |

---

## 📲 Step 7: Push Alerts via Telegram & WhatsApp (Optional)

You can receive real-time notifications with snapshot photos on your phone whenever your dog displays signs of distress, anxiety, or prolonged whimpering.

### Setting Up Telegram
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot` and follow instructions to get your **Bot API Token**.
3. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat.id`.
4. In your backend `.env` file, add:
   ```bash
   NOTIFY_MODE=telegram
   TELEGRAM_BOT_TOKEN="your_bot_token_here"
   TELEGRAM_CHAT_ID="your_chat_id_here"
   ```
5. Test it: `.venv/bin/python scripts/notify_test.py --mode telegram`

### Setting Up WhatsApp
1. Obtain Meta WhatsApp Cloud API credentials from [developers.facebook.com](https://developers.facebook.com/).
2. In your backend `.env` file, add:
   ```bash
   NOTIFY_MODE=whatsapp
   WHATSAPP_TOKEN="your_token"
   WHATSAPP_PHONE_NUMBER_ID="your_phone_number_id"
   WHATSAPP_RECIPIENT_PHONE="15551234567"
   ```
3. Test it: `.venv/bin/python scripts/notify_test.py --mode whatsapp`

---

## 🔋 Device Health & 24/7 Operation Tips

* **Battery Protection**: On Samsung, Google Pixel, or Xiaomi phones, enable **Protect Battery** / **Charge limit to 80%** in Android Settings (`Settings > Battery > Battery Protection`). This protects battery health when keeping the phone plugged in 24/7.
* **Do Not Disturb**: Turn on **Do Not Disturb** mode on the station phone so incoming personal phone calls or ringtones don't startle your pet.
* **Disable Auto-Lock**: Station Dim mode handles screen sleep automatically, but you can also set `Screen timeout` to 10 minutes or enable Developer Options `Stay awake while charging`.

---

## ❓ Frequently Asked Questions & Troubleshooting

### Q: Why did the camera stop when I pressed the phone's power button?
**A:** This is standard Android behavior: the operating system forcibly revokes camera hardware access whenever the physical power button is pressed. Always use the in-app **🌙 Station Dim** button instead of the physical button.

### Q: What if my home Wi-Fi drops temporarily?
**A:** WagWatch features built-in **circular event spooling (`EventSpool`)**. When Wi-Fi disconnects, the phone buffers up to 256 emotion events locally in RAM and automatically flushes them to your dashboard the moment the connection recovers.

### Q: Can I use an old Android phone with Android 8 or 9?
**A:** Yes! WagWatch supports `minSdk 26` (Android 8.0 Oreo and newer). It is fully optimized for 3 GB / 4 GB Android Go and budget handsets.

### Q: Is my dog's video being saved to the cloud?
**A:** **No.** All computer vision (YOLO26, RTMPose, DogFLW) runs locally on the phone's processor. Video frames are processed in volatile RAM and immediately discarded. Continuous video is never saved to disk or sent to any company server.
