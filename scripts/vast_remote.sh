#!/bin/bash
# Runs on the Vast instance from /workspace/fc: build the pinned training env once, then train.
#   scripts/vast_remote.sh --data data/mix/v3 --out runs/v3 --init init/v2 [train.py args]
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  uv venv -p 3.12 .venv
  uv pip install -p .venv -r requirements-train.txt
fi
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))"
exec .venv/bin/python train.py "$@"
