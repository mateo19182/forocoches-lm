# Next run: instructions and lessons from v0–v2

Written 2026-10-20, after v2. For the model trained on the big 2026 crawl that is running now. Everything here is
verified on this machine (RTX 3090, 24 GB) unless marked *untested*.

## Where we are

| | tokens | time | FC ppl (shared held-out) | eval loss |
|---|---:|---:|---:|---:|
| base gemma-3-1b-pt | – | – | 11.11 | – |
| v0 | 52M | 3 h | 7.65 | – |
| v1 | 270M | 14 h | 7.08 | 2.026 |
| v2 | 496M | 26 h | 6.81 | 1.992 |

Doubling the archive data from v1 to v2 bought 4%. Both still write good short replies and drift after ~150 tokens
in long opening posts. **More of the same archive will not fix that; different data and a longer context budget per
story might.** Published: `mateo-19182/forocoches-lm-v2` (bf16), `-q4f16_1-MLC` (browser), dataset
`forocoches-threads-v2.1`. v1 browser weights stay for the web.

## The recipe that works (keep)

1. **Text format**: `TÍTULO: ...`, blank line, `#n autor: texto` posts, quotes `[cita=autor]...[/cita]`, authors
   `OP` / `forero_a`… per thread. Images/links/emails reduced to `[img]`, `[enlace dominio]`, `[email]`.
2. **Header**: `tipo: X` only, kept in half the documents (dropout), masked from the loss. `calidad` measured as having
   zero effect on the model (`samples/cond_v1_first256.md`); `páginas` measures attention, not quality. Don't bring
   them back. The web already hides them for v2 (`HEADER_FIELDS` in `web/app.py`).
3. **Mix** (`fcdata/mix.py --chunk --fields tipo`): historia ×2, rest ×1, compraventa out, ~8% general-Spanish replay
   (FineWeb-2 `spa_Latn`, no header), 1% of threads held out by stable hash. Long threads chunked into 4,096-token
   pieces with 256 tokens of masked continuation context, not cut.
4. **Training** (`train.py`): full fine-tune, fp32 master weights + adamw_8bit, lr 2e-5 cosine, 3% warmup, clip 1.0,
   packed 4,096-token rows with per-document attention (FlexAttention), 16 rows/step = 65,536 tokens/step,
   completion-only loss. 12.0–12.4 s/step → **~19M tokens/hour, ~1B tokens ≈ 53 h**. Launch:
   ```sh
   setsid nohup scripts/gpu_train.sh .venv-train/bin/python train.py --data data/mix/<mix> --out runs/<v> > runs/<v>.log 2>&1 &
   setsid nohup scripts/watch_run.sh runs/<v>.log > runs/watch_<v>.log 2>&1 &   # Telegram every 3 h + finish/fail
   ```
5. **Eval**: `scripts/eval_ppl.py` on the **same** held-out set every time (`data/mix/broad/val.jsonl`, 4,873 threads,
   or its successor), base + previous + new model in one call, plus `scripts/sample.py` and *read the samples*.
   Perplexity alone missed that long OPs drift.
6. **Ship**: `scripts/export_webllm.py <v>` → `data/web/mlc/<v>` (575 MB); smoke-test the bf16 copy with a greedy
   generation; `huggingface_hub.upload_folder` for the bf16 dir, the MLC dir and the dataset dir; add the version to
   `HF_REPOS` and `HEADER_FIELDS` in `web/app.py`, update the Resultados table and Enlaces in `web/static/index.html`,
   `systemctl --user restart forocoches-web`. Restart only after the HF repos exist, or the public site offers a model
   it cannot download.

## Lessons: data

- **Pseudonymise mentions too.** Authors were `forero_x` but 56,879 `@RealNick` mentions survived in the text; v2
  produces nickname-like strings. Mask `@\w+` not in the thread's author map to `@usuario` (or to the right
  `@forero_x` when it is) *before* mixing, so the model and the published dataset agree.
- **Labels are cheap, weighting by them is not worth it.** Haiku labels cost ~$12 for 500k threads and the `tipo`
  header is worth ~2% perplexity. `calidad` was worth nothing; label-based upweighting (v0/v1) did not beat flat ×1/×2.
- **Dedup matters at the group level**: exact + MinHash (5-word shingles, Jaccard ≥ 0.8) and keep whole duplicate
  groups on one side of the train/val split (`fcdata/curate.py` does this for v2.1).
- **Reply count**: the archive caps threads at 30 posts and the public crawl only got first pages. The new crawl
  should keep **all pages of story threads**; the continuation of a long story is the content we lack.
