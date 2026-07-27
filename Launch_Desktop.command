#!/bin/bash
# Run native desktop app from source (no browser, free open)
# Robust to project folder rename/move (broken .venv paths).
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
# shellcheck disable=SC1091
source "$ROOT/_lib_venv.sh"
export GRS_RAM_GB=16
export GRS_SSD_CACHE="$ROOT/app/ssd_cache"
export GRS_REQUIRE_LOGIN=0

echo "=============================================="
echo " GRS Observatory — native desktop (no web)"
echo " Folder: $ROOT"
echo "=============================================="

if ! command -v python3 >/dev/null; then
  echo "python3 required"; read -r -p "Enter to close"; exit 1
fi

PY="$(grs_ensure_venv)" || { read -r -p "Enter to close"; exit 1; }
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$ROOT/requirements.txt"

mkdir -p "$ROOT/app/ssd_cache" "$ROOT/app/outputs" "$ROOT/app/uploads" \
         "$ROOT/app/logs" "$ROOT/app/nasa_cache"
cd "$ROOT/app"
exec "$PY" desktop_app.py
