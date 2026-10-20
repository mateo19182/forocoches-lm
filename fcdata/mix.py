"""Build the training mix: dedup, type weights, header with field dropout, val split, replay.

    python3 -m fcdata.mix --threads data/processed/threads.v0.jsonl --labels data/labels \
        --dedup data/processed/dedup_drop.json --replay data/replay/fineweb2_spa.jsonl \
        --target-mtok 50 --out data/mix/v0

Writes {out}/train.jsonl and {out}/val.jsonl in TRL prompt/completion format:
  prompt     = conditioning header + blank line (masked from the loss), or "" when every field dropped
  completion = the thread (TÍTULO + posts), trained on; the trainer appends EOS
"""

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

# Story-leaning profile. historia/alta gets an extra boost; compraventa is dropped.
WEIGHTS = {
    "historia": 3.0,
    "humor": 1.5,
    "hipotetico": 1.5,
    "debate": 1.0,
    "pregunta": 0.5,
    "noticia": 0.3,
    "otro": 0.3,
    "compraventa": 0.0,
    None: 0.5,  # the 26 unlabelled threads
}
HISTORIA_ALTA = 4.0
FIELD_KEEP_P = 0.5
REPLAY_FRAC = 0.08
MAX_CHARS = 13_000  # ~4096 tokens; longer threads are cut at a post boundary
CHARS_PER_TOKEN = 3.5
VAL_MOD = 100  # 1% of threads, by stable hash

_POST_SPLIT = re.compile(r"\n\n(?=#\d+ (?:OP|forero_[a-z]+): )")


def is_val(thread_id: int) -> bool:
    return int(hashlib.md5(f"val{thread_id}".encode()).hexdigest()[:8], 16) % VAL_MOD == 0


def weight(lab) -> float:
    if lab is None:
        return WEIGHTS[None]
    if lab["tipo"] == "historia" and lab["calidad"] == "alta":
        return HISTORIA_ALTA
    return WEIGHTS[lab["tipo"]]


ALL_FIELDS = ("tipo", "calidad", "etiquetas", "páginas")


def header(row, lab, rng, dropout=True, calidad=True, fields=ALL_FIELDS) -> str:
    cand = []
    if lab:
        cand.append(("tipo", lab["tipo"]))
        if calidad:
            cand.append(("calidad", lab["calidad"]))
    if row["tags"]:
        cand.append(("etiquetas", " ".join(row["tags"])))
    cand.append(("páginas", row["pages_bucket"]))
    kept = [f"{k}: {v}" for k, v in cand if k in fields and (not dropout or rng.random() < FIELD_KEEP_P)]
    return " | ".join(kept) + "\n\n" if kept else ""


