"""Near-duplicate OPs (reposts, copypastas, the same news pasted twice) via MinHash LSH.

    .venv/bin/python -m fcdata.dedup --threads data/processed/threads.v0.jsonl --out data/processed/dedup_drop.json

Only OPs with >= MIN_CHARS of their own text take part: short OPs ("¿Qué capricho os habéis dado?")
legitimately repeat, and their threads differ in the replies. In each duplicate cluster the thread
with the most replies is kept.
"""

import argparse
import json
import re
from multiprocessing import Pool

from datasketch import MinHash, MinHashLSH

from .build import core_text

MIN_CHARS = 200
NUM_PERM = 128
THRESHOLD = 0.8
SHINGLE = 5

_OP_RE = re.compile(r"#1 OP: (.*?)(?:\n\n#\d+ |\Z)", re.S)


def signature(line: str):
    r = json.loads(line)
    m = _OP_RE.search(r["text"])
    op = core_text(m.group(1)) if m else ""
    if len(op) < MIN_CHARS:
        return None
    words = re.findall(r"\w+", op.lower())
    shingles = {" ".join(words[i : i + SHINGLE]) for i in range(max(1, len(words) - SHINGLE + 1))}
    mh = MinHash(num_perm=NUM_PERM)
    mh.update_batch([s.encode() for s in shingles])
    return r["thread_id"], r["n_replies"], mh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=28)
    args = ap.parse_args()

    with open(args.threads) as f, Pool(args.workers) as pool:
        sigs = [s for s in pool.imap(signature, f, chunksize=500) if s]
    print(f"{len(sigs)} OPs with >= {MIN_CHARS} chars")

    lsh = MinHashLSH(threshold=THRESHOLD, num_perm=NUM_PERM)
    # Most replies first, so the first member of a cluster to be inserted is the keeper.
    sigs.sort(key=lambda s: -s[1])
    drop, examples = [], []
    for tid, _, mh in sigs:
        hits = lsh.query(mh)
        if hits:
            drop.append(tid)
            if len(examples) < 30:
                examples.append((tid, hits[0]))
            continue
        lsh.insert(str(tid), mh)

    print(f"dropping {len(drop)} near-duplicate threads ({100 * len(drop) / len(sigs):.1f}% of long OPs)")
    with open(args.out, "w") as f:
        json.dump({"drop": drop, "examples": examples}, f)


if __name__ == "__main__":
    main()
