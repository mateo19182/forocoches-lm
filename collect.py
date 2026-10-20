"""Resumable thread-page collector. One process per data directory."""
import argparse
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import transport
import post_retention

BASE = 'https://forocoches.com/foro/'
VERSION = 7


def canonical(url):
    try:
        p = urlparse(urljoin(BASE, url))
    except ValueError:
        return None
    if p.scheme != 'https' or p.hostname != 'forocoches.com' or p.path != '/foro/showthread.php':
        return None
    q = parse_qs(p.query)
    try:
        thread, page = int(q['t'][0]), int(q.get('page', ['1'])[0])
    except (KeyError, ValueError, IndexError):
        return None
    if thread < 1 or page < 1:
        return None
    return f'{BASE}showthread.php?t={thread}&page={page}'


def clean_unicode(value):
    """Repair numeric-entity surrogate pairs; replace only unpaired surrogates."""
    if isinstance(value,str):
        return value.encode('utf-16','surrogatepass').decode('utf-16','replace')
    if isinstance(value,list):return [clean_unicode(v) for v in value]
    if isinstance(value,dict):return {k:clean_unicode(v) for k,v in value.items()}
    return value


def page_soup(html):
    # Current forum pages declare UTF-8; guessing bytes can select MacRoman.
    if isinstance(html, bytes):
        html = html.decode('utf-8', errors='replace')
    # Legacy logged-in pages self-close message divs; browsers treat them as open tags.
    html = re.sub(r'''(<div\b[^>]*\bid=["']post_message_\d+["'][^>]*?)\s*/>''', r'\1>', html)
    return BeautifulSoup(html, 'html.parser')


def parse(html, url):
    soup = page_soup(html)
    posts = []
    for body in soup.select('[id^="post_message_"]'):
        match = re.fullmatch(r'post_message_(\d+)', body['id'])
        if not match:
            continue
        wrapper = body.find_parent(class_='postbit_wrapper')
        if wrapper is None:
            wrapper = body.find_parent('table', id=f'post{match[1]}')
        author = next((a for a in wrapper.select('a[href*="member.php?u="]')
                       if a.get_text(strip=True)), None) if wrapper else None
        date = wrapper.select_one('.postdate, td.thead') if wrapper else None
        author_id_match = re.search(r'[?&]u=(\d+)', author.get('href', '')) if author else None
        number = wrapper.select_one('.post-counter, .postcount, a[onclick*=copyToClipboard], a[id^="postcount"]') if wrapper else None
        number_match = re.search(r'#?([\d.]+)', number.get_text()) if number else None
        quotes = [int(m) for a in body.select('a[href]') for m in re.findall(r'[?&]p=(\d+)', a['href'])]
        # Keep verbatim body HTML so quote removal and text extraction can be revised.
        posts.append(dict(id=int(match[1]), page_index=len(posts),
                          author_id=int(author_id_match[1]) if author_id_match else None,
                          position=int(number_match[1].replace('.', '')) if number_match else None,
                          author=author.get_text(' ', strip=True) if author else None,
                          posted_at_raw=date.get_text(' ', strip=True) if date else None,
                          html=str(body), text=body.get_text('\n', strip=True), quote_post_ids=quotes,
                          images=[dict(src=i.get('src'), alt=i.get('alt'), title=i.get('title')) for i in body.select('img')]))
    thread_id = parse_qs(urlparse(url).query).get('t')
    links = sorted({u for a in soup.select('a[href]')
                    if (u := canonical(a['href']))
                    and parse_qs(urlparse(u).query).get('t') == thread_id})
    forums = soup.select('.navbar a[href*="forumdisplay.php"], .breadcrumb a[href*="forumdisplay.php"]')
    page_count = max([int(parse_qs(urlparse(url).query).get('page', ['1'])[0])] +
                     [int(parse_qs(urlparse(link).query)['page'][0]) for link in links])
    return clean_unicode(dict(page_count_observed=page_count,
                subforum=forums[-1].get_text(' ', strip=True) if forums else None,
                title=soup.title.get_text(' ', strip=True) if soup.title else None,
                posts=posts, links=links, parser_version=VERSION))


