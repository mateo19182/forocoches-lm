#!/bin/bash
# Copy a Vast run's training log to runs/RUN.vast.log every minute, for the site's live loss curve.
# Stops once the instance stops answering (destroyed) or the run has a final model locally.
#   setsid nohup scripts/vast_live_log.sh INSTANCE RUN > /dev/null 2>&1 &
cd "$(dirname "$0")/.."
id=$1; run=$2; fails=0
url=$(vastai ssh-url "$id"); hp=${url#ssh://}
while (( fails < 10 )) && [ ! -d "runs/$run/final" ]; do
  if rsync -az -e "ssh -i $HOME/.ssh/vast_ed25519 -p ${hp##*:} -o ConnectTimeout=20 -o LogLevel=ERROR" "${hp%:*}:/workspace/fc/runs/$run.log" "runs/$run.vast.log"; then fails=0; else fails=$((fails + 1)); fi
  sleep 60
done
