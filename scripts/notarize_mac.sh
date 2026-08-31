#!/bin/bash
# Sign + notarize GRS_Observatory.app for distribution outside Gatekeeper blocks.
#
# Prerequisites (you must own these — this script cannot invent them):
#   1) Apple Developer Program membership
#   2) Developer ID Application certificate in Keychain
#   3) App-specific password stored in keychain as AC_PASSWORD profile
#
# Setup (once):
#   xcrun notarytool store-credentials "GRS_NOTARY" \
#     --apple-id "you@email.com" \
#     --team-id "YOURTEAMID" \
#     --password "app-specific-password"
#
# Usage:
#   export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   ./scripts/notarize_mac.sh
#   # or after build:
#   ./scripts/notarize_mac.sh /path/to/GRS_Observatory.app
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/dist/GRS_Observatory.app}"
ID="${CODESIGN_IDENTITY:-}"
PROFILE="${NOTARY_PROFILE:-GRS_NOTARY}"
BUNDLE_ID="com.grs.observatory.desktop"

if [[ ! -d "$APP" ]]; then
  echo "ERROR: app not found: $APP"
  echo "Build first: ./build_mac_app.sh"
  exit 1
fi

if [[ -z "$ID" ]]; then
  echo "ERROR: set CODESIGN_IDENTITY to your Developer ID Application certificate name."
  echo "List certs: security find-identity -v -p codesigning"
  exit 1
fi

echo "Signing $APP with: $ID"
# Deep sign frameworks/dylibs then the app
codesign --force --deep --options runtime --timestamp \
  --sign "$ID" \
  --entitlements "$ROOT/scripts/entitlements.plist" \
  "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"
spctl -a -vv "$APP" 2>&1 || true

ZIP="$ROOT/dist/GRS_Observatory_notarize.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
echo "Submitting to Apple notary service (profile=$PROFILE)…"
xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait

echo "Stapling ticket…"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl -a -vv "$APP"

echo ""
echo "SUCCESS — notarized app ready to sell:"
echo "  $APP"
echo "Ship with: dist/GRS_Observatory_Data/ and docs/GRS_CODE_WALKTHROUGH_ESSAY.md"
