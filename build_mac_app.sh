#!/bin/bash
# Build native macOS .app (no browser) via PyInstaller
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"

echo "=============================================="
echo " Building GRS Observatory.app (native desktop)"
echo "=============================================="

if ! command -v python3 >/dev/null; then
  echo "python3 required"
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python -m pip install -q pyinstaller pillow

mkdir -p dist build app/outputs app/ssd_cache

# Collect pure-Python modules that must ship inside the bundle
# (grs_complete_system is large but required)
APP_ENTRY="app/desktop_app.py"

echo "Running PyInstaller…"
pyinstaller \
  --noconfirm \
  --windowed \
  --name "GRS_Observatory" \
  --osx-bundle-identifier "com.grs.observatory.desktop" \
  --paths app \
  --add-data "app/models:models" \
  --add-data "app/ephemeris_data:ephemeris_data" \
  --hidden-import numpy \
  --hidden-import scipy \
  --hidden-import scipy.ndimage \
  --hidden-import scipy.signal \
  --hidden-import PIL \
  --hidden-import PIL.Image \
  --hidden-import PIL.ImageTk \
  --hidden-import certifi \
  --hidden-import grs_complete_system \
  --hidden-import synthetic_hq \
  --hidden-import precision_engine \
  --hidden-import research_grade \
  --hidden-import vlbi_metrology \
  --hidden-import ephemeris_pro \
  --hidden-import multi_epoch \
  --hidden-import hard_synth_suite \
  --hidden-import nasa_compare \
  --hidden-import nn_grs \
  --hidden-import ram_ssd \
  --hidden-import verbose_log \
  --hidden-import desktop_pipeline \
  --hidden-import product_core \
  --hidden-import license_manager \
  --hidden-import spice_auto \
  --hidden-import paths \
  --hidden-import group_access \
  --hidden-import cli \
  --hidden-import winjupos_twin \
  --hidden-import publish_primary \
  --hidden-import gold_standard \
  --hidden-import sota_accuracy \
  --hidden-import all_methods \
  --hidden-import all_methods_extra \
  --hidden-import fits_time \
  --add-data "docs:docs" \
  --add-data "VERSION:." \
  --add-data "LICENSE:." \
  --collect-submodules numpy \
  --collect-submodules scipy \
  "$APP_ENTRY"

# Writable data folder next to the .app for first run
DATA_DIR="$ROOT/dist/GRS_Observatory_Data"
mkdir -p "$DATA_DIR"/{outputs,uploads,ssd_cache,nasa_cache,logs,ephemeris_data,docs}
if [ -f app/ephemeris_data/winjupos_cm_template.csv ]; then
  cp -f app/ephemeris_data/winjupos_cm_template.csv "$DATA_DIR/ephemeris_data/" 2>/dev/null || true
fi
# Ship THE book + CNN weights next to data
cp -f docs/GRS_CODE_WALKTHROUGH_ESSAY.md "$DATA_DIR/docs/" 2>/dev/null || true
mkdir -p "$DATA_DIR/models"
cp -f app/models/spire_net_weights.npz app/models/spire_net_meta.json \
  app/models/spire_net_weights.GOOD.npz app/models/MODELS_README.txt \
  "$DATA_DIR/models/" 2>/dev/null || true
cp -f VERSION LICENSE "$DATA_DIR/" 2>/dev/null || true

APP_PATH="$ROOT/dist/GRS_Observatory.app"
if [ -d "$APP_PATH" ]; then
  # Embed short version string if possible
  if [ -f VERSION ]; then
    VER=$(tr -d ' \n' < VERSION)
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VER" \
      "$APP_PATH/Contents/Info.plist" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VER" \
      "$APP_PATH/Contents/Info.plist" 2>/dev/null || true
  fi
  echo ""
  echo "SUCCESS"
  echo "  App:  $APP_PATH"
  echo "  Data: $DATA_DIR"
  echo "  Book:   $DATA_DIR/docs/GRS_CODE_WALKTHROUGH_ESSAY.md"
  echo "  CNN:    $DATA_DIR/models/spire_net_weights.npz"
  echo ""
  echo "Next (for selling outside your Mac):"
  echo "  1) export CODESIGN_IDENTITY='Developer ID Application: …'"
  echo "  2) ./scripts/notarize_mac.sh"
  echo "  3) ./scripts/package_release.sh"
  echo ""
  echo "Double-click GRS_Observatory.app to run (no browser)."
  open "$ROOT/dist" 2>/dev/null || true
else
  echo "Build finished but .app not found — check dist/"
  ls -la dist/ || true
  exit 1
fi
