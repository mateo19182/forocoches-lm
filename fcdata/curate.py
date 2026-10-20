"""Merge an archive and a public snapshot, filter labels, and group duplicates."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from multiprocessing import Pool
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from datasketch import MinHash, MinHashLSH
from bs4 import BeautifulSoup

from .build import build_thread, core_text
from .mix import is_val
from .parse import parse_post

POSTS = re.compile(r'\n\n(?=#\d+ (?:OP|forero_[a-z]+): )')
OTHER_TYPES = {'pregunta', 'debate', 'humor', 'hipotetico'}


def read_rows(path):
    with path.open() as source:
        yield from map(json.loads, source)


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def own_op(row):
    parts = POSTS.split(row['text'])
    return core_text(parts[1].split(': ', 1)[1]) if len(parts) > 1 else ''


def eligible(label):
    return bool(label and label.get('decision') != 'exclude' and label['calidad'] in {'alta', 'media'}
                and label['tipo'] in OTHER_TYPES | {'historia'})


def reviewed_label(row, labels):
    label = labels.get(row['thread_id'])
    if label and label.get('text_sha256') == hashlib.sha256(row['text'].encode()).hexdigest():
        return label
    return None


def shingles(text):
    words = re.findall(r'\w+', normalized(text))
    return {' '.join(words[i:i+5]) for i in range(max(1, len(words)-4))}


def signature(item):
    tid, text = item
    mh = MinHash(num_perm=64)
    values = list(shingles(text))
    for start in range(0, len(values), 256):
        mh.update_batch([s.encode() for s in values[start:start+256]])
    return tid, mh


def merge_thread(archive, tid, fresh, stats, root):
    old = archive.execute('SELECT id,post_id,username,n_pages,title,body FROM threads WHERE id=?', (tid,)).fetchone()
    op = next((p for p in fresh if p.get('position') == 1), None)
    if op is None:
        stats['fresh_without_op'] += 1
        return None, False
    if old:
        title, pages = old[4], old[3]
        comments = {pid: (pid, user, body) for pid, user, body in archive.execute(
            'SELECT post_id,username,body FROM comments WHERE thread_id=?', (tid,))}
        unchanged = normalized(core_text(parse_post(old[5]).text)) == normalized(core_text(parse_post(op['html']).text))
    else:
        comments = {}
        unchanged = False
        pages = None
        digest = op['_sha256']
        raw = gzip.decompress((root/'raw'/digest[:2]/(digest+'.html.gz')).read_bytes())
        soup = BeautifulSoup(raw.decode('utf-8', errors='replace'), 'html.parser')
        title = soup.title.get_text(' ', strip=True) if soup.title else ''
        title = re.sub(r'\s*[-|]\s*ForoCoches.*$', '', title, flags=re.I)
    old_ids = set(comments) | ({old[1]} if old else set())
    for p in fresh:
        stats['fresh_existing_post_ids' if p['id'] in old_ids else 'fresh_new_post_ids'] += 1
        if p['id'] != op['id']:
            comments[p['id']] = (p['id'], p.get('author') or f'unknown_{p["id"]}', p['html'])
    comments.pop(op['id'], None)
    row = build_thread((tid, op['id'], op.get('author') or f'unknown_{op["id"]}', pages, title, op['html']),
                       sorted(comments.values()), Counter())
    if row:
        if pages is None:
            row['pages_bucket'] = 'unknown'
        row.update(sources=['archive', 'public'] if old else ['public'],
                   page_url=op['_url'], collected_at=max(p['_observed'] for p in fresh),
                   post_ids=[op['id']] + sorted(comments), label_reused=unchanged)
    return row, unchanged


def deduplicate(rows, workers=2):
    parent = {tid: tid for tid in rows}

    def find(tid):
        while parent[tid] != tid:
            parent[tid] = parent[parent[tid]]
            tid = parent[tid]
        return tid

    def union(a, b):
        a, b = find(a), find(b)
        parent[max(a, b)] = min(a, b)

    texts = {tid: own_op(r) for tid, r in rows.items()}
    exact = {}
    for tid in sorted(rows):
        # Generic short openers can have unrelated reply conversations.
        text = texts[tid] if len(texts[tid]) >= 200 else rows[tid]['text']
        key = hashlib.sha256(normalized(text).encode()).hexdigest()
        if key in exact:
            union(tid, exact[key])
        else:
            exact[key] = tid
    lsh = MinHashLSH(threshold=0.7, num_perm=64)
    work = ((tid, texts[tid]) for tid in sorted(rows) if len(texts[tid]) >= 200)
    with Pool(workers) as pool:
        for n, (tid, mh) in enumerate(pool.imap(signature, work, chunksize=32), 1):
            hits = lsh.query(mh)
            a = shingles(texts[tid]) if hits else set()
            for other in hits:
                other = int(other)
                if find(tid) == find(other):
                    continue
                b = shingles(texts[other])
                if len(a & b) / len(a | b) >= 0.8:
                    union(tid, other)
            lsh.insert(str(tid), mh)
            if n % 10000 == 0:
                print(json.dumps(dict(stage='dedup',indexed=n)), flush=True)
    groups = defaultdict(list)
    for tid in rows:
        groups[find(tid)].append(tid)
    return groups


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive', type=Path, default=Path('data/fc_dataset_7-8.sqlite'))
    ap.add_argument('--threads', type=Path, default=Path('data/processed/threads.v0.jsonl'))
    ap.add_argument('--public', type=Path, default=Path('data/public-2026-10-16'))
    ap.add_argument('--labels', type=Path, default=Path('data/labels'))
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--review-labels', type=Path)
    ap.add_argument('--prepare-only', action='store_true')
    ap.add_argument('--snapshot', type=Path, help='Reuse a frozen crawl DB; raw HTML still comes from --public')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    stats = Counter()
    reviews = {}
    if args.review_labels:
        for lab in read_rows(args.review_labels):
            reviews[lab['thread_id']] = lab
    labels = {}
    for path in sorted(args.labels.glob('*.jsonl')):
        for lab in read_rows(path):
            labels[lab['thread_id']] = lab
    source_path = args.snapshot or args.public/'crawl.sqlite'
    source = sqlite3.connect(f'file:{source_path}?mode=ro', uri=True)
    snap = sqlite3.connect(args.out/'public-snapshot.sqlite')
    source.backup(snap)
    source.close()
    fresh = defaultdict(list)
    for tid, url, payload, observed, digest in snap.execute('SELECT x.thread_id,x.page_url,x.payload,x.observed_at,p.sha256 FROM posts x JOIN pages p ON p.url=x.page_url ORDER BY x.thread_id,x.id'):
        p = json.loads(payload)
        p.update(_url=url,_observed=observed,_sha256=digest)
        fresh[tid].append(p)
    snap.close()
    archive = sqlite3.connect(f'file:{args.archive}?mode=ro', uri=True)
    merged = {}
    review_ids = set()
    review = (args.out/'needs-labels.jsonl').open('w')
    excluded = (args.out/'excluded.jsonl').open('w')
    for n, (tid, posts) in enumerate(fresh.items(), 1):
        row, unchanged = merge_thread(archive, tid, posts, stats, args.public)
        merged[tid] = row
        if row and not unchanged and not reviewed_label(row, reviews):
            row['label'] = None
            review.write(json.dumps(row, ensure_ascii=False)+'\n')
            review_ids.add(tid)
            stats['needs_labels'] += 1
        if n % 1000 == 0:
            print(json.dumps(dict(stage='merge',threads=n)), flush=True)
    archive.close()
    fresh.clear()
    rows = {}
    seen = set()

    def admit(row, label):
        tid = row['thread_id']
        reviewed = reviewed_label(row, reviews)
        if reviewed:
            label = reviewed
            stats['review_labels_applied'] += 1
        row['label'] = label
        row.setdefault('sources', ['archive'])
        if label is None and tid not in review_ids:
            review.write(json.dumps(row,ensure_ascii=False)+'\n')
            review_ids.add(tid)
            stats['needs_labels'] += 1
        row['eligible'] = eligible(label)
        # Retain every previous validation thread as a leakage guard, even if filtered.
        if row['eligible'] or is_val(tid):
            rows[tid] = row
        else:
            reason = 'needs_labels' if label is None else label.get('reason', 'quality_or_type')
            excluded.write(json.dumps(dict(thread_id=tid,reason=reason))+'\n')
            stats['excluded_'+reason] += 1

    for old in read_rows(args.threads):
        tid = old['thread_id']
        seen.add(tid)
        stats['archive_processed_threads'] += 1
        if tid in merged and merged[tid]:
            update = merged.pop(tid)
            row = update if update['label_reused'] or reviewed_label(update, reviews) else old
            lab = labels.get(tid)
            if row is old:
                stats['archive_version_kept_pending_review'] += 1
            stats['merged_existing_threads'] += 1
        else:
            row, lab = old, labels.get(tid)
        admit(row, lab)
    for tid, row in merged.items():
        if row and tid not in seen:
            admit(row, labels.get(tid) if row['label_reused'] else None)
            stats['additional_threads'] += 1
    excluded.close()
    review.close()
    if args.prepare_only:
        (args.out/'summary.json').write_text(json.dumps(dict(counts=stats,stage='prepared'),indent=2))
        return
    print(json.dumps(dict(stage='dedup_start',candidates_and_guards=len(rows),counts=stats)), flush=True)
    groups = deduplicate(rows, args.workers)
    with (args.out/'threads.jsonl').open('w') as output, (args.out/'duplicates.jsonl').open('w') as duplicates:
        for group_id, members in sorted(groups.items()):
            choices = [rows[tid] for tid in members if rows[tid]['eligible']]
            if not choices:
                continue
            best = max(choices, key=lambda r: (r['label']['calidad']=='alta',r['n_replies'],-r['thread_id']))
            best.pop('eligible')
            best.update(group_id=group_id,split='val' if any(is_val(tid) for tid in members) else 'train')
            output.write(json.dumps(best,ensure_ascii=False)+'\n')
            stats['kept_'+best['split']] += 1
            stats['kept_'+best['label']['tipo']] += 1
            for tid in members:
                if tid != best['thread_id']:
                    duplicates.write(json.dumps(dict(thread_id=tid,representative=best['thread_id'],group_id=group_id,split=best['split']))+'\n')
                    stats['duplicate_members'] += 1
    report = dict(created_at=time.time(),counts=stats,inputs={k:str(getattr(args,k)) for k in ('archive','threads','public','labels')},
                  review_labels=str(args.review_labels) if args.review_labels else None,
                  snapshot_source=str(source_path),
                  dedup='Normalized exact; MinHash LSH candidates at 0.7, verified 5-word Jaccard >=0.8; connected groups; old validation membership wins',
                  labels='Existing labels reused for unchanged own openers; review overrides require exact text SHA256; unmatched revisions await review',
                  filter='media/alta historias, preguntas, debates, humor, hipoteticos')
    (args.out/'summary.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
