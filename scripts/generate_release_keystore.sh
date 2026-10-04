#!/usr/bin/env bash
# generate_release_keystore.sh
# Generates a production upload keystore (.jks) for Google Play Store / F-Droid release.
set -euo pipefail

KEYSTORE_PATH="${1:-android/release.jks}"
KEY_ALIAS="${2:-btb_release}"
KEY_PASS="${BTB_KEYSTORE_PASSWORD:-behindthebarks}"

if [ -f "$KEYSTORE_PATH" ]; then
    echo "Keystore already exists at $KEYSTORE_PATH. Refusing to overwrite."
    exit 0
fi

echo "==> Generating production release keystore at $KEYSTORE_PATH..."
keytool -genkeypair \
    -v \
    -keystore "$KEYSTORE_PATH" \
    -alias "$KEY_ALIAS" \
    -keyalg RSA \
    -keysize 4096 \
    -validity 10000 \
    -storepass "$KEY_PASS" \
    -keypass "$KEY_PASS" \
    -dname "CN=WagWatch Release, OU=Mobile Perception, O=Behind The Barks, L=San Francisco, ST=CA, C=US"

chmod 600 "$KEYSTORE_PATH"
echo "==> Keystore generated successfully at $KEYSTORE_PATH (permissions set to 600)."
echo ""
echo "To use this keystore for Play Store release builds:"
echo "  export BTB_KEYSTORE_PATH=\"$KEYSTORE_PATH\""
echo "  export BTB_KEYSTORE_PASSWORD=\"$KEY_PASS\""
echo "  export BTB_KEY_ALIAS=\"$KEY_ALIAS\""
echo "  export BTB_KEY_PASSWORD=\"$KEY_PASS\""
echo ""
echo "Then build the Android App Bundle (.aab):"
echo "  cd android && ./gradlew bundleRelease"
