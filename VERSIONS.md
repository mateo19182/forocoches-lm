# Dataset and model versions

Dataset completion verified 2026-10-18; publication cleanup checked local final-model configs on 2026-10-19. Dataset versions and model versions are separate: the curated v2/v2.1 datasets below are not the broad archive mix used by the model run named v2. Hugging Face releases (2026-10-20, user's decision: latest model and dataset only): `mateo-19182/forocoches-lm-v2` (bf16 Transformers checkpoint of model run v2), `mateo-19182/forocoches-lm-v2-q4f16_1-MLC` (its WebLLM browser weights), `mateo-19182/forocoches-threads-v2.1` (curated v2.1 `threads.jsonl` + `summary.json`, with remaining `@mentions` masked to `@usuario`; no raw pages or SQLite). `mateo-19182/forocoches-lm-v1-q4f16_1-MLC` (2026-10-19) stays up for the web's v1 option. v0/v1 full checkpoints and the other datasets are not released.

| Version | Data and selection | Training size | Model status |
|---|---|---|---|
| v0 | Historical archive; cleaned threads, original labels, weighted sampling, OP dedup, general-Spanish replay | 51.6M estimated tokens | Final checkpoint exists; evaluated against base |
| v1 | Same historical source and filtering as v0; larger weighted sample and replay pool | 269.8M estimated tokens | Final model config exists locally; evaluated against base/v0 |
| curated v2 dataset | Historical archive merged with a frozen public crawl snapshot; media/alta only; stories, questions, debate, humour and hypotheticals | 55,392,861 measured training completion tokens | Superseded by curated v2.1; no training launched by this build |
| curated v2.1 dataset | Completed crawl snapshot; new/changed openers relabelled, plus a bounded audit of existing stories | 56,533,981 measured training completion tokens | Dataset complete and validated; no training launched |
| curated v3 dataset | v2.1 plus the full-ID long-post crawl (2003–2026), Haiku labels, nick masking, `año` header | 545,517,660 measured completion tokens in `data/mix/v3` (8192 context, stories ×2) | Model run v3 training on Vast.ai (RTX 5090) from v2 |

V0/v1 counts use the old character-based estimate; v2 uses the local Gemma tokenizer. These are not identical token accounting methods. V2 selects distinct chunks at 60% stories, 30% conversation and 10% general Spanish. It is smaller because the distinct story pool limits this ratio. V0/v1 use weighted repetitions and include low-quality labels, news and other threads (sales excluded).

## Broad v2 model run and new discovery

The 2026-10-19 training notes identify model run `runs/v2` with `data/mix/broad`: an archive-based, chunked, weighted mix (about 496M estimated tokens). This is not the 56.53M measured completion-token curated v2.1 mix. The run finished 2026-10-20 04:25 (7,654 steps, 26.3 h, final eval loss 1.992). Held-out completion perplexity on the shared 4,873-thread validation set (`samples/eval_v2.md`, header `tipo` only): base 11.11, v1 7.08, v2 6.81; general-Spanish control 13.19 / 12.82 / 13.03. Recipe and lessons: [docs/NEXT-RUN.md](docs/NEXT-RUN.md).

The 2026 full-range crawl (see [docs/DATA-WISHLIST.md](docs/DATA-WISHLIST.md)) is running; its output is not yet a released dataset or an input to these model runs.

## Categorisation and cleaning

The original label script configures Anthropic batch requests with `claude-haiku-5-5`. It classifies type and quality from the title, up to 1,500 OP characters and three replies of up to 150 characters each. See `fcdata/label.py`; batch IDs and outputs are under `data/labels/`. Batch metadata does not pin the model/prompt hash, so current code and README are the available model provenance, not a per-result model receipt.

V2 submitted no new labelling requests. It reuses labels only when normalized own OP text is unchanged. Changed OPs enter review while the labelled archive revision remains eligible. New/unlabelled OPs await review. The initial v2 review queue contained 649 rows; two apparent story mislabels were recorded in `data/curated/v2/review-findings.json`. The original labels remain provisional.

V2 retains 171,231 threads, including 20,455 labelled stories. Among retained refreshed threads, 2,585 post IDs are absent from the archive. It verifies actual shingle similarity after approximate duplicate candidate discovery, groups duplicates before splitting, and preserves old validation membership. Long documents become token-bounded continuation chunks rather than losing their endings to the old 13,000-character cutoff. Full mix validation found no group overlap and all rows within the 4,096-token limit.

## Crawl versus dataset snapshot

The completed public crawl has 8,037 threads, 172,432 posts and 13,307 long-post candidates. These are collected counts, not novel or accepted training examples. Most discovery targeted archive thread IDs. Queue exhaustion means eligible work finished: 67 deferred pages, 174 held sources and restricted/missing pages remain outside collected coverage.

The v2 snapshot contains 142,713 fetched posts (135,935 existing IDs and 6,778 archive-absent IDs). It predates crawl completion by 29,719 collected posts. Those final posts have not been incorporated into v2. Freeze a new output version when refreshing; do not overwrite v2 or v1 inputs.

## Evidence and remaining work

- Dataset recipes: README, `fcdata/build.py`, `label.py`, `dedup.py`, `mix.py`, `curate.py`, `mix_v2.py`.
- Counts: `data/mix/{v0,v1,v2}/summary.json`, `data/curated/v2/summary.json`.
- V2 audit: curated and mix `validation.json`, review sample/findings, paired train/val manifests, frozen `public-snapshot.sqlite`.
- V0 evaluation: `samples/eval_base_v0.md` and `.json`. FC completion perplexity improves from 11.07 to 7.65; this does not establish story-writing quality. Use the same held-out set for subsequent model comparisons.
- Training configuration/checkpoints: `train.py`, `runs/v*/checkpoint-*/trainer_state.json`, final checkpoints where present. Generated model cards are boilerplate, not experiment records; runtime lessons are in docs/NEXT-RUN.md.

The completed crawl and bounded label review are now incorporated in v2.1. Model comparison against base/v0 on shared evaluation prompts and holdout remains separate work. For future builds, record the exact command, code revision, input checksums, label model/prompt version, training configuration and evaluation paths together. Existing versions do not yet have all of those provenance fields.

## V2.1 build (2026-10-17)

The final crawl snapshot is `data/curated/v2.1-review/public-snapshot.sqlite`: 172,432 posts, of which 9,084 post IDs are absent from the entire archive (2,684 threads contain new posts). The preparation counter of 9,169 checks membership within each matching thread, not globally; 85 of those IDs already occur elsewhere in the archive. Preparation found 770 openers requiring labels. The review adds 100 existing stories per quality tier and two known suspects (202 audit rows), for 972 batch requests. The sample is targeted and stratified, not a population-weighted error estimate.

The review uses the existing configured Claude model, a 6,000-character OP excerpt, and an explicit instruction to distinguish personal stories from pasted articles and guides. It still sees excerpts, not every full thread. Every returned label records the actual response model, batch ID, text SHA256 and prompt SHA256. Overrides apply only to the exact reviewed text; otherwise the previous label policy remains in force. Model relabelling is not human ground truth.

Commands used:

```sh
.venv/bin/python -m fcdata.curate --out data/curated/v2.1-review --prepare-only --workers 2
.venv/bin/python -m fcdata.review --queue data/curated/v2.1-review/needs-labels.jsonl --out data/labels/v2.1
# API credentials are loaded from the local .env, never stored in reports.
.venv/bin/python -m fcdata.label --label-dir data/labels/v2.1 --op-max-chars 6000 --audit-stories submit --threads data/labels/v2.1/threads.jsonl --name review --max-usd 1
.venv/bin/python -m fcdata.finish_review --version v2.1
```

The submit budget is checked using the script's existing price estimates, not a provider-enforced spending cap. The follow-up originally ran as the user service `forocoches-v2-1.service`, at low CPU priority. It waits up to 24 hours for the batch, stops on incomplete results, builds `data/curated/v2.1` and `data/mix/v2.1`, then validates every mix row. It does not train a model or send notifications.

Build artifacts: `data/labels/v2.1/build-status.json`; log: `runs/v2.1-build.log`. Once available, `audit-report.json` records label changes and `build-provenance.json` records commands/input hashes, with copies of pipeline source under `data/labels/v2.1/source/`. The mix is ready for inspection only when status is `complete` and `data/mix/v2.1/validation.json` exists. Failures leave status `failed`; do not blindly rerun into existing output directories.


### V2.1 completion repair (2026-10-18)

The original batch returned 953 valid classifications and 19 explicit classifier refusals. Refusals now produce an explicit `decision: exclude` record bound to the reviewed text, not a fabricated category or quality label. They remain in the review ledger, are excluded from training, and do not block the build as unresolved requests. No replacement batch was submitted. The original batch usage estimate is $0.096; recollecting its results does not incur a second inference charge.

The 202-row story audit changed 93 type and/or quality labels. This is a targeted sample, not a measured population error rate. The final build was resumed interactively with `fcdata.finish_review --version v2.1`; its log is `runs/v2.1-finish.log`. Whole-archive post-ID novelty is recorded in `data/labels/v2.1/novelty.json`.

The archive-only training recipe discussed separately is not the v2.1 recipe: v2.1 merges accepted public posts, excludes low-quality labels, and targets 60% stories / 30% conversations / 10% replay without oversampling. Most source material still comes from the archive; the crawl does not constitute a substantially larger corpus.


### Completed and validated (2026-10-18)

Curated v2.1 retains 171,248 threads (169,556 train; 1,692 validation), including 20,465 stories. It applies 968 exact-revision review decisions; four audit records do not match current text. All 19 classifier-refused thread IDs are absent from the curated output (18 appear in ordinary exclusion counts; the other is an ineligible validation guard). The pending-label file is empty.

The selected mix has 45,929 training chunks and 56,533,981 completion tokens: 33,919,304 story, 16,960,803 conversation, 5,653,874 replay. Validation has 2,228 chunks and 2,356,095 completion tokens. Full validation passed: maximum sequence 4,090 tokens, zero overlapping groups/replay IDs/normalized long completions, matching manifests and token counts. Build status is `complete`.

Retained source provenance references 5,959 globally new post IDs across 1,819 threads; this is not a novel-token count. Of those threads, 1,069 are represented in selected training chunks and 10 in validation, which does not imply every new post in each thread is present in selected chunks. The increase over v2 is 1,141,120 training tokens, including selection/filter changes. No model training was started by this build.


## V3 dataset and releases (2026-10-27)

Source: the oldest-first full-ID crawl `data/stories-long-2026-10-19` (IDs 1–10,841,474, first pages, long posts ≥2,000 own chars plus opener context/follow-ups). `fcdata/crawl_v3.py docs` builds pseudonymised posts and thread documents; threads already in the archive or v2.1 keep their earlier version. A parser fix (`parse_post` now uses the crawled `post_message` div; its first `<td>` was a quote box) recovers the own text of 710k posts. `@mentions` and plain-text quote headers naming users outside the thread become `usuario`, now also in v2.1-origin rows (v2.1 had masked mentions only in its HF export and left ~4.8k `Cita de <nick>` headers).

Labels: Haiku 5.5 batches, title + 1,500 opener chars + three short replies, 325,058 threads, $14.57 including $1.82 for a cancelled batch built from mis-parsed text. `fcdata/crawl_v3.py merge` → `data/curated/v3`: 322,896 threads (81,834 historia), year from the crawl or estimated from the nearest crawled thread ID (98.8% leave-one-out). Archive IDs 7.0–8.0M are 2019–2020.

Mix: `fcdata.mix_v2 --fields tipo año --max-tokens 8192 --story-repeats 2 --budget stories=all --budget conversation=all --budget archive_conversation=90e6 --budget replay=all` with `data/replay/fineweb2_spa_240m.jsonl`: stories 279.9M, crawl conversation 119.8M, archive conversation 90.0M, replay 55.8M.

HF: published `mateo-19182/forocoches-threads-v3` (curated pool) and `mateo-19182/forocoches-crawl-2026` (all 345,039 crawled threads / 1,424,210 posts as Parquet, labels where available). `forocoches-threads-v2.1` was deleted at the user's request; v3 contains all of its threads. Models v1/v2 stay up for the web.
