# Data we want next

Updated 2026-10-19 after the public discovery pilot and authenticated access test.
The user selected **standalone funny stories of 800–1,500 words** as the next model's primary behaviour.
Keep every collected post in the source archive; select and format training examples separately.

## Historical baseline, 2026-10-18

| | threads | tokens |
|---|---:|---:|
| Archive (Atenrev dump), ids 7,000,000–7,999,998, ~2018–2019 | 556k | 407M unique after cleaning |
| of which stories (`historia`) | 24k | 33M |
| Public crawl 2026-10-16 (mostly re-fetched archive threads) | 8k | +1.2M new |

The historical training source is concentrated in one forum era. Those added-token figures predate the outside-archive pilot below.

## Verified discovery checkpoint, 2026-10-19

The outside-archive pilot stopped at its 2 GB response budget. It contains 141,052 posts from 2,131 threads;
138,598 post IDs are absent from both historical and prior-crawl baselines. Among these are 3,121 distinct cleaned
texts of at least 2,000 characters. These are candidate counts, not verified stories, humour labels or text novelty
against the historical corpus.

Across all IDs, the pilot has 400 long openers, 1,350 long replies by the original author, and 1,487 long replies by
other authors. Author identity does not prove a continuation. These counts show why opener-only discovery would
miss substantial material. Some very long openers are lists or discussions rather than stories.

The new account passed one normal direct login and the protected-page check on thread 10841302: guest HTTP 302,
authenticated HTTP 200, 20 posts (19 with text, one with images). Parser v7 handles its legacy logged-in layout.
This establishes ordinary private access for that page; it does not establish +PRV eligibility.

An offline manifest combines source-provenanced, outside-archive first-page candidates from both crawls:

| Pool | Distinct candidates |
|---|---:|
| Ordinary private information redirect | 511 |
| Unseen, after removing overlap with that pool | 800 |
| +PRV eligibility redirect | 35 |
| Other permission notice | 1 |

Local plan and seed manifest: `data/story-crawl-plan-2026-10-19/`. They are Git-ignored. Candidate titles and
compilation membership are discovery evidence, not quality judgements.

## 1. Threads outside the archive id block (highest value)

Threads below 7,000,000 or above 8,000,000 broaden source coverage. New IDs do not establish new text: reposts and
quoted copies require text deduplication. Priorities, in order:

1. **Story threads from any year.** The goal is funny, original stories; we have 24k. Doubling that is worth more than
   anything else on this list. Sources that concentrate stories:
   - recopilation threads ("recopilación de historias", "hilos míticos", "mejores hilos del año") link to hundreds each;
   - titles with `+HD`, `[HD]`, `historia`, `os cuento`, `mi experiencia`, `storytime`;
   - the first page of the General subforum sorted by replies, for old years.
2. **Story continuations and stories in replies.** Fetch beyond the opener, retain all replies, and promote
   narrative continuations by any author. Initial length and title hints only prioritise inspection. Track fetched
   page coverage and pending pages explicitly; a link to the last page does not establish completion.
3. **Later years (2020–2026).** The archive stops in 2019. Register, memes and references drift; the model currently
   writes a 2019 forum.
4. **Earlier years (2004–2017)** for breadth, lower priority: older slang, but many of the classic stories live there.

The earlier +300M overall / +50M story-token wishlist is an aspiration, not a forecast or launch requirement.
Measure accepted, deduplicated narrative tokens per request before increasing budgets.

## Current collection scope: oldest-first, long/OP retention, AirVPN

Latest steering: the user wants the complete numeric range scanned in under 30 days, continuing even if the
500,000-retained-post milestone is reached first. The active run fetches **first pages only**, with twenty-four workers
sharing one 0.020000000001-second start interval (50 total requests/second), following the user-authorised staged speed trials and subsequent request for 50/s. Four CPU processes run the original parser, while network requests retain one shared limiter and SQLite retains one writer. Previously pending continuations remain
`deferred`; collected content is preserved. Later-page OP continuations are missed under this policy. Staged live
samples are under the active root as `rate-stage-*rps.json`; the five-request stage measured 4.85 IDs/second with
no access blocks, projecting about 26 days if sustained. A later sustained sample measured 4.57 IDs/second. Continuous scheduling now removes the whole-batch wait when a request is slow; the six-request trial measured 5.88 IDs/second without blocks. The eight-request trial measured 7.76 IDs/second without blocks, projecting about 16 days if sustained. A later VPN-container recreation stranded the collector in its old network namespace. Recreating the collector restored access; tunnel/HTTP-response checks now stop and alert on this condition. Connection-error pages were requeued with attempt history preserved. The thread-prefix OP-recovery query avoids full completed-page scans. Subsequent ten/twelve-request trials measured 9.61/11.39 IDs/second without blocks, projecting roughly 11 days at the higher rate if sustained. The user then requested around 25 IDs/second. Four CPU parser processes retain the original parser and one database writer. The twenty-request stage measured 19.61 IDs/second; the 25-request stage measured 24.54, and its 204-second follow-up measured 24.68 across 5,037 requests with no access blocks, projecting roughly five days if sustained. The next 30/35-request trials measured 29.21/33.31 requests per second without blocks. A 150-second follow-up at the 35-request cap measured 34.20 resolved IDs/second across 5,118 responses with no access blocks, projecting about 3.59 days remaining if sustained. The user then requested 50/s. The collector now has a 50/s shared cap and 24 fetch workers; its 150-second follow-up measured 33.76 resolved IDs/second across 5,057 responses with no access blocks, projecting about 3.31 days remaining if sustained. The observed rate remains below the cap as local processing and I/O vary. Current trial results are in `rate-pipeline-*rps*.json`. The previous all-page settings below are historical.

