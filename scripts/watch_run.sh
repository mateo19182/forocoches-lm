#!/usr/bin/env bash
# Watch a training log and report to Telegram: a progress message every N hours, and a final
# message when the run finishes or fails (gpu_train.sh prints "command exited with status S").
#   setsid nohup scripts/watch_run.sh runs/v1.log v1 3 > runs/watch_v1.log 2>&1 &
set -uo pipefail
LOG=$1; NAME=${2:-run}; EVERY_H=${3:-3}
cd "$(dirname "$0")/.."
notify() { scripts/notify_tg.sh "$1" || echo "notify failed: $1"; }

progress() {  # last progress-bar line, last loss dict, last eval loss
  local bar loss ev state
  bar=$(tr '\r' '\n' < "$LOG" | grep -oE "[0-9]+/[0-9]+ \[[^]]*\]" | tail -1)
  loss=$(tr '\r' '\n' < "$LOG" | grep -oE "'loss': '[0-9.]+" | tail -1 | grep -oE "[0-9.]+$")
  ev=$(tr '\r' '\n' < "$LOG" | grep -oE "'eval_loss': '?[0-9.]+" | tail -1 | grep -oE "[0-9.]+$")
  if [[ -z "$ev" ]]; then  # after a --resume the log starts empty; the newest checkpoint has the history
    state=$(ls -t runs/"$NAME"/checkpoint-*/trainer_state.json 2>/dev/null | head -1)
    [[ -n "$state" ]] && ev=$(grep -oE '"eval_loss": [0-9.]+' "$state" | tail -1 | grep -oE "[0-9.]+$")
  fi
  printf 'step %s\nloss %s · eval %s' "${bar:-?}" "${loss:-?}" "${ev:-?}"
}

notify "[$NAME] watching. $(progress)"
last=$(date +%s)
while :; do
  if tr '\r' '\n' < "$LOG" | grep -q "command exited with status"; then
    status=$(tr '\r' '\n' < "$LOG" | grep -oE "command exited with status [0-9]+" | tail -1 | grep -oE "[0-9]+$")
    err=$(tr '\r' '\n' < "$LOG" | grep -E "Error|Traceback|Killed|out of memory" | grep -v Warning | tail -2)
    if [[ "$status" == "0" ]]; then
      notify "[$NAME] FINISHED ✅ $(progress)
model: runs/$NAME/final"
    else
      notify "[$NAME] FAILED ❌ exit $status
$(progress)
${err:-no error line found; see $LOG}"
    fi
    exit 0
  fi
  if ! pgrep -f "train.py --data" >/dev/null && ! tr '\r' '\n' < "$LOG" | grep -q "command exited"; then
    sleep 60  # give gpu_train.sh a minute to write its exit line
    if ! tr '\r' '\n' < "$LOG" | grep -q "command exited"; then
      notify "[$NAME] FAILED ❌ training process vanished without an exit line. $(progress)"
      exit 1
    fi
    continue
  fi
  now=$(date +%s)
  if (( now - last >= EVERY_H * 3600 )); then
    notify "[$NAME] $(progress)"
    last=$now
  fi
  sleep 60
done
