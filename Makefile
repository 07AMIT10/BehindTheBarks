PY ?= .venv/bin/python
ANDROID_BUILD_TOOLS ?= 35.0.0
ANDROID_APK = android/app/build/outputs/apk/debug/app-debug.apk
.PHONY: dev-backend dev-backend-remote dev-frontend dev demo test-web test-frontend tunnel-backend tunnel-frontend android-build android-test android-install
dev-backend:
	$(PY) -m uvicorn backend.main:app --reload --port 8000
dev-backend-remote:
	WEB_PIPELINE=remote $(PY) -m uvicorn backend.main:app --reload --port 8000
dev-frontend:
	cd frontend && npm run dev
dev:
	$(MAKE) -j2 dev-backend dev-frontend
demo:
	DEMO_MODE=1 $(PY) -m uvicorn backend.main:app --port 8000
test-web:
	$(PY) -m pytest -q tests/web
test-frontend:
	cd frontend && npm run test && npm run typecheck && npm run lint

tunnel-backend:
	cloudflared tunnel --url http://localhost:8000
tunnel-frontend:
	cloudflared tunnel --url http://localhost:3000

# Android (on-device perception app, android/). Needs ANDROID_HOME plus an accepted SDK licence.
# After `make android-build`, `make android-install` puts app-debug.apk on the plugged-in phone.
android-build:
	@test -n "$(ANDROID_HOME)" || { echo "ANDROID_HOME is not set. Install the Android SDK (platform-tools, platforms;android-35, build-tools;$(ANDROID_BUILD_TOOLS)), accept the licences with 'sdkmanager --licenses', then export ANDROID_HOME=/path/to/Android/Sdk."; exit 1; }
	cd android && ./gradlew :app:assembleDebug
	$(ANDROID_HOME)/build-tools/$(ANDROID_BUILD_TOOLS)/zipalign -c -P 16 -v 4 $(ANDROID_APK)
android-test:
	@test -n "$(ANDROID_HOME)" || { echo "ANDROID_HOME is not set. Install the Android SDK (platform-tools, platforms;android-35, build-tools;$(ANDROID_BUILD_TOOLS)), accept the licences with 'sdkmanager --licenses', then export ANDROID_HOME=/path/to/Android/Sdk."; exit 1; }
	cd android && ./gradlew :app:testDebugUnitTest
android-install:
	@test -n "$(ANDROID_HOME)" || { echo "ANDROID_HOME is not set. Install the Android SDK (platform-tools, platforms;android-35, build-tools;$(ANDROID_BUILD_TOOLS)), accept the licences with 'sdkmanager --licenses', then export ANDROID_HOME=/path/to/Android/Sdk."; exit 1; }
	cd android && ./gradlew :app:installDebug
