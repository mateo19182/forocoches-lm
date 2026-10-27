#!/bin/bash
# Drive a training run on a rented Vast.ai instance from this server.
#   scripts/vast_train.sh push  INSTANCE MIX INIT          # code + data/mix/MIX + INIT (local dir or HF repo id) -> /workspace/fc
#   scripts/vast_train.sh start INSTANCE RUN -- ARGS...    # nohup scripts/vast_remote.sh ARGS > runs/RUN.log
#   scripts/vast_train.sh sync  INSTANCE RUN [SECONDS]     # pull runs/RUN (+ log) until runs/RUN/final exists
# The instance is never destroyed here: `vastai destroy instance ID` once the final model is pulled.
set -euo pipefail
cd "$(dirname "$0")/.."
KEY=~/.ssh/vast_ed25519
REMOTE=/workspace/fc

target() {
  local url; url=$(vastai ssh-url "$1")  # ssh://root@host:port
  HOSTPART=${url#ssh://}; HOST=${HOSTPART%:*}; PORT=${HOSTPART##*:}
  SSH=(ssh -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)
}

cmd=$1; id=$2; shift 2
target "$id"
case $cmd in
  push)
    mix=$1; init=$2
    "${SSH[@]}" "$HOST" "mkdir -p $REMOTE/data/mix $REMOTE/init $REMOTE/runs $REMOTE/scripts"
    rsync -az --info=progress2 -e "${SSH[*]}" train.py requirements*.txt "$HOST:$REMOTE/"
    rsync -az -e "${SSH[*]}" scripts/vast_remote.sh scripts/eval_ppl.py scripts/sample.py "$HOST:$REMOTE/scripts/"
    rsync -az --info=progress2 -e "${SSH[*]}" "data/mix/$mix" "$HOST:$REMOTE/data/mix/"
    # Shared held-out set for comparing v1/v2/v3 (docs/NEXT-RUN.md).
    "${SSH[@]}" "$HOST" "mkdir -p $REMOTE/data/mix/broad"
    rsync -az -e "${SSH[*]}" data/mix/broad/val.jsonl "$HOST:$REMOTE/data/mix/broad/"
    if [ -d "$init" ]; then
      rsync -az --info=progress2 -e "${SSH[*]}" "$init/" "$HOST:$REMOTE/init/$(basename "$(dirname "$init")")/"
    else  # HF repo id: the instance's downlink is far faster than this server's uplink
      "${SSH[@]}" "$HOST" "export PATH=\$HOME/.local/bin:\$PATH; command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
        uvx --from huggingface_hub hf download $init --local-dir $REMOTE/init/${init##*/}"
    fi
    ;;
  start)
    run=$1; shift; [ "${1:-}" = -- ] && shift
    "${SSH[@]}" "$HOST" "cd $REMOTE && nohup scripts/vast_remote.sh $* > runs/$run.log 2>&1 < /dev/null & echo started"
    ;;
  sync)
    run=$1; every=${2:-600}
    mkdir -p "runs/$run"
    while :; do
      rsync -az -e "${SSH[*]}" "$HOST:$REMOTE/runs/$run.log" "runs/$run.vast.log" || true
      rsync -az --delete -e "${SSH[*]}" "$HOST:$REMOTE/runs/$run/" "runs/$run/" || true
      if [ -f "runs/$run/final/model.safetensors" ] && ! "${SSH[@]}" "$HOST" "pgrep -f '[t]rain.py' >/dev/null"; then
        echo "$(date -Is) final model pulled to runs/$run/final"; break
      fi
      sleep "$every"
    done
    ;;
  *) echo "unknown command $cmd" >&2; exit 2 ;;
esac
