"""Sanity checks before training: tokenizer stats and cross-document attention isolation.

    .venv-train/bin/python scripts/check_packing.py
"""
import json
import statistics

import unsloth  # noqa: F401  (must be imported before transformers)
import torch
from unsloth import FastLanguageModel

MODEL = "unsloth/gemma-3-1b-pt"

model, tok = FastLanguageModel.from_pretrained(MODEL, max_seq_length=4096, dtype=torch.bfloat16,
                                               load_in_4bit=False, full_finetuning=True)
model.eval()

# 1) Real chars/token on our data, and how many docs exceed 4096 tokens.
rows = [json.loads(l) for _, l in zip(range(3000), open("data/mix/v0/train.jsonl"))]
lens = [len(tok(r["prompt"] + r["completion"])["input_ids"]) for r in rows]
chars = sum(len(r["prompt"] + r["completion"]) for r in rows)
print(f"chars/token {chars / sum(lens):.2f}; tokens p50 {statistics.median(lens):.0f} "
      f"p90 {sorted(lens)[int(.9 * len(lens))]} max {max(lens)}; >4096: {sum(l > 4096 for l in lens)}")

# 2) Doc B alone vs doc B packed after doc A (position_ids restart, packed_seq_lengths set).
a = tok(rows[0]["completion"][:1500], return_tensors="pt")["input_ids"][0]
b = tok(rows[1]["completion"][:1500], return_tensors="pt")["input_ids"][0]
dev = model.device
with torch.no_grad():
    alone = model(input_ids=b[None].to(dev)).logits[0].float()
    ids = torch.cat([a, b])[None].to(dev)
    pos = torch.cat([torch.arange(len(a)), torch.arange(len(b))])[None].to(dev)
    packed = model(input_ids=ids, position_ids=pos,
                   packed_seq_lengths=torch.tensor([len(a), len(b)], dtype=torch.int32)).logits[0, len(a):].float()
    naive = model(input_ids=ids).logits[0, len(a):].float()
print(f"max |logit diff| packed vs alone: {(packed - alone).abs().max():.4f}  (naive concat: {(naive - alone).abs().max():.4f})")
