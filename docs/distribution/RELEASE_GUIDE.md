# WagWatch Production Release & Publishing Guide

This guide walks through deploying **WagWatch (Behind The Barks)** to the **Google Play Store** and **F-Droid**, and setting up live **Telegram & WhatsApp** push alerts.

---

## 1. Release Keystore Management

Google Play and F-Droid require release builds signed with a 4096-bit RSA production key.

### Generate a New Keystore
Run the automated generation script:
```bash
export JAVA_HOME=/tmp/opencode/jdk
export PATH="/tmp/opencode/jdk/bin:$PATH"

./scripts/generate_release_keystore.sh android/release.jks
```
This generates a 10,000-day valid keystore with `chmod 600` permissions.

### Build Environment Configuration
Set the following environment variables (or save them in `.env` / Gradle `gradle.properties`):
```bash
export BTB_KEYSTORE_PATH="release.jks"
export BTB_KEYSTORE_PASSWORD="behindthebarks"
export BTB_KEY_ALIAS="btb_release"
export BTB_KEY_PASSWORD="behindthebarks"
```

---

## 2. Building Release Packages

### A. Android App Bundle (`.aab`) for Google Play Store
Google Play requires `.aab` format for all new app submissions:
```bash
export JAVA_HOME=/tmp/opencode/jdk
export PATH="/tmp/opencode/jdk/bin:$PATH"
export ANDROID_HOME=$HOME/Android/Sdk

cd android && ./gradlew bundleRelease
```
Output:
`android/app/build/outputs/bundle/release/app-release.aab` (~52 MB)

Verify signature:
```bash
jarsigner -verify -verbose -certs android/app/build/outputs/bundle/release/app-release.aab | grep -E "jar verified"
```

### B. Production APK (`.apk`) for F-Droid & Direct GitHub Releases
```bash
cd android && ./gradlew assembleRelease
```
Output:
`android/app/build/outputs/apk/release/app-release.apk` (~64 MB)

Verify 16KB page alignment (required by Android 15/16):
```bash
$ANDROID_HOME/build-tools/35.0.0/zipalign -c -P 16 4 android/app/build/outputs/apk/release/app-release.apk
# Must output: Verification successful
```

---

## 3. Google Play Console Submission Checklist

### Store Listing Assets (Located in `fastlane/metadata/android/en-US/`)
- **Title (<= 30 chars)**: `WagWatch: Dog Emotion Monitor`
- **Short Description (<= 80 chars)**: `Contactless on-device dog emotional perception & well-being monitor.`
- **Full Description (<= 4000 chars)**: See [`fastlane/metadata/android/en-US/full_description.txt`](../../fastlane/metadata/android/en-US/full_description.txt).
- **Privacy Policy URL**: Host [`PRIVACY_POLICY.md`](../../PRIVACY_POLICY.md) on your public GitHub Pages or project website.

### Data Safety Form Declarations
When filling out the Google Play Data Safety questionnaire:
1. **Data Collection**: Select **No, the app does not collect or share any user data**.
2. **Device or Other IDs**: None collected.
3. **Camera**: Used strictly **Ephemerally on-device**. No video frames are sent off-device or stored permanently.
4. **Audio / Microphone**: Used strictly **Ephemerally on-device** for acoustic vocalization scoring. Raw audio is never recorded, transmitted, or stored.
5. **Security Practices**:
   - Data in transit: Point-to-point encrypted via TLS/WSS.
   - User deletion requests: All runtime buffers reside in RAM and are discarded immediately upon frame processing.

---

## 4. F-Droid Submission Workflow

WagWatch complies with all F-Droid free software guidelines (Apache 2.0 license, no proprietary tracking binaries, models open/reproducible).

1. Fork `https://gitlab.com/fdroid/fdroiddata`.
2. Add the metadata descriptor from [`metadata/com.btb.ondevice.yml`](../../metadata/com.btb.ondevice.yml) to `metadata/com.btb.ondevice.yml`.
3. Submit a Merge Request titled: `Add com.btb.ondevice (WagWatch)`.

---

## 5. Setting Up Push Notifications

WagWatch supports extensible push notifications via **Telegram** and **WhatsApp**.

### Telegram Setup
1. Message `@BotFather` on Telegram to create a bot: `/newbot`.
2. Copy the bot API token (e.g. `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
3. Send a message to your bot, then get your chat ID from:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Set in your `.env` or `config.yaml`:
   ```bash
   NOTIFY_MODE=telegram
   TELEGRAM_BOT_TOKEN="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
   TELEGRAM_CHAT_ID="987654321"
   ```

### WhatsApp Setup (Meta WhatsApp Cloud API)
1. Go to [developers.facebook.com](https://developers.facebook.com/) and create a Business App under WhatsApp.
2. Under API Setup, obtain:
   - **Temporary/Permanent Access Token** (`WHATSAPP_TOKEN`)
   - **Phone Number ID** (`WHATSAPP_PHONE_NUMBER_ID`)
   - Add your recipient WhatsApp phone number (`WHATSAPP_RECIPIENT_PHONE`, e.g. `15551234567`)
3. Set in your `.env` or `config.yaml`:
   ```bash
   NOTIFY_MODE=whatsapp
   WHATSAPP_TOKEN="EAAB..."
   WHATSAPP_PHONE_NUMBER_ID="1029384756"
   WHATSAPP_RECIPIENT_PHONE="15551234567"
   ```

### Multi-Channel Setup (Broadcast to Both Telegram & WhatsApp)
To send notifications to both channels simultaneously:
```bash
NOTIFY_MODE=multi
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="..."
WHATSAPP_TOKEN="..."
WHATSAPP_PHONE_NUMBER_ID="..."
WHATSAPP_RECIPIENT_PHONE="..."
```
WagWatch dispatches the alerts concurrently across both platforms. If any delivery channel experiences an outage, WagWatch logs the failure and ensures delivery via the available channel.
