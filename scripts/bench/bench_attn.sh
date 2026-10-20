#!/usr/bin/env bash
# Benchmark attention/checkpointing configurations on real packed rows (needs the GPU to itself).
# Each variant runs train.py for a few dozen steps on data/mix/bench and reports its steady step time.
#   scripts/gpu_train.sh scripts/bench/bench_attn.sh 30            # all variants
#   ONLY="flex_nogc flex_nogc_mb2" scripts/gpu_train.sh scripts/bench/bench_attn.sh 30
# Results 2026-10-18 (steps 16-30 of 30): flex_gc 12.3 s/step, sdpa_gc 20.6 s/step (SDPA cannot use the
# flash kernel with the packed block mask) -> FlexAttention stays.
set -uo pipefail
STEPS=${1:-30}
cd "$(dirname "$0")/.."
mkdir -p runs/bench

run() {  # name, train.py args..., then env assignments after --
  local name=$1; shift
  [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $name "* ]] && return
  local targs=() ; while [ $# -gt 0 ] && [ "$1" != "--" ]; do targs+=("$1"); shift; done; shift || true
  echo "=== $name  (${targs[*]}  env: $*)"
  env TORCH_LOGS=recompiles "$@" .venv-train/bin/python -u train.py --data data/mix/bench --out "runs/bench/$name" \
    --max-steps "$STEPS" --eval-steps 100000 --workers 0 "${targs[@]}" > "runs/bench/$name.log" 2>&1
  # mean s/it over the second half of the run, from the first tqdm timestamp seen for each step
  # (the last line for the final step includes the checkpoint save)
  tr '\r' '\n' < "runs/bench/$name.log" | grep -oE "[0-9]+/$STEPS \[[0-9:]+<" \
    | awk -F'[/ \\[<]' -v S="$STEPS" '{split($4,t,":"); s=(length(t)==3)?t[1]*3600+t[2]*60+t[3]:t[1]*60+t[2]; if (!($1 in step)) step[$1]=s}
       END {a=step[int(S/2)]; b=step[S]; if (a!="" && b!="") printf "%s: %.1f s/step (steps %d-%d)\n", "'"$name"'", (b-a)/(S-int(S/2)), int(S/2), S; else print "'"$name"': no timing (see log)"}'
  tr '\r' '\n' < "runs/bench/$name.log" | grep -cE "Recompiling function" | sed 's/^/  recompiles: /'
  tr '\r' '\n' < "runs/bench/$name.log" | grep -E "Traceback|Error|OutOfMemory" | grep -v Warning | head -2
  rm -rf "runs/bench/$name"
}

# peak GPU memory sampler, so we know how much headroom each variant leaves
nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -l 5 > runs/bench/mem.log 2>/dev/null &
MEM_PID=$!
trap 'kill $MEM_PID 2>/dev/null' EXIT

run flex_gc       --gc hf   --
run sdpa_gc       --gc hf   -- UNSLOTH_ENABLE_FLEX_ATTENTION=0
run flex_nogc     --gc none --
run flex_nogc_mb2 --gc none --micro-batch 2 --grad-accum 8 --
echo "peak GPU memory during the benchmark: $(sort -n runs/bench/mem.log | tail -1) MiB"
