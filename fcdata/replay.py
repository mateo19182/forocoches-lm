"""Fetch general Spanish replay text from FineWeb-2 (spa_Latn), streamed.

    .venv-train/bin/python -m fcdata.replay --out data/replay/fineweb2_spa.jsonl --mchars 60

60M chars (~17M tokens) covers 8% replay for runs up to ~200M FC tokens.
"""

import argparse
import json
from pathlib import Path

from datasets import load_dataset

MIN_CHARS, MAX_CHARS = 500, 10_000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mchars", type=float, default=60)
    args = ap.parse_args()

    ds = load_dataset("HuggingFaceFW/fineweb-2", name="spa_Latn", split="train", streaming=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    total, n = 0, 0
    with open(args.out, "w") as f:
        for ex in ds:
            t = ex["text"]
            if not MIN_CHARS <= len(t) <= MAX_CHARS:
                continue
            f.write(json.dumps({"text": t}, ensure_ascii=False) + "\n")
            total += len(t)
            n += 1
            if total >= args.mchars * 1e6:
                break
    print(f"wrote {n} docs, {total / 1e6:.1f}M chars to {args.out}")


if __name__ == "__main__":
    main()
