"""Collect a bounded, mixed-era extraction pilot, then export threads for review."""
import argparse
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import requests
import collect

HISTORICAL = [
    (4292241, '2015', 'https://forocoches.com/foro/showthread.php?t=4292241'),
    (8415216, '2021', 'https://forocoches.com/foro/showthread.php?t=8415216'),
    (8656312, '2021', 'https://forocoches.com/foro/showthread.php?t=8656312'),
    (10477903, '2025', 'https://forocoches.com/foro/showthread.php?t=10477903'),
    (10038332, '2024', 'https://forocoches.com/foro/showthread.php?t=10038332'),
]


def discover(root):
    """One General listing; save input and selection so the sample is reproducible."""
    url = 'https://forocoches.com/foro/forumdisplay.php?f=2'
    response = requests.get(url, timeout=(10, 45), allow_redirects=False,
                            headers={'User-Agent': 'forocoches-lm-archive/0.1'})
    root.mkdir(parents=True, exist_ok=True)
    (root / 'listing.html.gz').write_bytes(gzip.compress(response.content))
    (root / 'listing.json').write_text(json.dumps(dict(url=url, status=response.status_code,
        fetched_at=time.time(), sha256=hashlib.sha256(response.content).hexdigest()), indent=2))
    if response.status_code != 200:
        raise SystemExit(f'Listing paused: HTTP {response.status_code}')
    soup = BeautifulSoup(response.content.decode('utf-8', errors='replace'), 'html.parser')
    candidates = []
    seen = set()
    for a in soup.select('a[id^=thread_title_]'):
        canonical = collect.canonical(a.get('href', ''))
        if canonical and canonical not in seen:
            seen.add(canonical)
            candidates.append(dict(url=canonical, title=a.get_text(' ', strip=True)))
    if not candidates:
        raise SystemExit('Listing paused: no recognizable thread titles')
    # These are candidates, not verified stories. Keep one control from the listing.
    narrative = re.compile(r'\b(me|mi|ayer|hoy|historia|pasado|pasó|anécdota)\b', re.I)
    selected = [c for c in candidates if narrative.search(c['title'])][:2]
    control = next((c for c in candidates if c not in selected), None)
    if control:
        selected.append(control)
    seeds = [dict(**c, era='current-listing', source=url, selection='title heuristic or control') for c in selected]
    seeds += [dict(url=collect.canonical(f'showthread.php?t={tid}'), era=era,
                   source=source, selection='manual search result; not representative')
              for tid, era, source in HISTORICAL]
    (root / 'seeds.json').write_text(json.dumps(dict(candidates=candidates, selected=seeds), ensure_ascii=False, indent=2))
    time.sleep(5)
    return seeds


def export(root):
    db = sqlite3.connect(root / 'crawl.sqlite')
    seeds = json.loads((root / 'seeds.json').read_text())['selected']
    documents = []
    for seed in seeds:
        tid = int(parse_qs(urlparse(seed['url']).query)['t'][0])
        rows = db.execute('SELECT payload,page_url,observed_at FROM posts WHERE thread_id=?', (tid,)).fetchall()
        posts = [dict(**json.loads(p), page_url=u, observed_at=observed) for p,u,observed in rows]
        posts.sort(key=lambda p: (int(parse_qs(urlparse(p['page_url']).query)['page'][0]), p['page_index']))
        opener = posts[0] if posts else None
        for p in posts:
            p['is_op'] = p.get('author_id') == opener.get('author_id') if opener and opener.get('author_id') is not None else None
        pages = [dict(url=u,state=s,status=st) for u,s,st in db.execute('SELECT url,state,status FROM pages') if int(parse_qs(urlparse(u).query)['t'][0]) == tid]
        advertised = set()
        for page in pages:
            if page['state'] != 'done':
                continue
            payload = db.execute('SELECT sha256 FROM pages WHERE url=?', (page['url'],)).fetchone()[0]
            raw = gzip.decompress((root/'raw'/payload[:2]/(payload+'.html.gz')).read_bytes())
            advertised.update(int(parse_qs(urlparse(u).query)['page'][0]) for u in collect.parse(raw,seed['url'])['links'])
        done = {int(parse_qs(urlparse(p['url']).query)['page'][0]) for p in pages if p['state']=='done'}
        last = max(advertised | done, default=0)
        complete = bool(done) and set(range(1,last+1)).issubset(done)
        documents.append(dict(thread_id=tid, selection=seed, title=opener.get('title') if opener else None,
            opening_date=opener.get('posted_at_raw') if opener else None,
            complete_observed_pagination=complete, last_page_seen=last, pages=pages, posts=posts))
    with (root / 'threads.jsonl').open('w') as f:
        for d in documents:
            f.write(json.dumps(d, ensure_ascii=False)+'\n')
    with (root / 'threads.txt').open('w') as f:
        f.write('Extraction sample. Original language and quoted text retained.\n')
        for d in documents:
            f.write('\n' + '=' * 72 + '\n')
            f.write((d['title'] or 'Inaccessible candidate') + '\n')
            f.write(d['selection']['url'] + '\n')
            f.write(f"Opening date: {d['opening_date']}; observed pagination complete: {d['complete_observed_pagination']}\n")
            for p in d['posts']:
                f.write(f"\n#{p.get('position')} | {p['author']} | {p['posted_at_raw']} | OP: {p['is_op']}\n")
                f.write(p['text'] + '\n')
    report = dict(threads=len(documents), threads_with_posts=sum(bool(d['posts']) for d in documents),
        posts=sum(len(d['posts']) for d in documents),
        complete_threads=sum(d['complete_observed_pagination'] for d in documents),
        pages=dict(db.execute('SELECT state,count(*) FROM pages GROUP BY state')),
        compressed_bytes=sum(p.stat().st_size for p in root.rglob('*.gz')),
        uncompressed_saved_body_bytes=sum(len(gzip.decompress(p.read_bytes())) for p in root.rglob('*.gz')),
        quality=dict(missing_author_names=sum(not p['author'] for d in documents for p in d['posts']),
                     missing_author_ids=sum(p.get('author_id') is None for d in documents for p in d['posts']),
                     missing_positions=sum(p.get('position') is None for d in documents for p in d['posts']),
                     replacement_characters=sum(p['text'].count('\ufffd') for d in documents for p in d['posts']),
                     posts_with_quote_references=sum(bool(p['quote_post_ids']) for d in documents for p in d['posts'])),
        threads_detail=[{k:v for k,v in d.items() if k not in ['posts','pages','selection']} | {'posts':len(d['posts'])} for d in documents])
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', type=Path, default=Path('data/sample-2026-10-16'))
    ap.add_argument('--export-only', action='store_true')
    args = ap.parse_args()
    root = args.data
    root.mkdir(parents=True, exist_ok=True)
    with (root/'collector.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not args.export_only:
            seeds = json.loads((root/'seeds.json').read_text())['selected'] if (root/'seeds.json').exists() else discover(root)
            with collect.connect(root) as db:
                collect.enqueue(db,[s['url'] for s in seeds])
            with collect.connect(root) as db:
                remaining = max(0, 42 - db.execute('SELECT coalesce(sum(attempts),0) FROM pages').fetchone()[0])
                if remaining:
                    collect.run(db,root,limit=remaining,delay=5,max_thread_pages=6)
        export(root)


if __name__ == '__main__':
    main()