def connect(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'crawl.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
        status INTEGER, fetched_at REAL, sha256 TEXT, error TEXT);
      CREATE INDEX IF NOT EXISTS queue ON pages(state,due);
      CREATE TABLE IF NOT EXISTS fetch_events(id INTEGER PRIMARY KEY, url TEXT NOT NULL,
        status INTEGER, body_bytes INTEGER NOT NULL, fetched_at REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS threads(id INTEGER PRIMARY KEY,title TEXT,subforum TEXT,
        page_count_observed INTEGER,observed_at REAL);
      CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL,
        page_url TEXT NOT NULL, payload TEXT NOT NULL, observed_at REAL NOT NULL);
    ''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(pages)')}
    if 'priority' not in columns:
        db.execute('ALTER TABLE pages ADD COLUMN priority REAL NOT NULL DEFAULT 0')
    if 'redirect_target' not in columns:
        db.execute('ALTER TABLE pages ADD COLUMN redirect_target TEXT')
    if 'scan_order' not in columns:
        db.execute('ALTER TABLE pages ADD COLUMN scan_order INTEGER')
    db.execute("CREATE INDEX IF NOT EXISTS pending_scan ON pages(scan_order) WHERE state='pending' AND scan_order IS NOT NULL")
    event_columns = {r[1] for r in db.execute('PRAGMA table_info(fetch_events)')}
    if 'request_url' not in event_columns:
        db.execute('ALTER TABLE fetch_events ADD COLUMN request_url TEXT')
    for column in ('request_started_at','request_finished_at'):
        if column not in event_columns:
            db.execute(f'ALTER TABLE fetch_events ADD COLUMN {column} REAL')
    db.execute('CREATE INDEX IF NOT EXISTS fetch_start ON fetch_events(request_started_at)')
    post_retention.setup(db)
    db.commit()
    return db


def enqueue(db, urls):
    db.executemany('INSERT OR IGNORE INTO pages(url) VALUES(?)', ((u,) for u in urls))


def empty_thread(soup, url):
    posts=soup.find(id='posts')
    thread=soup.find(id='qr_threadid')
    last=posts.find(id='lastpost',recursive=False) if posts else None
    return bool(posts is not None and thread is not None and last is not None
                and thread.get('value')==parse_qs(urlparse(url).query)['t'][0]
                and not posts.get_text(strip=True)
                and len(posts.find_all(recursive=False))==1 and not last.find()
                and soup.select_one('a[href*="login.php?do=logout"]'))


