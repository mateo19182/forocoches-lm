"""Completion-only perplexity per `src` (tipo / replay) on a mix's val.jsonl, for one or more models.

    .venv-train/bin/python scripts/eval_ppl.py --val data/mix/v1/val.jsonl \
        --models base=unsloth/gemma-3-1b-pt v0=runs/v0/final v1=runs/v1/final --out samples/eval_v1.md

Tokenisation mirrors TRL's prompt/completion path: BOS + prompt + completion + EOS, loss on the
completion and EOS only. `replay` rows are general Spanish (FineWeb-2), so their perplexity
measures forgetting; the per-tipo rows measure the gain on FC text.
"""

import argparse
import json
import math
from collections import defaultdict

import torch
import torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer

MAX_LEN = 4096
TOKENS_PER_BATCH = 8192  # logits are vocab(262k)-wide: ~4 GB bf16 per 8k tokens


def encode(tok, rows):
    out = []
    for r in rows:
        p = tok(r["prompt"], add_special_tokens=False)["input_ids"] if r["prompt"] else []
        c = tok(r["completion"], add_special_tokens=False)["input_ids"]
        ids = ([tok.bos_token_id] + p + c + [tok.eos_token_id])[:MAX_LEN]
        n_prompt = 1 + len(p)
        out.append((ids, n_prompt, r["src"]))
    return out


@torch.no_grad()
def score(model, tok, encoded, first=None):
    """Returns {src: [sum_nll, n_tokens, n_docs]}. `first` scores only the first N completion tokens."""
    agg = defaultdict(lambda: [0.0, 0, 0])
    order = sorted(range(len(encoded)), key=lambda i: -len(encoded[i][0]))
    i = 0
    while i < len(order):
        longest = len(encoded[order[i]][0])
        bs = max(1, TOKENS_PER_BATCH // longest)
        batch = [encoded[j] for j in order[i:i + bs]]
        i += bs
        ids = torch.full((len(batch), longest), tok.pad_token_id, dtype=torch.long)
        mask = torch.zeros_like(ids)
        for k, (seq, _, _) in enumerate(batch):
            ids[k, :len(seq)] = torch.tensor(seq)
            mask[k, :len(seq)] = 1
        logits = model(input_ids=ids.cuda(), attention_mask=mask.cuda()).logits
        for k, (seq, n_prompt, src) in enumerate(batch):
            # predict token t from position t-1; score completion tokens only
            lg = logits[k, n_prompt - 1:len(seq) - 1]
            tgt = ids[k, n_prompt:len(seq)].cuda()
            if first:
                lg, tgt = lg[:first], tgt[:first]
            # fp32 in 1k-position chunks: a whole 4k doc in fp32 is 4 GB, too much next to the other GPU services
            nll = sum(F.cross_entropy(lg[j:j + 1024].float(), tgt[j:j + 1024], reduction="sum").item()
                      for j in range(0, len(tgt), 1024))
            a = agg[src]
            a[0] += nll
            a[1] += len(tgt)
            a[2] += 1
        del logits
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--val", required=True)
    ap.add_argument("--models", nargs="+", required=True, help="name=path ...")
    ap.add_argument("--out", required=True, help="markdown report; a .json sibling is written too")
    ap.add_argument("--limit", type=int, help="docs per src (default: all)")
    ap.add_argument("--first", type=int, help="score only the first N completion tokens (title + opener), where the "
                    "header can matter; whole-thread perplexity is dominated by the replies")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.val)]
    if args.limit:
        per = defaultdict(list)
        for r in rows:
            per[r["src"]].append(r)
        rows = [r for v in per.values() for r in v[:args.limit]]

    results = {}
    tok = None
    for spec in args.models:
        name, path = spec.split("=", 1)
        tok = AutoTokenizer.from_pretrained(path)
        encoded = encode(tok, rows)
        model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16).cuda().eval()
        agg = score(model, tok, encoded, args.first)
        fc = [v for k, v in agg.items() if k != "replay"]
        agg["FC (all tipos)"] = [sum(v[0] for v in fc), sum(v[1] for v in fc), sum(v[2] for v in fc)]
        results[name] = {k: {"nll": v[0] / v[1], "ppl": math.exp(v[0] / v[1]), "tokens": v[1], "docs": v[2]}
                         for k, v in agg.items()}
        print(f"{name}: FC ppl {results[name]['FC (all tipos)']['ppl']:.2f}, "
              f"replay ppl {results[name].get('replay', {}).get('ppl', float('nan')):.2f}")
        del model
        torch.cuda.empty_cache()

    names = list(results)
    srcs = sorted(results[names[0]], key=lambda s: -results[names[0]][s]["tokens"])
    srcs = ["FC (all tipos)"] + [s for s in srcs if s != "FC (all tipos)"]
    lines = [f"# Completion perplexity on `{args.val}`" + (f" (first {args.first} completion tokens)" if args.first else ""), "",
             "| src | docs | tokens | " + " | ".join(names) + " |",
             "|---|---:|---:|" + "---:|" * len(names)]
    for s in srcs:
        r0 = results[names[0]][s]
        lines.append(f"| {s} | {r0['docs']} | {r0['tokens']} | " +
                     " | ".join(f"{results[n][s]['ppl']:.2f}" for n in names) + " |")
    open(args.out, "w").write("\n".join(lines) + "\n")
    json.dump(results, open(args.out.rsplit(".", 1)[0] + ".json", "w"), indent=1)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
