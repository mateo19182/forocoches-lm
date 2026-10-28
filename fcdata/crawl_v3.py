"""Turn the full-ID story crawl into pseudonymised posts and thread documents, then merge with v2.1.

    python -m fcdata.crawl_v3 docs --out data/processed/crawl-2026
    python -m fcdata.label --label-dir data/labels/v3 submit --threads data/processed/crawl-2026/docs.jsonl --name crawl --max-usd 20
    python -m fcdata.crawl_v3 merge --docs data/processed/crawl-2026/docs.jsonl --labels data/labels/v3 --out data/curated/v3

The crawl kept long posts (>=2000 own chars), the OP's other posts in those threads and short OP context.
Threads already in the archive or in curated v2.1 keep their earlier (complete first page) version.
"""
import argparse
import bisect
from collections import Counter
import hashlib
import json
from multiprocessing import Pool
from pathlib import Path
import re
import sqlite3
import time

from .build import alias, core_text, page_name_re
from .curate import deduplicate, eligible, own_op, read_rows
from .mix import is_val
from .parse import parse_post, title_tags

# Shorter OP follow-ups mostly answer replies the crawl did not keep.
FOLLOWUP_MIN_CHARS = 280
_PLACEHOLDER_RE = re.compile(r"\[cita=\x00(\d+)\x00\]")
_MENTION_RE = re.compile(r"(?<![\w@])@+(\w(?:[\w.-]{0,30}\w)?)")
# Quote headers the parser leaves as text ("Cita de nick [enlace showthread]").
_CITA_DE_RE = re.compile(r"Cita de (.{1,40}?) \[enlace showthread\]")
# Older pages put the quoted nick first in the post with no link: "#3 OP: Cita de nick quoted text".
_LEGACY_CITA_RE = re.compile(r"((?:^|\n)#\d+ (?:OP|forero_[a-z]+): Cita de )([^\s\[\]]+)(?= )")
_ESCRITO_RE = re.compile(r"(Escrito originalmente por) ([^\n\[\]]{1,40})")
_TITLE_SUFFIX_RE = re.compile(r"\s*[-|]\s*ForoCoches.*$", re.I)
_YEAR_RE = re.compile(r"-(\d{4})")


def thread_rows(tid, posts):
    """posts: dicts with id, position, author, html, title, posted_at, reason, page_url, observed_at."""
    posts = sorted(posts, key=lambda p: (int(p['position'] or 0), p['id']))
    op = next((p for p in posts if str(p['position']) == '1'), None)
    names = {op['author']: 'OP'} if op else {}
    post_author = {p['id']: p['author'] for p in posts}

    def name_for(user):
        if user not in names:
            names[user] = alias(sum(v != 'OP' for v in names.values()))
        return names[user]

    parsed = []
    for p in posts:
        post = parse_post(p['html'])
        who = name_for(p['author'])

        def resolve(m, post=post):
            q_user, q_post = post.quotes[int(m.group(1))]
            return f"[cita={name_for(post_author.get(q_post, q_user))}]"

        parsed.append((p, who, _PLACEHOLDER_RE.sub(resolve, post.text)))
    name_re = page_name_re(names)
    lowered = {k.lower(): v for k, v in names.items() if k} | {v.lower(): v for v in names.values()}

    def scrub(text):
        if name_re:
            text = name_re.sub(lambda m: names[m.group(1)], text)
        text = _ESCRITO_RE.sub(lambda m: f"{m.group(1)} {lowered.get(m.group(2).strip().lower(), 'usuario')}", text)
        text = _CITA_DE_RE.sub(lambda m: f"Cita de {lowered.get(m.group(1).lower(), 'usuario')} [enlace showthread]", text)
        return _MENTION_RE.sub(lambda m: '@' + lowered.get(m.group(1).lower(), 'usuario'), text)

    title = _TITLE_SUFFIX_RE.sub('', re.sub(r'\s+', ' ', posts[0]['title'] or '')).strip()
    out_posts, body, kept_ids = [], [f"TÍTULO: {title}"], []
    for p, who, text in parsed:
        text = scrub(text)
        own = len(core_text(text))
        out_posts.append(dict(thread_id=tid, post_id=p['id'], position=int(p['position'] or 0), author=who,
                              retention=p['reason'], posted_at=p['posted_at'], own_chars=own, text=text))
        if not text.strip() or (p['reason'] == 'op_followup' and own < FOLLOWUP_MIN_CHARS):
            continue
        body.append(f"#{len(body)} {who}: {text}")
        kept_ids.append(p['id'])
    year = _YEAR_RE.search(posts[0]['posted_at'] or '')
    doc = dict(thread_id=tid, n_pages=None, pages_bucket='unknown', tags=title_tags(title),
               n_replies_raw=len(posts) - 1, n_replies=len(kept_ids) - 1,
               op_chars=len(core_text(parsed[0][2])) if op else 0, has_op=bool(op),
               year=int(year.group(1)) if year else None, text='\n\n'.join(body), sources=['crawl-2026'],
               page_url=posts[0]['page_url'], collected_at=max(p['observed_at'] for p in posts), post_ids=kept_ids)
    return out_posts, doc