def clip_thread(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    out, n = [], 0
    for part in _POST_SPLIT.split(text):
        if n + len(part) > MAX_CHARS and out:
            break
        out.append(part)
        n += len(part) + 2
    return "\n\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", required=True)
    ap.add_argument("--labels", required=True, help="dir with pilot.jsonl / full.jsonl")
    ap.add_argument("--dedup", required=True)
    ap.add_argument("--replay", help="JSONL with a 'text' field (general Spanish, no header)")
    ap.add_argument("--target-mtok", type=float, help="subsample the weighted FC mix to ~this many M tokens")
    ap.add_argument("--weights", type=json.loads, help='JSON tipo->weight overriding WEIGHTS, e.g. \'{"pregunta": 0.7}\'')
    ap.add_argument("--calidad-mult", type=json.loads,
                    help='JSON calidad->multiplier on the tipo weight, e.g. \'{"alta": 2, "media": 1, "baja": 0.6}\'; '
                         "replaces the historia/alta special case")
    ap.add_argument("--replay-frac", type=float, default=REPLAY_FRAC)
    ap.add_argument("--chunk", action="store_true",
                    help="split threads longer than MAX_CHARS into token-bounded continuation chunks (last 256 tokens of "
                         "the previous chunk as masked context) instead of dropping their tail; val stays clipped")
    ap.add_argument("--tokenizer", default="runs/v0/final/tokenizer.json", help="for --chunk")
    ap.add_argument("--fields", nargs="+", default=list(ALL_FIELDS), choices=ALL_FIELDS,
                    help="header fields to emit (each still subject to dropout); e.g. --fields tipo")
    ap.add_argument("--no-calidad", action="store_true",
                    help="leave calidad out of the header: v1 learned nothing from it (samples/cond_v1_first256.md), "
                         "so quality is steered by the mix weights instead")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.weights:
        WEIGHTS.update(args.weights)
    global weight
    if args.calidad_mult:
        def weight(lab):  # noqa: F811
            return WEIGHTS[None] if lab is None else WEIGHTS[lab["tipo"]] * args.calidad_mult[lab["calidad"]]
    chunker = None
    if args.chunk:
        from tokenizers import Tokenizer
        from .mix_v2 import chunks
        tok_ = Tokenizer.from_file(args.tokenizer)
        tok_.no_truncation(); tok_.no_padding()
        chunker = lambda text, prompt: [d for d, _ in chunks(text, prompt, tok_)]

    labels = {}
    for p in Path(args.labels).glob("*.jsonl"):
        for l in open(p):
            d = json.loads(l)
            labels[d["thread_id"]] = d
    drop = set(json.load(open(args.dedup))["drop"])

    rows = [json.loads(l) for l in open(args.threads)]
    rows = [r for r in rows if r["thread_id"] not in drop]

    train, val = [], []
    for r in rows:
        (val if is_val(r["thread_id"]) else train).append(r)

    expected = sum(weight(labels.get(r["thread_id"])) * len(r["text"]) for r in train) / CHARS_PER_TOKEN / 1e6
    scale = min(1.0, args.target_mtok / expected) if args.target_mtok else 1.0
    print(f"weighted FC mix ~{expected:.0f}M tokens; scaling by {scale:.2f}")

    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats, tok = Counter(), Counter()
    docs = []
    chunked = 0
    for r in train:
        lab = labels.get(r["thread_id"])
        w = weight(lab) * scale
        copies = int(w) + (rng.random() < w - int(w))
        src = lab["tipo"] if lab else "?"
        for _ in range(copies):
            # Each copy draws its own header, so repeated threads are seen under different conditioning.
            hdr = header(r, lab, rng, calidad=not args.no_calidad, fields=args.fields)
            if chunker and len(r["text"]) > MAX_CHARS:
                parts = chunker(r["text"], hdr)
                chunked += 1
            else:
                parts = [{"prompt": hdr, "completion": clip_thread(r["text"])}]
            for d in parts:
                docs.append({**d, "src": src})
                stats[src] += 1
                tok[src] += len(d["completion"]) / CHARS_PER_TOKEN / 1e6

    fc_tok = sum(tok.values())
    if args.replay:
        need = fc_tok * args.replay_frac / (1 - args.replay_frac)
        got = 0.0
        replay_rows = [json.loads(l)["text"] for l in open(args.replay)]
        rng.shuffle(replay_rows)
        val_replay = replay_rows[:500]
        for t in replay_rows[500:]:
            if got >= need:
                break
            t = t[:MAX_CHARS]
            docs.append({"prompt": "", "completion": t, "src": "replay"})
            got += len(t) / CHARS_PER_TOKEN / 1e6
        stats["replay"], tok["replay"] = sum(d["src"] == "replay" for d in docs), got
        if got < need:
            print(f"WARNING: replay file only covers {got:.1f}M of the {need:.1f}M tokens wanted")

    rng.shuffle(docs)
    with open(out_dir / "train.jsonl", "w") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # Validation: one copy per thread, full header (no dropout), so per-tipo perplexity is comparable.
    with open(out_dir / "val.jsonl", "w") as f:
        for r in val:
            lab = labels.get(r["thread_id"])
            d = {"prompt": header(r, lab, rng, dropout=False, calidad=not args.no_calidad, fields=args.fields), "completion": clip_thread(r["text"]),
                 "src": lab["tipo"] if lab else "?"}
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        if args.replay:
            for t in val_replay:
                f.write(json.dumps({"prompt": "", "completion": t[:MAX_CHARS], "src": "replay"}, ensure_ascii=False) + "\n")

    summary = {k: {"docs": stats[k], "mtok": round(tok[k], 1)} for k in sorted(stats, key=lambda k: -tok[k])}
    summary["_total_mtok"] = round(sum(tok.values()), 1)
    summary["_val_threads"] = len(val)
    summary["_dedup_dropped"] = len(drop)
    summary["_long_threads_chunked"] = chunked
    summary["_args"] = {k: v for k, v in vars(args).items() if k in ("weights", "calidad_mult", "replay_frac", "chunk", "no_calidad", "fields", "target_mtok", "seed")}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