- **Keep provenance** (`thread_id`, `page_url`, `collected_at`, `post_ids`, label model/batch/hash) in every row
  so extraction can be audited. The dataset card was written straight from those fields.
- **Expect the crawl to overlap the archive**: the first 2026 crawl re-fetched 163k of 172k post IDs. Measure new
  tokens, not new threads, before sizing the run.

## Lessons: training

- **Short packed rows stall FlexAttention.** A leftover BFD row < 128 tokens sends dynamo down a path that unfuses
  attention for the rest of the run (12 → 28 s/step). `train.py` drops rows < `MIN_ROW_TOKENS=2048`; keep it.
  Diagnose with `FC_DYNAMO_STATS=1` and `py-spy dump`.
- **Alternatives measured**: SDPA 20.6 s/step, no gradient checkpointing 16.3 s/step but 23 GB (no room for eval),
  micro-batch 2 OOM. Flex + HF checkpointing stays.
- **LR**: 150-step sweep at 2e-5 / 5e-5 / 1e-4 was flat (2.159 / 2.154 / 2.168); clip 1 vs 10 no difference. Don't
  re-sweep; spend the GPU on data.
- **Still learning at the end.** Both v1 and v2 eval loss were falling at the end of epoch 1 with clipping active every
  step. With a lot more data the question is moot; with the same amount, 2 epochs is the cheap experiment.
- **8,192-token rows** (*untested*): 4.8% of story threads exceed 4k tokens but hold 19% of story characters; Gemma 3
  1B supports 32k with a 512 sliding window. Bench 50 steps at 8,192 (`--micro-batch 1`, halve `--grad-accum` to
  keep 65k tokens/step) before deciding. Expect slower steps and more memory; the gain is whole stories in one window.
- **Unsloth saves `final/` in fp32** (4 GB). Convert to bf16 before uploading (`runs/v2/final-bf16` shows how).
- **Never edit a running bash script** (`scripts/gpu_train.sh`): bash reads it by offset; v2 ended with
  `unexpected EOF` (harmless that time, the restore trap still ran).
- **GPU sharing**: `gpu_train.sh` stops `henad_inference` and `typology-graffiti` and restarts them on exit. Eval and
  export fit next to henad (23.2 of 24 GB during eval; lower `TOKENS_PER_BATCH` in `eval_ppl.py` if it OOMs).
- Launch long jobs with `setsid nohup`, never as agent background tasks. Never `pkill -f` with a pattern that matches
  your own shell.

## Lessons: generation and web

- Sampling in the web is temperature 0.8, top_p 0.95, repetition penalty 1.1. Long OPs drift into caps and
  laughter after ~150 tokens with every model so far; try 0.7 / 1.15 and a lower `max tokens por post` before blaming
  the model. Short replies, quoting and titles are fine.
- The browser path needs WebGPU with shader-f16 (Chrome/Edge desktop, Safari iOS 26, Chrome Android). Headless Chrome
  on this server has no f16, so test it from the laptop or phone.
- The public site is browser-only (`cf-connecting-ip` ⇒ no server GPU); Tailscale gets the server models too. New
  versions appear on the Tailscale side as soon as `data/web/mlc/<v>` exists, and in public once in `HF_REPOS`.
- `/api/curves` reads `checkpoint-*/trainer_state.json`; if checkpoints are deleted it falls back to parsing
  `runs/<v>.log`, which is sparser. Keep the last checkpoint dir (or copy its `trainer_state.json` next to `final/`).

## Plan for v3 (proposal)

1. When the crawl ends: curate with `fcdata.curate` (dedup groups, mention masking, all pages), label new openers with
   Haiku, measure **new tokens vs archive**.
2. Mix: archive + crawl, historia ×2, `tipo` header only, replay 8–10%, `--chunk`. If stories now exceed 4k often,
   run the 8,192 bench first.
3. Train from the base, not from v2 (a second stage on top of v2 is possible with `--init runs/v2/final
   --min-lr-rate`, but a clean run is easier to compare). Size: whatever the data gives; 1 epoch if ≥ 1B tokens,
   2 epochs if about the same as v2.
4. Eval on the shared held-out + samples; export; publish latest model + dataset (user's stance since 2026-10-20);
   register in the web; update `VERSIONS.md` and this file.

If the goal becomes long, funny stories rather than forum-wide text, the lever is a bigger model on rented GPUs
(Gemma 3/4 12B full FT on 2×H100 ≈ $80 was the estimate), keeping the 1B as the browser model.
