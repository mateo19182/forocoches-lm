#!/usr/bin/env bash
# Wait for a checkpoint to be fully written, then restart the run from it with the current train.py
# (new flags/settings), and restart the Telegram watcher. Detached so it survives the Claude Code session.
#   setsid nohup scripts/restart_at_ckpt.sh runs/v1 3000 > runs/restart_v1.log 2>&1 &
set -uo pipefail
OUT=$1; STEP=$2; NAME=$(basename "$OUT"); LOG="runs/$NAME.log"
cd "$(dirname "$0")/.."
echo "$(date +%T) waiting for $OUT/checkpoint-$STEP and step $((STEP + 1))"
until [[ -f "$OUT/checkpoint-$STEP/trainer_state.json" ]] && tr '\r' '\n' < "$LOG" | grep -qE "^ *[0-9]+%\|[^|]*\| $((STEP + 1))/"; do
  tr '\r' '\n' < "$LOG" | grep -qE "command exited" && { echo "run ended before step $STEP; nothing to do"; exit 0; }
  sleep 30
done
echo "$(date +%T) checkpoint-$STEP complete; stopping run"
pkill -f "[w]atch_run.sh"
pkill -f "^\.venv-train/bin/python -u train.py --data data/mix/$NAME"
for _ in $(seq 1 40); do pgrep -f "[g]pu_train.sh .venv-train/bin/python -u train.py --data data/mix/$NAME" >/dev/null || break; sleep 3; done
sleep 5
echo "$(date +%T) relaunching from checkpoint-$STEP"
TORCH_LOGS="${TORCH_LOGS:-recompiles,graph_breaks,dynamo}" setsid nohup scripts/gpu_train.sh .venv-train/bin/python -u train.py --data data/mix/$NAME --out "$OUT" \
  --eval-steps 500 --resume "$OUT/checkpoint-$STEP" --workers 0 --gc hf > "$LOG" 2>&1 &
sleep 5
setsid nohup scripts/watch_run.sh "$LOG" "$NAME" 3 > "runs/watch_$NAME.log" 2>&1 &
echo "$(date +%T) relaunched: $(pgrep -af '^\.venv-train/bin/python -u train.py' | cut -c1-80)"
