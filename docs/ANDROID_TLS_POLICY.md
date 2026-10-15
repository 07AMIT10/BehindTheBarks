# Android TLS policy: cleartext only in debug

Status: in force from the H2 hardening stage (2026-10-11). Applies to the on-device station app in `android/`
(`com.btb.ondevice`). The server side is documented in `docs/privacy_architecture_and_threat_model.md`.

## 1. The rule

| Build | Cleartext (`ws://`, `http://`) | Where it is enforced |
|---|---|---|
| **release** (APK, AAB, anything not debug) | **Refused** | Network security config, `UrlPolicy`, build guard |
| **debug** (`./gradlew assembleDebug`, `installDebug`) | Allowed | Debug network security config only |

Release builds talk only to `wss://` and `https://` servers. Debug builds may also use `ws://` and `http://`
so the adb-reverse dev loop and a laptop on the LAN work. Nothing in the release APK permits cleartext.

## 2. Where each rule lives

Each layer is independent. A bug in one does not open the others.

1. **Platform (network security config).** `android/app/src/main/res/xml/network_security_config.xml` sets
   `cleartextTrafficPermitted="false"` for every domain. The main manifest references it with
   `android:networkSecurityConfig`; `usesCleartextTraffic` is no longer set. The debug source set
   (`android/app/src/debug/res/xml/network_security_config.xml`) overrides the same resource name and sets
   `cleartextTrafficPermitted="true"`. Release APKs never contain the debug file.
2. **App link policy (`UrlPolicy`).** `android/app/src/main/java/com/btb/ondevice/net/UrlPolicy.kt` is pure Kotlin.
   `check(url, allowCleartext)` returns `Allowed` or `Rejected(reason)`. In release, only `wss://` and `https://`
   with a host are accepted (scheme case-insensitive). In debug, `ws://` and `http://` are accepted too. Any
   other scheme, empty text and a link with no host are rejected in both builds. The host is the one
   `java.net.URI` reports: `wss://good.com@evil.test` is judged as `evil.test`.
   The refusal text for a cleartext link is:
   `Insecure server link (ws://). This build only connects over wss://.`
3. **Build guard (Gradle).** `android/app/build.gradle.kts` fails `assembleRelease` and `bundleRelease` before any
   task runs when the default server URL is not `wss://`. See section 4.

### Every place a server link enters or is used

The flag passed to `UrlPolicy` is `BuildConfig.DEBUG` in production code. JVM tests inject it explicitly.

| Entry point | Behaviour in release when the link is `ws://` or `http://` |
|---|---|
| QR scan on the monitor (`MonitorService` QR callback) | Toast with the reason; nothing saved (`QrDecoder.parsePairingText`) |
| Pair by QR / standalone scanner (`ScannerActivity`) | Toast with the reason; the scanner closes; nothing saved |
| Clipboard **Paste** (`MainActivity.handlePaste`) | Toast with the reason; nothing saved |
| `wagwatch://connect?url=...` and `btb://connect?url=...` deep link | Toast with the reason; no confirmation dialog, nothing saved |
| URL text field on **Start monitoring** | Toast with the reason; the monitor does not start; the typed text stays |
| `MonitorService` start (any path, e.g. a stored URL) | Defence in depth: the service shows the reason in its notification and stops |
| Pairing HTTP call (`pair_url`, `StationPairing.pair`) | Refused before any request is sent |
| Pairing payload saved by `PairingFlow` | Both the websocket URL and `pair_url` are checked; nothing is written unless both pass |
| Local Wi-Fi and USB presets | Disabled, labelled "debug builds only" |

The debug-only dev extras (`extra_server_url`, `extra_pair_payload`) are ignored in release, as before.

A release app that still has a `ws://` URL stored from an older build shows that URL in the field and refuses
to start with the message. The user replaces it with a `wss://` link (scan a QR from the dashboard, or paste).

## 3. Build a release (TLS-only)

A release needs a signing keystore (T2.8, see `docs/distribution/RELEASE_GUIDE.md`) and a `wss://` default URL:

```bash
export JAVA_HOME=$HOME/jdk17/jdk-17.0.20.1+1
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME=$HOME/Android/Sdk
cd android
./gradlew assembleRelease bundleRelease \
  -PbtbDefaultIngestUrl=wss://ingest.example.render.com/ingest-events
$ANDROID_HOME/build-tools/35.0.0/zipalign -c -P 16 4 app/build/outputs/apk/release/app-release.apk
```

Without a `wss://` default, the build stops with:
`Refusing to build the release variant: the default server URL is '...', but release builds must default to a wss:// server ...`

`BTB_DEFAULT_INGEST_URL` in the environment does the same job as `-PbtbDefaultIngestUrl`. The property wins if both
are set.

### The unsafe override

`-PbtbAllowCleartextRelease=true` skips the wss check. It exists for local testing of a release build only.
Even with it, the release APK refuses `ws://` and `http://` servers at runtime (layers 1 and 2), so a cleartext
default cannot connect. Never distribute an APK built with this flag.

## 4. Local development still uses cleartext, in debug

The dev loop is unchanged. It needs a debug build, which allows cleartext:

```bash
make android-build                 # debug APK, zipalign check
make android-install               # debug build over USB
scripts/dev_phone_loop.sh start-backend
scripts/dev_phone_loop.sh start-phone   # adb reverse tcp:8000 + START with ws://127.0.0.1:8000/ingest-events
scripts/dev_phone_loop.sh status
```

- The `ws://127.0.0.1` URL works because `adb reverse` forwards the phone's loopback to the laptop.
- A laptop on the same Wi-Fi can be used with `Local Wi-Fi` in debug builds (`ws://<lan-ip>:8000/...`).
- Debug builds still baked in `ws://10.0.2.2:8000/ingest-events` (the emulator's host alias) as the default URL. The
  build guard only applies to release tasks.

Do not install a release build to test local development: it refuses `ws://`, by design.

## 5. Verification

Checks used for H2 (run them again after changing the network config, the policy or the build script):

- Release APK manifest references the config resource, and the config says `cleartextTrafficPermitted="false"`:
  `build-tools/35.0.0/aapt2 dump xmltree --file AndroidManifest.xml app-release.apk` and
  `aapt2 dump resources app-release.apk` to find the `xml/network_security_config` path, then unzip it and decode it.
- Debug APK: same, with `cleartextTrafficPermitted="true"`.
- Unit tests: `UrlPolicyTest` (scheme, case, host, userinfo tricks, empty text) and the parse-path tests in
  `QrDecoderTest` and `PairingTest` (release refuses `ws://` and `http://` and sends no request).
- `zipalign -c -P 16 4` on both APKs.

## 6. Limits of this policy

- It covers the app. The server must still be reached over TLS (Render, or a `cloudflared` tunnel for demos).
- A user can still type a `wss://` URL that points at a server they do not trust. Certificates are checked by
  the system trust store; the app does not pin certificates.
- End-to-end encryption of media is a separate, opt-in design (`docs/superpowers/plans/2026-10-11-audio-and-hardening/PLAN.md`, part C).