def save_response(db, root, url, attempts, response, max_thread_pages=None, cookies=None, min_own_chars=None):
    now = time.time()
    digest = hashlib.sha256(response.content).hexdigest()
    path = root / 'raw' / digest[:2] / (digest + '.html.gz')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_suffix('.tmp')
        temp.write_bytes(gzip.compress(response.content))
        temp.replace(path)
    state, error, due = 'done', None, 0
    parsed = None
    try:
        location = urlparse(urljoin(url, response.headers.get('Location', '')))
    except ValueError:
        location = urlparse('')
    location_query = parse_qs(location.query)
    restricted = (response.status_code == 302 and location.hostname == 'forocoches.com'
                  and location.path == '/foro/misc.php'
                  and location_query.get('template') in (['Info'], ['InfoPRV'])
                  and location_query.get('do') == ['page']
                  and location_query.get('tid') == parse_qs(urlparse(url).query)['t']
                  and set(location_query) == {'do', 'template', 'tid'})
    moved_target = canonical(location.geturl())
    if max_thread_pages==1 and moved_target:
        moved_target=canonical('showthread.php?t='+parse_qs(urlparse(moved_target).query)['t'][0])
    if response.headers.get('cf-mitigated')=='challenge':
        state,error='blocked','Challenge response: inspect before resuming'
    elif response.status_code==200 and isinstance(getattr(response,'_fc_parse_error',None),Exception):
        state,error='blocked','Parser worker error: '+type(response._fc_parse_error).__name__
    elif restricted and location_query.get('template') == ['InfoPRV']:
        state,error='restricted','Private-thread information redirect; not followed'
    elif restricted:
        state = 'blocked' if cookies else 'restricted'
        error = 'Authenticated session cannot access thread' if cookies else 'Registered-user-only information redirect; not followed'
    elif response.status_code in (301,302,303,307,308) and moved_target and moved_target!=url:
        state,error='redirected','Same-site thread move queued'
    elif response.status_code in (401, 403, 429) or 300 <= response.status_code < 400:
        state, error = 'blocked', 'Authentication, redirect or rate limit: inspect before resuming'
    elif response.status_code in (404, 410):
        state = 'missing'
    elif response.status_code >= 500:
        state, error, due = 'pending', 'Server error', now + min(3600, 30 * 2 ** min(attempts, 7))
    elif response.status_code != 200:
        state, error = 'blocked', 'Unexpected HTTP status'
    else:
        prepared=getattr(response,'_fc_parsed',None)
        parsed = prepared if isinstance(prepared,dict) else parse(response.content, url)
        if not parsed['posts']:
            soup=page_soup(response.content)
            notice = ' '.join(soup.get_text(' ',strip=True).split())
            if any(message in notice for message in ('Tema especificado inválido.', 'Ningún Tema especificado.')):
                state, error = 'missing', 'Forum invalid-topic notice returned with HTTP 200'
            elif 'No has iniciado sesión o no tienes permiso para acceder a esta página.' in notice:
                state = 'blocked' if cookies else 'restricted'
                error = 'Explicit login/permission notice returned with HTTP 200'
            elif (cookies and soup.select_one('a[href*="login.php?do=logout"]')
                  and 'No tienes permisos para acceder a esta página.' in notice
                  and 'Falta de validación de tu cuenta' in notice
                  and not any(marker in str(soup).lower() for marker in ('cf-chl-', 'captcha'))):
                state,error='restricted','Signed-in account lacks permission for this thread; skipped'
            elif cookies and empty_thread(soup,url):
                state,error='empty','Authenticated thread rendered with an empty posts container'
            else:
                state, error = 'blocked', 'No posts: possible login, challenge, removed thread or parser change'
    if attempts >= 7 and state == 'pending':
        state = 'failed'
    with db:
        if state=='redirected':enqueue(db,[moved_target])
        request_url = getattr(response, 'url', None)
        timing=[getattr(response,name,None) for name in ('_fc_started_at','_fc_finished_at')]
        timing=[v if isinstance(v,(int,float)) else None for v in timing]
        db.execute('INSERT INTO fetch_events(url,status,body_bytes,fetched_at,request_url,request_started_at,request_finished_at) VALUES(?,?,?,?,?,?,?)',
                   (url,response.status_code,len(response.content),now,request_url if isinstance(request_url,str) else url,*timing))
        db.execute('UPDATE pages SET redirect_target=? WHERE url=?',
                   (response.headers.get('Location'),url))
        db.execute('UPDATE pages SET state=?,attempts=attempts+1,due=?,status=?,fetched_at=?,sha256=?,error=? WHERE url=?',
                   (state, due, response.status_code, now, digest, error, url))
        if state == 'done' and parsed:
            thread = int(parse_qs(urlparse(url).query)['t'][0])
            db.execute('''INSERT INTO threads VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,subforum=coalesce(excluded.subforum,threads.subforum),
                page_count_observed=max(threads.page_count_observed,excluded.page_count_observed),
                observed_at=excluded.observed_at''',
                (thread,parsed['title'],parsed['subforum'],parsed['page_count_observed'],now))
            records=(post_retention.retain_page(db,root,url,parsed,now,min_own_chars,parse)
                     if min_own_chars is not None else [(post,url,now) for post in parsed['posts']])
            for post,source_url,observed_at in records:
                payload = json.dumps({**post, 'title': parsed['title'], 'parser_version': VERSION}, ensure_ascii=False)
                db.execute('INSERT INTO posts VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET page_url=excluded.page_url,payload=excluded.payload,observed_at=excluded.observed_at',
                           (post['id'], thread, source_url, payload, observed_at))
                if min_own_chars is not None:
                    db.execute('''INSERT INTO post_retention_meta VALUES(?,?,?) ON CONFLICT(post_id) DO UPDATE SET
                      reason=excluded.reason,own_chars=excluded.own_chars''',
                               (post['id'],post['retention_reason'],post['own_text_chars']))
            links = parsed['links']
            if max_thread_pages is not None:
                links = [u for u in links if int(parse_qs(urlparse(u).query)['page'][0]) <= max_thread_pages]
            enqueue(db, links)
    print(json.dumps(dict(url=url, state=state, status=response.status_code)), flush=True)
    return state


