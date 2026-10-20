"""Full fine-tune of Gemma 3 1B (base) on the FC mix with Unsloth + TRL.

    scripts/gpu_train.sh .venv-train/bin/python train.py --data data/mix/v0 --out runs/v0
    # smoke test: add --max-steps 30

Data is TRL prompt/completion JSONL from fcdata/mix.py: the header (prompt) is masked from the
loss, the thread (completion) is trained, and TRL appends EOS. Documents are packed into 4096-token
rows with best-fit decreasing; Unsloth's packed attention keeps them from attending to each other
(verified by scripts/check_packing.py).
"""

import argparse
import os

import unsloth  # noqa: F401  (must be imported before transformers/trl)
import torch
import torch._dynamo

# Unsloth caps recompiles at 1024 per frame / accumulated, after which torch.compile silently falls back
# to eager and every step takes ~2.4x longer (v1 hit this ~550 steps into each process: CPU-bound
# forward and autograd threads, GPU half idle). A recompile per step is far cheaper than eager forever.
# Launch with TORCH_LOGS=recompiles to see what keeps triggering them.
torch._dynamo.config.recompile_limit = 1_000_000
torch._dynamo.config.accumulated_recompile_limit = 1_000_000
from datasets import load_dataset
from trl import SFTConfig, SFTTrainer
from unsloth import FastLanguageModel

