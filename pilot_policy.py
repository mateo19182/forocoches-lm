"""Queue policy for the bounded outside-archive discovery pilot."""
import json
import re
from urllib.parse import parse_qs, urlparse

import collect
import crawl_metrics
from fcdata.build import core_text
from fcdata.parse import parse_post


def identity(url):
    q = parse_qs(urlparse(url).query)
    return int(q['t'][0]), int(q.get('page', ['1'])[0])


def config(db):
    return {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM public_config')}


def allow_seed(db, url):
    settings = config(db)
    if not settings.get('novel_pilot'):
        return True
    tid, _ = identity(url)
    if settings.get('exclude_archive_ids',True) and 7000000 <= tid < 8000000:
        return False
    first = collect.canonical(f'showthread.php?t={tid}')
    if db.execute('SELECT 1 FROM pilot_candidates WHERE thread_id=?', (tid,)).fetchone():
        return True
    count = crawl_metrics.total(db,'candidates')
    return count < settings.get('candidate_limit', 1000)


def admit(db, url, title, curated):
    if not config(db).get('novel_pilot'):
        return
    tid, _ = identity(url)
    index_hint = (re.search(r'recopil|[íi]ndice|biblioteca', title or '', re.I)
                  and re.search(r'histor|relat|hilos|forococh', title or '', re.I))
    role = 'index' if url in curated or index_hint else 'candidate'
    previous = db.execute('SELECT role FROM pilot_candidates WHERE thread_id=?',(tid,)).fetchone()
    db.execute("""INSERT INTO pilot_candidates VALUES(?,?,?) ON CONFLICT(thread_id) DO UPDATE SET
        role=CASE WHEN excluded.role='index' THEN 'index' ELSE pilot_candidates.role END,
        title=coalesce(excluded.title,pilot_candidates.title)""", (tid, role, title))
    if role == 'index' and previous and previous[0] != 'index':
        pattern = f'{collect.BASE}showthread.php?t={tid}&page=%'
        db.execute('DELETE FROM processed_pages WHERE url LIKE ?', (pattern,))
        db.execute("UPDATE pages SET state='pending' WHERE state='deferred_cap' AND url LIKE ?", (pattern,))


def observe(db,tid,url):
    own_long=any(len(core_text(parse_post(json.loads(row[0])['html']).text))>=2000
                 for row in db.execute("SELECT payload FROM posts WHERE page_url=? AND length(json_extract(payload,'$.text'))>=2000",(url,)))
    db.execute('INSERT INTO thread_signals VALUES(?,?) ON CONFLICT(thread_id) DO UPDATE SET long_own_text=max(thread_signals.long_own_text,excluded.long_own_text)',(tid,int(own_long)))
    long_text=bool(db.execute('SELECT long_own_text FROM thread_signals WHERE thread_id=?',(tid,)).fetchone()[0])
    if own_long:
        db.execute("UPDATE pages SET state='pending' WHERE state='deferred_cap' AND url LIKE ?",(f'{collect.BASE}showthread.php?t={tid}&page=%',))
    return long_text


def apply(db, curated):
    settings=config(db)
    if not settings.get('novel_pilot'):
        return
    rows = db.execute("SELECT url FROM pages WHERE state='pending'").fetchall()
    decisions = {}
    for (url,) in rows:
        tid, page = identity(url)
        if settings.get('exclude_archive_ids',True) and 7000000 <= tid < 8000000:
            db.execute("UPDATE pages SET state='excluded_archive' WHERE url=?", (url,))
            continue
        role = db.execute('SELECT role FROM pilot_candidates WHERE thread_id=?', (tid,)).fetchone()
        if not role:
            db.execute("UPDATE pages SET state='excluded_limit' WHERE url=?", (url,))
            continue
        if page == 1:
            continue
        if settings.get('first_pages_only'):
            db.execute("UPDATE pages SET state='deferred' WHERE url=?",(url,))
            continue
        if settings.get('all_thread_pages'):
            if role[0]=='index':
                db.execute('UPDATE pages SET priority=180 WHERE url=?',(url,))
            continue
        first = collect.canonical(f'showthread.php?t={tid}')
        if tid not in decisions:
            signal=db.execute('SELECT long_own_text FROM thread_signals WHERE thread_id=?',(tid,)).fetchone()
            if signal is None:
                op = db.execute("SELECT payload FROM posts WHERE thread_id=? AND json_extract(payload,'$.position')=1 LIMIT 1", (tid,)).fetchone()
                own = core_text(parse_post(json.loads(op[0])['html']).text) if op else ''
                long_text=len(own)>=2000
            else:
                long_text=bool(signal[0])
            # Long own-text is a collection heuristic, not a story label.
            decisions[tid] = role[0] == 'index' or long_text
        if not decisions[tid] and page > 20:
            db.execute("UPDATE pages SET state='deferred_cap' WHERE url=?", (url,))
        elif role[0] == 'index':
            db.execute('UPDATE pages SET priority=180 WHERE url=?', (url,))
