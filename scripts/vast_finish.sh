#!/bin/bash
# Watch a Vast training run, then evaluate on the instance, pull the results and destroy the instance.
# Telegram: progress every 2 h, failures, and the final summary.
#   setsid nohup scripts/vast_finish.sh INSTANCE RUN INIT_NAME > runs/RUN.finish.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
id=$1; run=$2; init=$3
KEY=~/.ssh/vast_ed25519
REMOTE=/workspace/fc
url=$(vastai ssh-url "$id"); hostport=${url#ssh://}
HOST=${hostport%:*}
SSHOPT=(ssh -i "$KEY" -p "${hostport##*:}" -o ConnectTimeout=20 -o ServerAliveInterval=30 -o LogLevel=ERROR)
SSH=("${SSHOPT[@]}" "$HOST")
remote() { "${SSH[@]}" "$@" 2>/dev/null | grep -v 'setlocale\|Welcome to vast\|Have fun'; }
tg() { echo "$(date -Is) $1"; hermes send -q --to telegram "forocoches-lm: $1"; }
progress() { remote "tr '\r' '\n' < $REMOTE/runs/$run.log | grep -E ' [0-9]+/[0-9]+ \[' | tail -1" | sed 's/.*| *//' ; }

last=$(date +%s)
while :; do
  if remote "test -f $REMOTE/runs/$run/final/model.safetensors && ! pgrep -f '[t]rain.py' >/dev/null && echo done" | grep -q done; then break; fi
  if remote "! pgrep -f '[t]rain.py' >/dev/null && grep -q Traceback $REMOTE/runs/$run.log && echo failed" | grep -q failed; then
    tg "$run training FAILED on Vast: $(remote "grep -A1 -E '^[A-Za-z]*Error' $REMOTE/runs/$run.log | tail -2" | tr '\n' ' ' | cut -c1-200). Instance $id kept running (\$0.51/h): check runs/$run.vast.log."
    exit 1
  fi
  if (( $(date +%s) - last >= 7200 )); then
    tg "$run training: $(progress); last eval loss $(remote "grep -oE \"'eval_loss': '[0-9.]+'\" $REMOTE/runs/$run.log | tail -1 | grep -oE '[0-9.]+'")."
    last=$(date +%s)
  fi
  sleep 300
done

tg "$run training done; evaluating on the instance (~30 min) before pulling and destroying it."
remote "cd $REMOTE && nohup bash -c '
  .venv/bin/python scripts/eval_ppl.py --val data/mix/broad/val.jsonl --models v2=init/$init $run=runs/$run/final --out runs/eval_${run}_broad.md &&
  .venv/bin/python scripts/eval_ppl.py --val data/mix/$run/val.jsonl --models v2=init/$init $run=runs/$run/final --out runs/eval_${run}_own.md &&
  .venv/bin/python scripts/sample.py --model init/$init --out runs/v2_gen_long.md --max-new 1200 &&
  .venv/bin/python scripts/sample.py --model runs/$run/final --out runs/${run}_gen_long.md --max-new 1200;
  echo \$? > runs/eval.done' > runs/eval.log 2>&1 < /dev/null &"
until remote "cat $REMOTE/runs/eval.done" | grep -qE '^[0-9]+$'; do sleep 60; done
eval_rc=$(remote "cat $REMOTE/runs/eval.done")

mkdir -p "runs/$run" samples
rsync -az -e "${SSHOPT[*]}" "$HOST:$REMOTE/runs/$run/final/" "runs/$run/final/"
rsync -az -e "${SSHOPT[*]}" "$HOST:$REMOTE/runs/$run.log" "runs/$run.vast.log"
rsync -az -e "${SSHOPT[*]}" "$HOST:$REMOTE/runs/eval_${run}_*" "$HOST:$REMOTE/runs/*_gen_long.md" "$HOST:$REMOTE/runs/eval.log" samples/ || true
local_md5=$(md5sum "runs/$run/final/model.safetensors" | cut -d' ' -f1)
remote_md5=$(remote "md5sum $REMOTE/runs/$run/final/model.safetensors" | cut -d' ' -f1)
summary=$(grep -E '^\| (FC \(all tipos\)|historia|replay) ' "samples/eval_${run}_broad.md" 2>/dev/null | awk -F'|' '{gsub(/ /,"",$2); printf "%s v2 %s -> v3 %s; ", $2, $5, $6}')
if [ "$local_md5" = "$remote_md5" ] && [ -n "$local_md5" ]; then
  vastai destroy instance "$id" -y
  tg "$run done. Held-out ppl: ${summary:-eval failed (rc $eval_rc), see samples/eval.log}. Model in runs/$run/final, samples in samples/${run}_gen_long.md. Instance destroyed. Next: read samples, then export + publish."
else
  tg "$run done but the model pull did not verify (md5 $local_md5 vs $remote_md5). Instance $id kept running (\$0.51/h)."
  exit 1
fi