def _work(batch):
    stats, posts_out, docs = Counter(), [], []
    for tid, posts in batch:
        try:
            rows, doc = thread_rows(tid, posts)
        except Exception as e:  # noqa: BLE001 - one malformed page should not stop 345k threads
            stats[f'error_{type(e).__name__}'] += 1
            continue
        posts_out.extend(rows)
        docs.append(doc)
        stats['threads'] += 1
        stats['posts'] += len(rows)
        stats['doc_posts'] += len(doc['post_ids'])
        stats['doc_chars'] += len(doc['text'])
    return posts_out, docs, stats


def _batches(db, skip, size=200):
    batch, tid, posts = [], None, []
    rows = db.execute("""SELECT thread_id,id,json_extract(payload,'$.position'),json_extract(payload,'$.author'),
        json_extract(payload,'$.html'),json_extract(payload,'$.title'),json_extract(payload,'$.posted_at_raw'),
        json_extract(payload,'$.retention_reason'),page_url,observed_at FROM posts ORDER BY thread_id""")
    for t, pid, pos, author, html, title, posted, reason, url, observed in rows:
        if t != tid:
            if posts:
                batch.append((tid, posts))
                if len(batch) >= size:
                    yield batch
                    batch = []
            tid, posts = t, []
        if t in skip:
            continue
        posts.append(dict(id=pid, position=pos, author=author or f'unknown_{pid}', html=html, title=title,
                          posted_at=posted, reason=reason, page_url=url, observed_at=observed))
    if posts:
        batch.append((tid, posts))
    if batch:
        yield batch


