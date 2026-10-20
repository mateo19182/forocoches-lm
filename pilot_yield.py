"""Read-only pilot yield against historical and previous-crawl post IDs."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import parse_qs, urlparse

from fcdata.build import core_text
from fcdata.parse import parse_post

ARCHIVE = Path('data/fc_dataset_7-8.sqlite')
PREVIOUS = Path('data/public-2026-10-16/crawl.sqlite')
BATCH_SIZE = 900
USEFUL_CHARS = 40
LONG_CHARS = 2000


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def existing_ids(db, table, column, ids):
    """Inspect the plan; never repeat an unindexed scan for each batch."""
    sql = f'SELECT {column} FROM {table} WHERE {column} IN (?)'
    plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + sql, (0,))]
    indexed = any('SEARCH ' in detail for detail in plan)
    found = set()
    if ids and indexed:
        ordered = sorted(ids)
        for start in range(0, len(ordered), BATCH_SIZE):
            batch = ordered[start:start + BATCH_SIZE]
            placeholders = ','.join('?' for _ in batch)
            found.update(row[0] for row in db.execute(
                f'SELECT {column} FROM {table} WHERE {column} IN ({placeholders})', batch))
    elif ids:
        found.update(value for (value,) in db.execute(f'SELECT {column} FROM {table}')
                     if value in ids)
    return found, dict(table=table, column=column, query_plan=plan,
                       strategy='batched_index_lookup' if indexed else 'single_id_only_scan')


def thread_id(url):
    try:
        return int(parse_qs(urlparse(url).query)['t'][0])
    except (KeyError, ValueError, TypeError):
        return None


def pagination(pages):
    states = Counter(state for _, state in pages)
    later = Counter(state for url, state in pages
                    if int(parse_qs(urlparse(url).query).get('page', ['1'])[0]) > 1)
    total = len(pages)
    return dict(queued_pages=total, states=dict(sorted(states.items())),
                done_pages=states['done'], restricted_pages=states['restricted'],
                done_fraction=states['done'] / total if total else None,
                later_page_states=dict(sorted(later.items())))


def quality(rows):
    result = dict(collected_posts=0, new_id_posts=0, parse_errors=0,
                  new_id_parse_errors=0, useful_new_posts=0, long_new_posts=0,
                  new_id_own_text_chars=0)
    texts = set()
    for is_new, own in rows:
        result['collected_posts'] += 1
        result['new_id_posts'] += int(is_new)
        if own is None:
            result['parse_errors'] += 1
            result['new_id_parse_errors'] += int(is_new)
        elif is_new:
            result['new_id_own_text_chars'] += len(own)
            result['useful_new_posts'] += int(len(own) >= USEFUL_CHARS)
            result['long_new_posts'] += int(len(own) >= LONG_CHARS)
            if own:
                texts.add(own)
    result.update(exact_distinct_new_id_own_texts=len(texts),
                  exact_distinct_new_id_own_text_chars=sum(map(len, texts)),
                  exact_distinct_long_new_id_own_texts=sum(len(t) >= LONG_CHARS for t in texts),
                  exact_distinct_long_new_id_own_text_chars=sum(len(t) for t in texts if len(t) >= LONG_CHARS))
    return result


def analyze(snapshot, archive, previous):
    ids = {row[0] for row in snapshot.execute('SELECT id FROM posts')}
    plans = {}
    with closing(readonly(archive)) as db:
        db.execute('BEGIN')
        openers, plans['archive_openers'] = existing_ids(db, 'threads', 'post_id', ids)
        comments, plans['archive_comments'] = existing_ids(db, 'comments', 'post_id', ids)
    with closing(readonly(previous)) as db:
        db.execute('BEGIN')
        old, plans['previous_posts'] = existing_ids(db, 'posts', 'id', ids)
    historical = openers | comments
    known = historical | old
    posts = defaultdict(list)
    for pid, tid, payload in snapshot.execute('SELECT id,thread_id,payload FROM posts'):
        try:
            html = json.loads(payload)['html']
            if not isinstance(html, str):
                raise TypeError('HTML must be text')
            own = core_text(parse_post(html).text)
        except (ValueError, KeyError, TypeError):
            own = None
        posts[tid].append((pid not in known, own))
    pages = defaultdict(list)
    for url, state in snapshot.execute('SELECT url,state FROM pages'):
        pages[thread_id(url)].append((url, state))
    sources = defaultdict(set)
    for url, source in snapshot.execute('SELECT url,source FROM discoveries'):
        tid = thread_id(url)
        if tid is not None:
            sources[source].add(tid)
    attributed = set().union(*sources.values()) if sources else set()
    unattributed = (set(posts) | set(pages)) - attributed
    per_source = []
    for source, tids in sorted(sources.items()):
        per_source.append(dict(source=source, threads=len(tids),
                               **quality(row for tid in tids for row in posts[tid]),
                               pagination=pagination([p for tid in tids for p in pages[tid]])))
    return dict(
        schema_version=1, created_at=time.time(),
        inputs=dict(archive=str(Path(archive).resolve()), previous=str(Path(previous).resolve())),
        definitions=dict(
            new_id='Post ID absent from all archive opener/comment IDs and all previous-crawl post IDs.',
            own_text='core_text(parse_post(html).text); Unicode characters, not bytes or tokens.',
            useful_min_own_chars=USEFUL_CHARS, long_min_own_chars=LONG_CHARS,
            exact_distinct='Case-sensitive equality of cleaned own text among new-ID pilot posts; empty text excluded.',
            novelty_caveat='New IDs do not establish novel text. Historical and previous bodies are not compared; copied text may remain.',
            source_attribution='Each discovery source gets all collected posts and queued pages of its directly discovered threads. Sources overlap; do not sum them.',
            pagination='Done fraction of recorded queue pages only, not total thread coverage; undiscovered pages are unknown.'),
        id_lookup=plans,
        overlap=dict(archive_posts=len(ids & historical), previous_posts=len(ids & old),
                     archive_and_previous_posts=len(ids & historical & old)),
        totals=quality(row for rows in posts.values() for row in rows),
        pagination=pagination([p for rows in pages.values() for p in rows]),
        per_source=per_source,
        unattributed=dict(threads=len(unattributed - {None}),
                          **quality(row for tid in unattributed for row in posts[tid]),
                          pagination=pagination([p for tid in unattributed for p in pages[tid]])))


def atomic_json(path, result):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.pilot-yield-', suffix='.tmp', delete=False) as out:
            temp = Path(out.name)
            json.dump(result, out, ensure_ascii=False, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def report(root, archive=ARCHIVE, previous=PREVIOUS):
    root = Path(root)
    with tempfile.TemporaryDirectory(prefix='pilot-yield-') as directory:
        with closing(sqlite3.connect(Path(directory) / 'snapshot.sqlite')) as snapshot:
            with closing(readonly(root / 'crawl.sqlite')) as live:
                live.backup(snapshot)
            snapshot.execute('PRAGMA query_only=ON')
            result = analyze(snapshot, archive, previous)
    result['inputs']['crawl'] = str((root / 'crawl.sqlite').resolve())
    atomic_json(root / 'pilot-yield.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='Crawl root containing crawl.sqlite')
    parser.add_argument('--archive', type=Path, default=ARCHIVE)
    parser.add_argument('--previous', type=Path, default=PREVIOUS)
    args = parser.parse_args()
    print(json.dumps(report(args.data, args.archive, args.previous), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