def run(db, root, limit, delay, cookies=None, max_thread_pages=None, proxy_file=None, client=None,min_own_chars=None):
    if db.execute("SELECT 1 FROM pages WHERE state='blocked' LIMIT 1").fetchone():
        raise SystemExit('Collection paused: inspect blocked pages before resetting their state.')
    session = client if client is not None else transport.session(cookies, proxy_file)
    try:
        for _ in range(limit):
            row = db.execute("SELECT url,attempts FROM pages WHERE state='pending' AND due<=? ORDER BY priority DESC,due,url LIMIT 1", (time.time(),)).fetchone()
            if row is None:
                break
            url, attempts = row
            try:
                response = session.get(url, timeout=(10, 45), allow_redirects=False)
                state = save_response(db,root,url,attempts,response,max_thread_pages,cookies,min_own_chars)
                if state == 'blocked':
                    break
            except requests.RequestException as exc:
                with db:
                    db.execute('INSERT INTO fetch_events(url,status,body_bytes,fetched_at) VALUES(?,NULL,0,?)',(url,time.time()))
                    db.execute("UPDATE pages SET attempts=attempts+1,state=?,due=?,error=? WHERE url=?",
                               ('failed' if attempts >= 7 else 'pending', time.time()+min(3600,30*2**min(attempts,7)), type(exc).__name__, url))
            time.sleep(delay + random.uniform(0, delay * .2))
    finally:
        if client is None:
            session.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', type=Path, default=Path('data/crawl'))
    sub = ap.add_subparsers(dest='command', required=True)
    seed = sub.add_parser('seed'); seed.add_argument('urls', nargs='+')
    crawl = sub.add_parser('run'); crawl.add_argument('--limit', type=int, default=10); crawl.add_argument('--delay', type=float, default=5)
    crawl.add_argument('--cookies', type=Path, help='Private Netscape-format browser cookie file')
    crawl.add_argument('--proxy-file', type=Path, help='Private JSON with http and https proxy URLs')
    crawl.add_argument('--max-thread-pages', type=int, help='Pilot cap; longer threads remain partial')
    sub.add_parser('status')
    args = ap.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    with (args.data / 'collector.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with connect(args.data) as db:
            if args.command == 'seed':
                urls = [canonical(u) for u in args.urls]
                if None in urls:
                    ap.error('Use HTTPS forocoches.com/foro/showthread.php?t=ID URLs')
                enqueue(db, urls)
            elif args.command == 'run':
                if args.delay < 2 or args.limit < 1:
                    ap.error('delay must be >=2 seconds; limit must be positive')
                if args.max_thread_pages is not None and args.max_thread_pages < 1:
                    ap.error('max-thread-pages must be positive')
                with transport.host_lock():
                    run(db, args.data, args.limit, args.delay, args.cookies, args.max_thread_pages, args.proxy_file)
            else:
                print(dict(db.execute('SELECT state,count(*) FROM pages GROUP BY state')))
                print('posts', db.execute('SELECT count(*) FROM posts').fetchone()[0])


if __name__ == '__main__':
    main()
