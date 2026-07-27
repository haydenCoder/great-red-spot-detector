#!/bin/bash
# =============================================================================
#  GRS Observatory — ONE CLICK RUN (free open, no login)
# =============================================================================
# Works even if the project folder was moved (broken .venv paths).
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
export GRS_RAM_GB="${GRS_RAM_GB:-16}"
export GRS_SSD_CACHE="$ROOT/app/ssd_cache"
export GRS_REQUIRE_LOGIN=0

clear 2>/dev/null || true
echo "=============================================="
echo "  GRS Observatory — starting…"
echo "  Folder: $ROOT"
echo "=============================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found."
  echo "Install Python 3 from https://www.python.org/downloads/ then try again."
  read -r -p "Press Enter to close…"
  exit 1
fi

if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true
fi

# shellcheck disable=SC1091
source "$ROOT/_lib_venv.sh"

echo "[1/4] Virtual environment…"
PY="$(grs_ensure_venv)" || {
  read -r -p "Press Enter to close…"
  exit 1
}
echo "  Using: $PY"

echo "[2/4] Dependencies (quiet)…"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$ROOT/requirements.txt" || {
  echo "WARN: some packages failed; trying minimal set…"
  "$PY" -m pip install -q numpy scipy Pillow astropy certifi flask spiceypy || true
}

echo "[3/4] Folders + CNN weights…"
mkdir -p "$ROOT/app/ssd_cache" "$ROOT/app/outputs" "$ROOT/app/uploads" \
         "$ROOT/app/logs" "$ROOT/app/nasa_cache" "$ROOT/app/ephemeris_data" \
         "$ROOT/app/models" "$ROOT/app/owner_access"
cd "$ROOT"
"$PY" - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, "app")
from paths import ensure_models_present
d = ensure_models_present()
w = d / "spire_net_weights.npz"
print(f"  CNN weights: {'OK' if w.exists() else 'MISSING'} → {w}")
missing = []
for m in ("numpy", "scipy", "PIL", "astropy"):
    try:
        __import__(m if m != "PIL" else "PIL")
    except Exception:
        missing.append(m)
if missing:
    print("  WARN: missing imports:", ", ".join(missing))
else:
    print("  Core imports: OK")
PY

echo "[4/4] Launching desktop app (no login)…"
echo ""
cd "$ROOT/app"
# Absolute venv python — never rely on activate/PATH after a folder move
exec "$PY" desktop_app.py
