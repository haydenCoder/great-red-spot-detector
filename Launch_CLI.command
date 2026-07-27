#!/bin/bash
# CLI launcher — uses project venv (not bare system python3).
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
# shellcheck disable=SC1091
source "$ROOT/_lib_venv.sh"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 required"; read -r -p "Enter to close"; exit 1
fi

PY="$(grs_ensure_venv)" || { read -r -p "Enter to close"; exit 1; }
# Install deps if venv was just rebuilt (import check)
if ! "$PY" -c "import numpy" 2>/dev/null; then
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r "$ROOT/requirements.txt" || true
fi

cd "$ROOT/app"

echo "GRS Observatory CLI — examples:"
echo "  \"$PY\" cli.py version"
echo "  \"$PY\" cli.py eph \"2026-07-14 12:00:00\""
echo "  \"$PY\" cli.py synth --mode metrology --res 1080p"
echo "  \"$PY\" cli.py certify --n 30"
echo ""
"$PY" cli.py version
echo ""
echo "Venv Python: $PY"
echo "Type commands above, or exit to close."
export PATH="$(dirname "$PY"):$PATH"
exec "$SHELL"
