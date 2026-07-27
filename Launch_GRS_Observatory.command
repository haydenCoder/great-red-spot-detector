#!/bin/bash
# Web UI launcher — robust to project folder rename/move.
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
# shellcheck disable=SC1091
source "$ROOT/_lib_venv.sh"
export GRS_RAM_GB=16
export GRS_SSD_CACHE="$ROOT/app/ssd_cache"
export GRS_HOST=127.0.0.1
export GRS_PORT=8765

echo "=============================================="
echo " GRS Observatory — FULL ADVANCED UI (web)"
echo " Folder: $ROOT"
echo " http://127.0.0.1:8765"
echo "=============================================="

if ! command -v python3 >/dev/null; then
  echo "python3 required"; read -r -p "Enter to close"; exit 1
fi

PY="$(grs_ensure_venv)" || { read -r -p "Enter to close"; exit 1; }
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$ROOT/requirements.txt"

mkdir -p "$ROOT/app/ssd_cache" "$ROOT/app/outputs" "$ROOT/app/uploads" \
         "$ROOT/app/logs" "$ROOT/app/nasa_cache"
( sleep 1.2; open "http://127.0.0.1:8765/" 2>/dev/null || true ) &

echo "Open http://127.0.0.1:8765  (leave this window open)"
cd "$ROOT/app"
exec "$PY" server.py
