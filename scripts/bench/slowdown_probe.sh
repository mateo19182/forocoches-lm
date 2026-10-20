#!/usr/bin/env bash
# Sample the step rate of a run every few minutes; on the first slowdown, capture evidence
# (py-spy dumps of the trainer, GPU state, last log lines, dynamo counters) into runs/probe_<name>/.
#   setsid nohup scripts/bench/slowdown_probe.sh runs/v1.log v1 18 > runs/probe_v1.log 2>&1 &
set -uo pipefail
LOG=$1; NAME=${2:-run}; THRESH=${3:-18}   # seconds per step that counts as slow
cd "$(dirname "$0")/.."
OUT="runs/probe_$NAME"; mkdir -p "$OUT"
step() { tr '\r' '\n' < "$LOG" | grep -oE "[0-9]+/[0-9]+ \[" | tail -1 | cut -d/ -f1; }
prev_s=$(step); prev_t=$(date +%s)
echo "$(date +%T) probing from step $prev_s (threshold ${THRESH}s/step)"
while :; do
  sleep 300
  s=$(step); t=$(date +%s)
  [[ -z "$s" || -z "$prev_s" ]] && { prev_s=$s; prev_t=$t; continue; }
  if (( s > prev_s )); then
    rate=$(( (t - prev_t) / (s - prev_s) ))
    echo "$(date +%T) step $s  ${rate}s/step"
    if (( rate > THRESH && s - prev_s > 3 )); then
      PID=$(pgrep -f "^\.venv-train/bin/python -u train.py" | head -1)
      echo "$(date +%T) SLOWDOWN at step $s (${rate}s/step); capturing evidence for pid $PID"
      for i in 1 2 3; do .venv-train/bin/py-spy dump --pid "$PID" > "$OUT/pyspy_dump_$i.txt" 2>&1; sleep 4; done
      .venv-train/bin/py-spy record --pid "$PID" --duration 30 --rate 50 --format raw -o "$OUT/pyspy_record.txt" >/dev/null 2>&1
      nvidia-smi -q -d PERFORMANCE,MEMORY,TEMPERATURE > "$OUT/nvidia_smi.txt" 2>&1
      tr '\r' '\n' < "$LOG" | grep -vE "^\s*[0-9]+%\|" | tail -300 > "$OUT/log_tail.txt"
      free -g > "$OUT/free.txt"; top -b -H -n 1 -p "$PID" | head -20 > "$OUT/threads.txt"
      scripts/notify_tg.sh "[$NAME] slowdown detected at step $s (${rate}s/step); evidence in $OUT" 2>/dev/null
      echo "$(date +%T) evidence captured; continuing to sample"
      THRESH=9999  # capture once
    fi
  fi
  prev_s=$s; prev_t=$t
  tr '\r' '\n' < "$LOG" | grep -q "command exited" && { echo "run ended"; exit 0; }
done