def cmd_docs(args):
    skip = set() if args.keep_known else {r['thread_id'] for r in read_rows(args.archive_threads)} | {r['thread_id'] for r in read_rows(args.previous)}
    args.out.mkdir(parents=True, exist_ok=False)
    # Pool.imap consumes the row generator from its task-feeder thread.
    db = sqlite3.connect(f'file:{args.crawl}?mode=ro', uri=True, check_same_thread=False)
    stats = Counter(skipped_known_threads=len({t for (t,) in db.execute('SELECT thread_id FROM crawl_thread_posts')} & skip))
    with Pool(args.workers) as pool, (args.out/'posts.jsonl').open('w') as posts_file, \
            (args.out/'docs.jsonl').open('w') as docs_file:
        for n, (posts, docs, st) in enumerate(pool.imap(_work, _batches(db, skip), chunksize=1), 1):
            stats.update(st)
            posts_file.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in posts)
            docs_file.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in docs)
            if n % 100 == 0:
                print(json.dumps(dict(stage='docs', threads=stats['threads'])), flush=True)
    report = dict(created_at=time.time(), crawl=str(args.crawl), counts=stats, followup_min_chars=FOLLOWUP_MIN_CHARS,
                  skip='thread IDs in archive threads or curated v2.1 keep their earlier version')
    (args.out/'summary.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


_PSEUDONYM = re.compile(r"(OP|forero_[a-z]+|usuario)$")


def mask_unmapped(text):
    """Already-pseudonymised text: any @mention or quote header that is not a pseudonym becomes `usuario`."""
    keep = lambda name: name if _PSEUDONYM.match(name) else 'usuario'
    text = _ESCRITO_RE.sub(lambda m: f"{m.group(1)} {keep(m.group(2).strip())}", text)
    text = _CITA_DE_RE.sub(lambda m: f"Cita de {keep(m.group(1))} [enlace showthread]", text)
    text = _LEGACY_CITA_RE.sub(lambda m: m.group(1) + keep(m.group(2)), text)
    return _MENTION_RE.sub(lambda m: '@' + keep(m.group(1)), text)


def longest_own(row):
    """Dedup key for crawl docs: the longest post, since many openers are short context."""
    parts = re.split(r'\n\n(?=#\d+ (?:OP|forero_[a-z]+): )', row['text'])[1:]
    return max((core_text(p.split(': ', 1)[1]) for p in parts), key=len, default='')


class YearIndex:
    """Thread IDs grow with time: date an undated thread by its nearest crawled neighbour (98.8% exact, leave-one-out)."""
    def __init__(self, path):
        ref = sorted((r['thread_id'], r['year']) for r in read_rows(path) if r.get('year'))
        self.ids, self.years = [t for t, _ in ref], [y for _, y in ref]

    def __call__(self, tid):
        i = bisect.bisect(self.ids, tid)
        near = [j for j in (i - 1, i) if 0 <= j < len(self.ids)]
        return self.years[min(near, key=lambda j: abs(self.ids[j] - tid))]


def cmd_merge(args):
    year_of = YearIndex(args.year_ref)
    labels = {}
    for path in sorted(args.labels.glob('*.jsonl')):
        for lab in read_rows(path):
            labels[lab['thread_id']] = lab
    stats, rows, texts = Counter(), {}, {}
    for row in read_rows(args.previous):
        # v2.1 masked @mentions only in its HF export; labels keep their pre-mask text hashes.
        row['text'] = mask_unmapped(row['text'])
        rows[row['thread_id']] = row
        texts[row['thread_id']] = own_op(row)
        row.update(year=year_of(row['thread_id']), year_estimated=True)
        stats['previous_threads'] += 1
    excluded = (args.out/'excluded.jsonl')
    args.out.mkdir(parents=True, exist_ok=False)
    with excluded.open('w') as out:
        for row in read_rows(args.docs):
            tid, lab = row['thread_id'], labels.get(row['thread_id'])
            if lab and lab.get('tipo') is None and lab.get('decision') != 'exclude':
                lab = None
            row['label'] = lab
            if not eligible(lab):
                stats['excluded_' + ('unlabelled' if lab is None else 'quality_or_type')] += 1
                out.write(json.dumps(dict(thread_id=tid, reason='unlabelled' if lab is None else 'quality_or_type')) + '\n')
                continue
            rows[tid] = row
            texts[tid] = longest_own(row)
            stats['crawl_eligible'] += 1
    print(json.dumps(dict(stage='dedup_start', rows=len(rows), counts=stats)), flush=True)
    groups = deduplicate(rows, args.workers, texts)
    with (args.out/'threads.jsonl').open('w') as output, (args.out/'duplicates.jsonl').open('w') as duplicates:
        for group_id, members in sorted(groups.items()):
            best = max((rows[t] for t in members), key=lambda r: (r['label']['calidad'] == 'alta', r['n_replies'], -r['thread_id']))
            best.update(group_id=group_id, split='val' if any(is_val(t) for t in members) else 'train')
            best.pop('eligible', None)
            output.write(json.dumps(best, ensure_ascii=False) + '\n')
            stats['kept_' + best['split']] += 1
            stats['kept_' + best['label']['tipo']] += 1
            stats['kept_' + best['sources'][-1]] += 1
            for tid in members:
                if tid != best['thread_id']:
                    duplicates.write(json.dumps(dict(thread_id=tid, representative=best['thread_id'], group_id=group_id)) + '\n')
                    stats['duplicate_members'] += 1
    report = dict(created_at=time.time(), counts=stats, inputs=dict(previous=str(args.previous), docs=str(args.docs), labels=str(args.labels)),
                  dedup='v2.1 rows keyed by own opener, crawl rows by their longest post; exact + MinHash 5-word Jaccard >=0.8; any validation member makes the group validation',
                  filter='media/alta historias, preguntas, debates, humor, hipoteticos')
    (args.out/'summary.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('docs')
    d.add_argument('--crawl', type=Path, default=Path('data/stories-long-2026-10-19/crawl.sqlite'))
    d.add_argument('--archive-threads', type=Path, default=Path('data/processed/threads.v0.jsonl'))
    d.add_argument('--previous', type=Path, default=Path('data/curated/v2.1/threads.jsonl'))
    d.add_argument('--out', type=Path, required=True)
    d.add_argument('--workers', type=int, default=24)
    d.add_argument('--keep-known', action='store_true', help='also export threads already in the archive or v2.1 (full crawl release)')
    m = sub.add_parser('merge')
    m.add_argument('--previous', type=Path, default=Path('data/curated/v2.1/threads.jsonl'))
    m.add_argument('--docs', type=Path, required=True)
    m.add_argument('--labels', type=Path, required=True)
    m.add_argument('--out', type=Path, required=True)
    m.add_argument('--year-ref', type=Path, default=Path('data/processed/crawl-2026-all/docs.jsonl'))
    m.add_argument('--workers', type=int, default=16)
    args = ap.parse_args()
    {'docs': cmd_docs, 'merge': cmd_merge}[args.cmd](args)


if __name__ == '__main__':
    main()
