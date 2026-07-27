#!/bin/bash
# Shared venv helper for GRS Observatory launchers.
# Source after: ROOT="$(cd "$(dirname "$0")" && pwd)"
# Usage: PY="$(grs_ensure_venv)"   # path only on stdout

grs_venv_path_ok() {
  # activate hardcodes the creation path; accept any quoting / cygpath form
  # if the current absolute .venv path appears in the file.
  local act="$1/bin/activate"
  local expect="$1"
  [ -f "$act" ] || return 1
  grep -Fq "$expect" "$act"
}

grs_ensure_venv() {
  local venv="${ROOT}/.venv"
  local py="${venv}/bin/python"
  local need_rebuild=0

  if [ ! -x "$py" ]; then
    need_rebuild=1
  elif ! grs_venv_path_ok "$venv"; then
    echo "Note: .venv path is stale (folder was moved). Rebuilding…" >&2
    need_rebuild=1
  elif ! "$py" -c "import sys" 2>/dev/null; then
    need_rebuild=1
  fi

  if [ "$need_rebuild" -eq 1 ]; then
    echo "Rebuilding virtual environment at $venv" >&2
    rm -rf "$venv"
    python3 -m venv "$venv"
  fi

  if [ ! -x "$py" ]; then
    echo "ERROR: venv python missing at $py" >&2
    return 1
  fi
  printf '%s\n' "$py"
}