The user replaced link-only discovery with a systematic oldest-first numeric thread scan and requested retaining
only long posts plus OP continuations. They confirmed **500,000 retained long/OP posts** as the milestone, with the full ID range taking precedence.
Classification comes later; neither story keywords nor humour labels gate the numeric scan.

The new root is `data/stories-long-2026-10-19/`. Filtering the stopped 147,396-post source crawl retained 17,589 IDs:
3,330 long own-text posts, 97 short OP context posts and 14,162 short OP follow-ups. The original full-post crawl,
historical archive and immutable raw responses remain intact. Retain >=2,000 own-text characters from any author;
retain shorter OP sections only where that OP has a long post. OP follow-ups are candidates, not verified story
continuations. A later long OP section triggers recovery of earlier short sections from saved raw pages with their
original collection URLs and times. Ordinary short replies are omitted from the parsed corpus; full response HTML
still preserves them for audit.

The persisted scan walks IDs **1 through 10,841,474**, the fixed frontier supplied by the user. A 32-ID staging
window prevents enqueueing millions of URLs. Known outcomes are reused; the historical 7M–8M block is included.
All worker slots take the oldest queued scan IDs. Invalid/deleted threads remain explicit missing outcomes, and
login/rate/challenge stops still apply. The range can extend without resetting its cursor. Complete-ID mode skips
linked-thread graph processing and refreshes exports on exit; status updates each batch using incremental counters.

The worker runs inside the existing AirVPN/WireGuard `slskd-vpn` network namespace. Checks confirmed distinct VPN
egress, route device `tun0`, outbound DROP policy and authenticated HTTP 200 on protected thread 10841302. There
is no direct fallback and no change to the server's other routing. Systemd supervises the Docker worker; the local
health timer checks its container rather than its namespace-local PID. The user subsequently authorised Telegram
monitoring: a separate minute timer sends alerts, recovery messages, milestone/completion notifications and daily
progress through Hermes. It does not change collection settings or resume access stops.

The active limits are 12 million total attempts, 2.5 TB decoded responses, 12 million discovered pages/candidates
and 30 days, plus a 15 GB free-disk reserve on both storage filesystems. Raw gzip HTML is on the data HDD via
`raw` symlink; SQLite stays on SSD. A 200-page sample projected roughly 297 GB compressed HTML for the full range;
this may vary substantially across eras. The old SSD raw directory remains preserved. Budgets and cursor persist
across restarts; access stops do not automatically reset. One configured proxy had a historical HTTP 403 login-page
failure and is not used. Backup accounts are unnecessary for the measured rate, and rotation must not bypass stops.
Status distinguishes retained long posts, retained OP context/follow-ups, scanned pages and page post observations.
These are collection counters, not accepted training-story counts. See README for commands and output schema.

## Earlier collection scope (superseded)

The user subsequently requested scraping only, targeting **500,000 distinct posts total**, including the existing
141,052 posts. This supersedes the bounded 500-opener proposal below. The new independent resumable run lives in
`data/stories-2026-10-19/`, using a SQLite snapshot of the novel pilot and hard-linked immutable raw pages. Original
crawl files and the historical archive are unchanged. All fetched posts are retained; no labelling or training runs
are part of this collection task.

The account, authenticated legacy discovery, persistent target stop and reply-based long-text promotion are wired
through the collector. Explicit `pp=30` was confirmed live, and every imported post with a recorded position fits
the same 30-post page boundaries. Known ordinary-private restrictions can now be retried without mixing page sizes;
known +PRV restrictions remain outside the active queue. Actual request URLs are retained in fetch events.