MODEL = "unsloth/gemma-3-1b-pt"  # ungated mirror of google/gemma-3-1b-pt
MAX_LEN = 4096
MIN_ROW_TOKENS = 2048
EVAL_DOCS = 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--init", default=MODEL, help="model to start from: the base, or a previous run's final/ for a second stage")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=float, default=1)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-grad-norm", type=float, default=1.0,
                    help="v1 ran at 1.0 with a pre-clip norm of 3-5 most of the time, i.e. the clip scaled every step")
    ap.add_argument("--micro-batch", type=int, default=1, help="packed 4096-token rows per step per device")
    ap.add_argument("--grad-accum", type=int, default=16, help="16 x 4096 = ~65k tokens per optimizer step")
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--warmup-ratio", type=float, default=0.03)
    ap.add_argument("--min-lr-rate", type=float, default=0.0,
                    help="cosine floor as a fraction of --lr; >0 leaves room for a second stage to finish the decay")
    ap.add_argument("--eval-steps", type=int, default=200)
    ap.add_argument("--resume", help="checkpoint dir to resume from (e.g. runs/v1/checkpoint-500); same --data/--out")
    ap.add_argument("--workers", type=int, default=2, help="dataloader workers (0 saves host RAM)")
    ap.add_argument("--gc", choices=["unsloth", "hf", "none"], default="hf",
                    help="gradient checkpointing: 'hf' recomputes on GPU; 'unsloth' offloads to host RAM, which went "
                         "2.5x slower after ~600 steps in v1 (autograd thread CPU-bound) and saves nothing at 1B/4k; "
                         "'none' keeps all activations (faster if it fits)")
    args = ap.parse_args()

    model, tok = FastLanguageModel.from_pretrained(
        args.init,
        max_seq_length=MAX_LEN,
        dtype=torch.bfloat16,
        load_in_4bit=False,
        full_finetuning=True,
        # Pure-bf16 weights round away most updates at lr ~2e-5; keep fp32 master weights.
        float32_mixed_precision=True,
        use_gradient_checkpointing={"unsloth": "unsloth", "hf": True, "none": False}[args.gc],
    )
    # Unsloth's generated modules reset these to 1024 (and set suppress_errors) while loading the model.
    torch._dynamo.config.recompile_limit = 1_000_000
    torch._dynamo.config.accumulated_recompile_limit = 1_000_000
    torch._dynamo.config.verbose = True  # print the traceback of any compile error that suppress_errors swallows
    print(f"dynamo limits: recompile {torch._dynamo.config.recompile_limit}, "
          f"accumulated {torch._dynamo.config.accumulated_recompile_limit}")

    ds = load_dataset("json", data_files={"train": f"{args.data}/train.jsonl", "val": f"{args.data}/val.jsonl"})
    ds = ds.remove_columns("src")
    val = ds["val"].shuffle(seed=0).select(range(min(EVAL_DOCS, len(ds["val"]))))

    cfg = SFTConfig(
        output_dir=args.out,
        max_length=MAX_LEN,
        packing=True,
        packing_strategy="bfd",
        completion_only_loss=True,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        per_device_train_batch_size=args.micro_batch,
        per_device_eval_batch_size=args.micro_batch,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine_with_min_lr" if args.min_lr_rate else "cosine",
        lr_scheduler_kwargs={"min_lr_rate": args.min_lr_rate} if args.min_lr_rate else None,
        warmup_ratio=args.warmup_ratio,
        weight_decay=0.01,
        max_grad_norm=args.max_grad_norm,
        # SFTConfig defaults this to True, which would re-enable checkpointing whatever Unsloth was told.
        gradient_checkpointing=args.gc != "none",
        optim="adamw_8bit",
        bf16=True,
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.eval_steps,
        save_total_limit=3,
        report_to="none",
        dataloader_num_workers=args.workers,
        seed=0,
    )
    trainer = SFTTrainer(model=model, processing_class=tok, args=cfg, train_dataset=ds["train"], eval_dataset=val)

    # Drop the short leftover rows of the bin packing. A row of a few hundred tokens (query length under flex's
    # 128-token block) sends FlexAttention down a path with a generator graph break; the dynamic-shape graph dynamo
    # then builds serves every later batch and runs attention unfused: 12 -> 28 s/step for good (v1, v2 diagnosis
    # 2026-10-19, runs/diag). Rows are otherwise 4065-4096 tokens, so this loses well under 0.1% of tokens.
    for split in ("train_dataset", "eval_dataset"):
        d = getattr(trainer, split)
        n = len(d)
        d = d.filter(lambda ex: len(ex["input_ids"]) >= MIN_ROW_TOKENS, num_proc=8, desc=f"drop short rows ({split})")
        setattr(trainer, split, d)
        print(f"{split}: {n} packed rows, {n - len(d)} shorter than {MIN_ROW_TOKENS} tokens dropped")
    if os.environ.get("FC_DYNAMO_STATS"):
        # Diagnostic: how many graphs dynamo has compiled so far and what the live recompile budgets are.
        from transformers import TrainerCallback
        from torch._dynamo.utils import counters

        seen = []  # (seq_len, n_docs) of every micro-batch in the current step
        model.register_forward_pre_hook(
            lambda m, a, kw: seen.append((int(kw["input_ids"].shape[1]) if "input_ids" in kw else -1,
                                          len(kw["packed_seq_lengths"]) if kw.get("packed_seq_lengths") is not None else -1)),
            with_kwargs=True)

        # Unsloth's logging setup swallows TORCH_LOGS; ask torch directly, after all imports.
        torch._logging.set_logs(graph_breaks=True, recompiles=True)

        class DynamoStats(TrainerCallback):
            def on_step_end(self, args_, state, control, **kw):
                st = counters["stats"]
                print(f"[dynamo] step {state.global_step}: rows={seen[-16:]} graph_breaks={dict(counters['graph_break'])}", flush=True)
                seen.clear()
                print(f"[dynamo] step {state.global_step}: unique_graphs={st.get('unique_graphs')} "
                      f"calls_captured={st.get('calls_captured')} frames_ok={counters['frames'].get('ok')} "
                      f"total={counters['frames'].get('total')} recompile_limit={torch._dynamo.config.recompile_limit} "
                      f"accumulated={torch._dynamo.config.accumulated_recompile_limit} "
                      f"unimplemented={dict(counters['unimplemented']) if counters['unimplemented'] else {}}", flush=True)
        trainer.add_callback(DynamoStats())

    # Packed docs must not attend to each other: Unsloth's collator wrapper turns each row's
    # seq_lengths into packed_seq_lengths, which drives its block-diagonal attention mask.
    batch = trainer.data_collator([trainer.train_dataset[0]])
    if "packed_seq_lengths" not in batch:
        raise SystemExit("collator emits no packed_seq_lengths: packed documents would attend to each other")
    ids, labels = batch["input_ids"][0], batch["labels"][0]
    shown = "".join(tok.decode([int(t)]) if int(l) != -100 else f"[{tok.decode([int(t)])}]"
                    for t, l in zip(ids[:80], labels[:80]))
    print(f"packed row: {len(ids)} tokens, {len(batch['packed_seq_lengths'])} docs, "
          f"{int((labels != -100).sum())} trained; masked in [brackets]:\n{shown!r}")

    # Resuming replays the data order (seed 0, same packed dataset) and skips the batches already seen.
    trainer.train(resume_from_checkpoint=args.resume)
    trainer.save_model(f"{args.out}/final")
    tok.save_pretrained(f"{args.out}/final")


if __name__ == "__main__":
    main()
