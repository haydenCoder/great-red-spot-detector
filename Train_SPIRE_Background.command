#!/bin/bash
# =============================================================================
#  SPIRE-Net DURABLE BACKGROUND TRAIN
#  - Keeps Mac awake (caffeinate) so training continues with lid closed*
#  - Resumes from checkpoint if you reopen
#  - NaN-guard: never writes corrupt weights; keeps GOOD backup
#  * Full power-off / dead battery still stops everything (hardware limit).
#    Prefer plugged-in power. System Settings → Battery → Options:
#    "Prevent automatic sleeping on power adapter when display is off" = ON
# =============================================================================
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
# shellcheck disable=SC1091
source "$ROOT/_lib_venv.sh"
HOURS="${1:-8}"
SAMPLES="${2:-20}"

echo "=============================================="
echo " SPIRE-Net durable background train"
echo " Hours: $HOURS   Samples/epoch: $SAMPLES"
echo " Folder: $ROOT"
echo "=============================================="

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found"
  read -r -p "Enter to close"; exit 1
fi

PY="$(grs_ensure_venv)" || { read -r -p "Enter to close"; exit 1; }
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$ROOT/requirements.txt" 2>/dev/null || true

LOGDIR="$ROOT/app/models/train_logs"
mkdir -p "$LOGDIR"
STAMP=$(date +%Y%m%d_%H%M%S)
LOG="$LOGDIR/durable_train_${STAMP}.log"

echo "Log: $LOG"
echo "Starting under caffeinate (prevent idle sleep)…"
echo "To stop: open app → Stop train, or: kill the python process."
echo ""

nohup caffeinate -dims "$PY" -u "$ROOT/app/nn_grs.py" \
  --hours "$HOURS" \
  --samples "$SAMPLES" \
  >> "$LOG" 2>&1 &

PID=$!
echo "PID=$PID"
echo "$PID" > "$LOGDIR/durable_train.pid"
echo "Training detached. You can close this window."
echo "Watch: tail -f \"$LOG\""
echo "Checkpoint: app/models/spire_train_checkpoint.json"
echo "GOOD weights: app/models/spire_net_weights.GOOD.npz"
sleep 2
tail -n 15 "$LOG" 2>/dev/null || true
echo ""
read -r -p "Enter to close this launcher (training keeps running)…"