Run settings: two workers, one shared two-second start interval, up to 30,000 admitted candidates, 100,000 total
attempts, 30 GB decoded responses, 250,000 queued pages or seven days. The post target includes imported IDs and is
checked between batches. Counts describe collected posts, not 500,000 stories or novel training examples. CLI host
locking prevents a second collector/login process from overlapping this run.

Systemd service: `forocoches-story-crawl.service`; local health timer: `forocoches-story-health.timer`. Access pauses
exit normally and do not restart automatically. Crash restarts are limited to three per 15 minutes. Status, local
alerts, source/long-post exports and the import provenance are under the new data root. No remote alerts are enabled.

## Earlier small-pilot proposal (superseded)

Start with 500 new openers, drawing mainly from ordinary-private candidates and unseen compilation links. Reserve
an exploration share for recent years, new authors and weaker title hints, so famous old threads do not dominate.
Collect continuations within a total budget of 2,000 requests, 1 GB response bodies or four hours, whichever comes
first. Use one worker with a five-second delay and no other concurrent collector. Partial threads remain pending;
all posts on every visited page are retained. +PRV candidates stay outside the initial queue.

Before launch, wire the saved session through discovery, fetching and authenticated permission classification.
Discovery must use the same legacy message-tag normalisation as post extraction: normalising only extracted
posts leaves compilation links invisible. The existing bulk discovery worker currently creates guest sessions.

Review a stratified sample before scaling. Annotate narrative, humour, coherence, originality/copy suspicion and
completeness independently. A funny story can have both narrative and humour labels; the old exclusive
`tipo=historia|humor` categories cannot express this. Inspect the full story or continuation, including the ending,
rather than labelling from a short opener excerpt. Calibrate any automated scorer against human judgements.
External paid labelling is a separate step, not part of this pilot plan.

## 2. What each thread must carry

The pipeline (`fcdata/parse.py`, `build.py`) needs, per thread: id, title, subforum, page count; per post: id,
position, author handle (hashed later), body as parsed text **and** raw HTML, quoted post ids, date. Raw HTML stays
in the data directory, never in git. Without raw HTML we cannot re-parse when the cleaner changes, and without
quote ids the `[cita=...]` structure is lost.

## 3. Constraints (from AGENTS.md)

The user has authorised testing authenticated private access and planning the new crawl. Use one collector per
data directory, preserve host-wide pacing, and stop on login failures, rate limits and challenge pages. Ordinary
private access does not imply +PRV access. Keep credentials/cookies private and ignored; retain original raw HTML,
source IDs, URLs and collection times. The historical archive remains read-only.

## 4. What we do not need

- Blind relabelling of the whole archive. The `calidad` conditioning field showed little effect in
  `samples/cond_v1_first256.md`; this does not establish that training-data quality selection is ineffective.
  New narrative/humour labels need their own calibration.
- Replay text: `data/replay/fineweb2_spa_240m.jsonl` covers 8% replay up to ~800M FC tokens.
- Threads from `compraventa`; they are excluded from training.

## 5. How new data enters a run

Freeze a separate dataset version. Build two views from the retained source posts: standalone narrative segments,
with confirmed continuations kept in order, and contextual forum exchanges. Preserve paragraphs, spelling, voice
and meaningful smilies. Do not rewrite stories into generic prose or concatenate every author reply as narrative.
Retain source post references for each assembled segment and keep image-dependent/incomplete material explicit.

Deduplicate normalised text and near-duplicate narrative passages across all corpora before splitting or chunking;
opener deduplication alone misses copied stories in replies. Keep duplicate groups and every chunk of a story in
one split. Include unseen-author and modern-thread evaluation slices, and prevent prolific authors or huge threads
from dominating the token budget.

An initial experimental mix is 70% actual narrative completion tokens, 20% selected conversation and 10% general
Spanish. Whole threads labelled `historia` currently include replies in the story pool; those shares do not measure
narrative content. Compare mixes at the same token budget rather than treating this proposed ratio as optimal.

Validate formatted examples with the actual Gemma tokenizer against the current 4,096-token training limit. Use
longer stories as complete source material and retain continuations when chunking; never label a truncated ending
as complete. Evaluate fixed prompts for humour, consistency, satisfying endings, repetition and copied passages,
with blind human comparisons alongside held-out loss. More collected tokens alone do not establish a better model.

Local length calibration found 119 cleaned openers in the selected 800–1,500-word band: 1,019–2,306 Gemma tokens,
median 1,451. All fit below 4,000 tokens before formatting. This supports testing the chosen length with the current
context window; it does not classify those openers as stories or prove generation quality. Full measurements are
in the ignored local plan JSON.
