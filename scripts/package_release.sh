#!/bin/bash
# Build release folder customers can download (ZIP).
# Does NOT notarize (run notarize_mac.sh first for public Gatekeeper-friendly .app).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VER=$(tr -d ' \n' < "$ROOT/VERSION" 2>/dev/null || echo "6.1.0")
OUT="$ROOT/dist/GRS_Observatory_Release_v${VER}"
rm -rf "$OUT"
mkdir -p "$OUT"

echo "Building app…"
"$ROOT/build_mac_app.sh"

# Copy product artifacts
cp -R "$ROOT/dist/GRS_Observatory.app" "$OUT/" 2>/dev/null || true
cp -R "$ROOT/dist/GRS_Observatory_Data" "$OUT/" 2>/dev/null || mkdir -p "$OUT/GRS_Observatory_Data"
mkdir -p "$OUT/docs" "$OUT/models"
cp -f "$ROOT/docs/GRS_CODE_WALKTHROUGH_ESSAY.md" "$OUT/docs/" 2>/dev/null || true
cp -f "$ROOT/docs/SECURITY.md" "$OUT/docs/" 2>/dev/null || true
cp -f "$ROOT/app/models/spire_net_weights.npz" "$ROOT/app/models/spire_net_meta.json" \
  "$ROOT/app/models/spire_net_weights.GOOD.npz" "$OUT/models/" 2>/dev/null || true
cp -f "$ROOT/LICENSE" "$ROOT/VERSION" "$ROOT/README.md" "$OUT/"
cp -f "$ROOT/START_HERE.txt" "$OUT/" 2>/dev/null || true
cp -f "$ROOT/Launch_Desktop.command" "$ROOT/Launch_CLI.command" "$OUT/" 2>/dev/null || true
cp -f "$ROOT/requirements.txt" "$OUT/" 2>/dev/null || true

cat > "$OUT/START_HERE.txt" << EOF
GRS Observatory v${VER}
======================

ONLY GUIDE: docs/GRS_CODE_WALKTHROUGH_ESSAY.md

1. Double-click GRS_Observatory.app
2. Help → The Book (or open the .md file)
3. CNN weights: models/spire_net_weights.npz (included)

Publish = GS-MAP. CM = SPICE / Horizons / WinJUPOS (Book §3).

If macOS blocks the app: right-click → Open once.
EOF

ZIP="$ROOT/dist/GRS_Observatory_v${VER}.zip"
rm -f "$ZIP"
( cd "$ROOT/dist" && zip -ry "GRS_Observatory_v${VER}.zip" "GRS_Observatory_Release_v${VER}" )
echo "Release folder: $OUT"
echo "ZIP: $ZIP"
