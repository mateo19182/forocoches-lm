"""Resumable guest or authenticated crawl, prioritising linked stories."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import time
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
import requests
import collect
import transport
import pilot_policy
import id_scan
import crawl_metrics
from monitor_crawl import network_problem
from parallel_fetch import Fetcher

LISTING = 'https://forocoches.com/foro/forumdisplay.php?f=2&page={}'
LONG_CHARS = 2000
MIN_REQUEST_INTERVAL = 1 / 50
STOP = False
LAST_EXPORT_ATTEMPT = None
RUN_STARTED_AT = time.time()
SOURCES = json.loads(Path(__file__).with_name('discovery_sources.json').read_text())
CURATED = {collect.canonical(f"showthread.php?t={item['thread_id']}") for item in SOURCES if item['kind']!='story_candidate'}
STORY_HINT = re.compile(r'relato|historias?|tocho|an[eé]cdota|me pas[oó]|me ha pasado|cr[oó]nica|confesi[oó]n|recopilaci[oó]n|[ií]ndice|biblioteca forococh|os cuento|mi experiencia|storytime|\+hd\b|\[hd\]',re.I)


def relevant_link(source,title):
    canonical_source = thread_link(source)
    if canonical_source:
        canonical_source = collect.canonical(f"showthread.php?t={pilot_policy.identity(canonical_source)[0]}")
    return canonical_source in CURATED or (bool(thread_link(source)) and bool(STORY_HINT.search(title or '')))



def thread_link(href):
    try:
        p = urlparse(urljoin(collect.BASE, href))
        if p.scheme not in ('https','http') or p.hostname not in ('forocoches.com','www.forocoches.com','m.forocoches.com'):
            return None
        return collect.canonical('https://forocoches.com'+p.path+'?'+p.query)
    except ValueError:
        return None


def listing_threads(html):
    soup = BeautifulSoup(html.decode('utf-8',errors='replace'),'html.parser')
    return [(u,a.get_text(' ',strip=True)) for a in soup.select('a[id^=thread_title_][href]')
            if (u := thread_link(a['href']))]


def setup(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS pilot_candidates(thread_id INTEGER PRIMARY KEY,role TEXT,title TEXT);
      CREATE TABLE IF NOT EXISTS listing_jobs(page INTEGER PRIMARY KEY,state TEXT DEFAULT 'pending',
        attempts INTEGER DEFAULT 0,status INTEGER,sha256 TEXT,fetched_at REAL,error TEXT);
      CREATE TABLE IF NOT EXISTS discoveries(url TEXT NOT NULL,source TEXT NOT NULL,title TEXT,
        discovered_at REAL NOT NULL,PRIMARY KEY(url,source));
      CREATE TABLE IF NOT EXISTS public_config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS candidate_links(url TEXT NOT NULL,source TEXT NOT NULL,title TEXT,accepted INTEGER NOT NULL,PRIMARY KEY(url,source));
      CREATE TABLE IF NOT EXISTS processed_pages(url TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS response_sizes(url TEXT PRIMARY KEY,bytes INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS thread_signals(thread_id INTEGER PRIMARY KEY,long_own_text INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS posts_page_url ON posts(page_url);
      CREATE INDEX IF NOT EXISTS posts_thread_id ON posts(thread_id);
      CREATE INDEX IF NOT EXISTS posts_text_chars ON posts(length(json_extract(payload,'$.text')));
      CREATE VIEW IF NOT EXISTS long_posts AS SELECT id,thread_id,page_url,observed_at,
        length(json_extract(payload,'$.text')) AS text_chars,
        json_extract(payload,'$.position') AS position,
        json_extract(payload,'$.title') AS title
        FROM posts WHERE length(json_extract(payload,'$.text'))>=2000;
    ''')
    id_scan.setup(db)
    crawl_metrics.setup(db)


def seed(db, url, source, title=None):
    # Start each linked thread at its opener, irrespective of linked page number.
    title=collect.clean_unicode(title)
    if not pilot_policy.allow_seed(db,url):
        return False
    p=urlparse(url);tid=parse_qs(p.query)['t'][0]
    url=collect.canonical(f'showthread.php?t={tid}')
    pilot_policy.admit(db,url,title,CURATED)
    hint=bool(re.search(r'relato|historia|tocho|anécdota|anecdota|me pasó|me ha pasado',title or '',re.I))
    collect.enqueue(db,[url])
    db.execute("UPDATE pages SET priority=max(priority,?),state=CASE WHEN state='held_source' THEN 'pending' ELSE state END WHERE url=?",(120 if hint else 100,url))
    db.execute('INSERT OR IGNORE INTO discoveries VALUES(?,?,?,?)',(url,source,title,time.time()))
    return True


def save_raw(root, content):
    digest=hashlib.sha256(content).hexdigest()
    path=root/'raw'/digest[:2]/(digest+'.html.gz')
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        temp=path.with_suffix('.tmp');temp.write_bytes(gzip.compress(content));temp.replace(path)
    return digest


def postprocess(db,root):
    for url,target,priority in db.execute("SELECT url,redirect_target,priority FROM pages WHERE state='redirected' AND url NOT IN (SELECT url FROM processed_pages)").fetchall():
        moved=thread_link(target)
        if moved:
            with db:
                seed(db,moved,url,'Same-site moved thread')
                db.execute('UPDATE pages SET priority=max(priority,?) WHERE url=?',(priority,moved))
                db.execute('INSERT OR IGNORE INTO processed_pages VALUES(?)',(url,))
    rows=db.execute("SELECT url,sha256 FROM pages WHERE state='done' AND url NOT IN (SELECT url FROM processed_pages)").fetchall()
    for url,digest in rows:
        html=gzip.decompress((root/'raw'/digest[:2]/(digest+'.html.gz')).read_bytes())
        soup=collect.page_soup(html)
        tid=int(parse_qs(urlparse(url).query)['t'][0])
        with db:
            db.execute('INSERT OR REPLACE INTO response_sizes VALUES(?,?)',(url,len(html)))
            pilot_policy.admit(db,collect.canonical(f'showthread.php?t={tid}'),
                               collect.clean_unicode(soup.title.get_text(' ',strip=True)) if soup.title else None,CURATED)
            # Follow only links in actual message bodies, not navigation or profiles.
            for a in soup.select('[id^=post_message_] a[href]'):
                target=thread_link(a['href'])
                if target:
                    first_page=collect.canonical(f"showthread.php?t={parse_qs(urlparse(target).query)['t'][0]}")
                    if pilot_policy.identity(first_page)[0]==tid:
                        continue
                    title=collect.clean_unicode(a.get_text(' ',strip=True))
                    focused=db.execute("SELECT value FROM public_config WHERE key='stories_only'").fetchone()
                    index_role=db.execute("SELECT 1 FROM pilot_candidates WHERE thread_id=? AND role='index'",(tid,)).fetchone()
                    accepted=(bool(index_role) or not (focused and json.loads(focused[0])) or relevant_link(url,title)) and pilot_policy.allow_seed(db,first_page)
                    db.execute('INSERT OR REPLACE INTO candidate_links VALUES(?,?,?,?)',(first_page,url,title,int(accepted)))
                    if not accepted:
                        continue
                    maximum=pilot_policy.config(db).get('max_discovered_pages',50000)
                    if crawl_metrics.page_count(db) >= maximum:
                        break
                    seed(db,target,url,title)
            first=db.execute("SELECT payload FROM posts WHERE thread_id=? ORDER BY CAST(json_extract(payload,'$.position') AS INTEGER),id LIMIT 1",(tid,)).fetchone()
            long_op=bool(first and len(json.loads(first[0])['text'])>=LONG_CHARS)
            long_text=pilot_policy.observe(db,tid,url)
            for (pending,) in db.execute("SELECT url FROM pages WHERE state='pending' AND url LIKE ?",
                                       (f'{collect.BASE}showthread.php?t={tid}&page=%',)).fetchall():
                q=parse_qs(urlparse(pending).query)
                if int(q['t'][0])==tid and int(q['page'][0])>1:
                    page=int(q['page'][0])
                    # First replies to long openers are prioritized; very long threads yield to new openers.
                    priority=(160 if long_op or long_text else 80)/page**.2
                    db.execute('UPDATE pages SET priority=? WHERE url=?',(priority,pending))
            db.execute('INSERT OR IGNORE INTO processed_pages VALUES(?)',(url,))
    # Include non-content responses in the body budget too.
    for url,digest in db.execute("SELECT url,sha256 FROM pages WHERE sha256 IS NOT NULL AND url NOT IN (SELECT url FROM response_sizes)").fetchall():
        raw=root/'raw'/digest[:2]/(digest+'.html.gz')
        with db:db.execute('INSERT OR REPLACE INTO response_sizes VALUES(?,?)',(url,len(gzip.decompress(raw.read_bytes()))))


def export_long_posts(db,root):
    temp=root/'long-posts.tmp'
    if pilot_policy.config(db).get('min_own_chars'):
        query="SELECT p.id,p.thread_id,p.page_url,p.payload,p.observed_at FROM posts p JOIN post_retention_meta m ON m.post_id=p.id WHERE m.reason='long'"
        rows=db.execute(query)
    else:
        rows=db.execute("SELECT id,thread_id,page_url,payload,observed_at FROM posts WHERE length(json_extract(payload,'$.text'))>=?",(LONG_CHARS,))
    with temp.open('w') as output:
        for post_id,thread_id,url,payload,observed in rows:
            post=json.loads(payload)
            output.write(json.dumps(dict(thread_id=thread_id,page_url=url,observed_at=observed,**post),ensure_ascii=False)+'\n')
    temp.replace(root/'long-posts.jsonl')


def source_yield(db,root):
    rows=[]
    for source,candidates,readable,long_pages in db.execute("""SELECT source,count(distinct d.url),
        count(distinct CASE WHEN p.state='done' THEN d.url END),
        count(distinct CASE WHEN EXISTS(SELECT 1 FROM posts x WHERE x.page_url=d.url
          AND length(json_extract(x.payload,'$.text'))>=2000) THEN d.url END)
        FROM discoveries d LEFT JOIN pages p ON p.url=d.url GROUP BY source ORDER BY 4 DESC,3 DESC"""):
        rows.append(dict(source=source,candidates=candidates,readable_first_pages=readable,
                         first_pages_with_long_text=long_pages))
    temp=root/'source-yield.tmp';temp.write_text(json.dumps(rows,ensure_ascii=False,indent=2));temp.replace(root/'source-yield.json')


def progress(db,root,state,error=None):
    global LAST_EXPORT_ATTEMPT
    settings=pilot_policy.config(db)
    retained=crawl_metrics.counts(db,'crawl_retention_states') if settings.get('min_own_chars') else None
    counts=crawl_metrics.counts(db,'crawl_page_states')
    attempts=crawl_metrics.total(db,'attempts')
    report=dict(state=state,updated_at=time.time(),pid=os.getpid(),route=os.environ.get('FC_NETWORK_ROUTE','direct'),pages=counts,
        attempts=attempts,listing_pages=dict(db.execute('SELECT state,count(*) FROM listing_jobs GROUP BY state')),
        threads=db.execute('SELECT count(*) FROM crawl_thread_posts').fetchone()[0],
        posts=crawl_metrics.total(db,'posts'),
        long_posts=retained.get('long',0) if retained is not None else db.execute('SELECT count(*) FROM long_posts').fetchone()[0],long_post_min_chars=LONG_CHARS,
        saved_response_bytes=crawl_metrics.total(db,'saved_response_bytes'),
        limits=settings)
    report['worker_container']=os.environ.get('FC_WORKER_CONTAINER')
    last_response=db.execute('SELECT fetched_at FROM fetch_events WHERE status IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    report['last_http_response_at']=last_response[0] if last_response else None
    last_fetch=db.execute('SELECT status FROM fetch_events ORDER BY id DESC LIMIT 1').fetchone()
    report['last_fetch_status']=last_fetch[0] if last_fetch else None
    retry=db.execute("SELECT due,error,status FROM pages WHERE state='pending' ORDER BY due LIMIT 1").fetchone()
    report['next_request_due_at']=retry[0] if retry else None
    report['pending_retry_error']=retry[1] if retry else None
    report['pending_retry_http_status']=retry[2] if retry else None
    report['run_started_at']=RUN_STARTED_AT
    report['network_ready']=Path('/sys/class/net/tun0').exists() if report['route']=='airvpn' else None
    report['network_namespace']=os.readlink('/proc/self/ns/net')
    report['free_disk_bytes']=shutil.disk_usage(root).free
    if (root/'raw').exists():
        report['raw_free_disk_bytes']=shutil.disk_usage(root/'raw').free
        report['free_disk_bytes']=min(report['free_disk_bytes'],report['raw_free_disk_bytes'])
    report['threads_seen']=crawl_metrics.total(db,'threads_seen')
    report['id_scan']=id_scan.summary(db)
    if report['limits'].get('min_own_chars'):
        report['retention']=dict(min_own_chars=report['limits']['min_own_chars'],
            kept_long=retained.get('long',0),
            kept_op_context_or_followup=retained.get('op_context',0)+retained.get('op_followup',0),
            page_post_observations=crawl_metrics.total(db,'page_post_observations'))
        report['long_posts']=report['retention']['kept_long']
    if error:report['error_type']=error
    report['authenticated']=report['limits'].get('authenticated',False)
    report['parser_version']=collect.VERSION
    tmp=root/'status.tmp';tmp.write_text(json.dumps(report,indent=2));tmp.replace(root/'status.json')
    export_due=LAST_EXPORT_ATTEMPT is None or report['attempts']-LAST_EXPORT_ATTEMPT>=100 or state!='running'
    if export_due and (not report['limits'].get('complete_id_scan') or state!='running'):
        export_long_posts(db,root)
        if not settings.get('complete_id_scan'):
            source_yield(db,root)
        LAST_EXPORT_ATTEMPT=report['attempts']
    return report


def configure_access(db,cookies,posts_per_page):
    settings=pilot_policy.config(db)
    for key,value in (('authenticated',bool(cookies)),('posts_per_page',posts_per_page),('network_route',os.environ.get('FC_NETWORK_ROUTE','direct'))):
        if key in settings and settings[key]!=value:
            raise SystemExit('Access mode, page size and network route must match the saved crawl configuration')
        db.execute('INSERT OR IGNORE INTO public_config VALUES(?,?)',(key,json.dumps(value)))


def configure_pagination(db,first_pages=False,all_pages=False):
    if first_pages and all_pages:
        raise ValueError('First-page-only and all-page modes are mutually exclusive')
    if first_pages or all_pages:
        for key,value in (('first_pages_only',first_pages),('all_thread_pages',all_pages)):
            db.execute('INSERT OR REPLACE INTO public_config VALUES(?,?)',(key,json.dumps(value)))
    settings=pilot_policy.config(db)
    if settings.get('first_pages_only'):
        db.execute("UPDATE pages SET state='deferred' WHERE state='pending' AND url NOT LIKE '%&page=1'")
    elif settings.get('all_thread_pages'):
        db.execute("UPDATE pages SET state='pending' WHERE state IN ('deferred','deferred_cap')")


def stop_reason(report,settings,now=None):
    if report['pages'].get('blocked') or report['listing_pages'].get('blocked'):
        return 'paused'
    if settings.get('complete_id_scan') and report.get('id_scan',{}).get('complete'):
        return 'scan_complete'
    if not settings.get('complete_id_scan') and settings.get('target_posts') and report['posts']>=settings['target_posts']:
        return 'target_reached'
    if network_problem(report,time.time() if now is None else now):
        return 'network_unavailable'
    if report.get('free_disk_bytes',float('inf'))<settings.get('min_free_bytes',0):
        return 'disk_limit'
    if (report['attempts']>=settings['max_attempts'] or (time.time() if now is None else now)>=settings['deadline']
            or report['saved_response_bytes']>=settings['max_body_bytes']):
        return 'budget_reached'
    if sum(report['pages'].values())>=settings.get('max_discovered_pages',50000):
        return 'queue_limit'


def main():
    global RUN_STARTED_AT
    RUN_STARTED_AT=time.time()
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data',type=Path,default=Path('data/public-2026-10-16'))
    ap.add_argument('--max-attempts',type=int,default=5000)
    ap.add_argument('--hours',type=float,default=24)
    ap.add_argument('--max-body-mb',type=int,default=2000)
    ap.add_argument('--workers',type=int,choices=range(1,25),help='Fetch concurrency; starts share one host limiter')
    ap.add_argument('--parser-workers',type=int,choices=range(0,5),help='CPU parser processes; requests and SQLite remain in one collector')
    ap.add_argument('--cookies',type=Path,help='Private Netscape session file; no automatic login')
    ap.add_argument('--posts-per-page',type=int,choices=[20,30,40,50,100],help='Fixed page size; verify server support before migrating a guest queue')
    ap.add_argument('--target-posts',type=int,help='Stop at this many distinct post IDs, including imported posts')
    ap.add_argument('--max-discovered-pages',type=int,default=50000)
    ap.add_argument('--scan-start',type=int,help='First numeric thread ID, inclusive; cursor persists')
    ap.add_argument('--scan-end',type=int,help='Last numeric thread ID, inclusive')
    ap.add_argument('--complete-id-scan',action='store_true',help='Finish the numeric range; retained-post target becomes a milestone')
    ap.add_argument('--include-archive-ids',action='store_true',help='Also visit the historical 7M–8M thread block')
    ap.add_argument('--min-own-chars',type=int,help='Retain long own-text posts and sections by an OP with a long post')
    ap.add_argument('--all-thread-pages',action='store_true',help='Collect continuations without the length-based page cap')
    ap.add_argument('--min-free-gb',type=float,default=0,help='Stop before free disk space falls below this amount')
    ap.add_argument('--delay',type=float,help='Shared request-start interval, at least 0.02 second (50 total requests/second)')
    ap.add_argument('--novel-pilot',action='store_true',help='Exclude archive IDs; cap non-long threads at 20 pages')
    ap.add_argument('--candidate-limit',type=int,default=1000)
    ap.add_argument('--first-pages-only',action='store_true')
    ap.add_argument('--stories-only',action='store_true',help='Disable General listings; discover through story compilation links')
    ap.add_argument('--listing-pages',type=int,default=20)
    args=ap.parse_args()
    if min(args.max_attempts,args.hours,args.max_body_mb,args.listing_pages,args.candidate_limit)<=0:ap.error('Limits must be positive')
    if args.novel_pilot and not args.stories_only:ap.error('Novel pilot requires --stories-only')
    if args.first_pages_only and args.all_thread_pages:ap.error('Choose either first-page-only or all-page collection')
    if args.delay is not None and args.delay<MIN_REQUEST_INTERVAL:ap.error('Shared request interval must be at least 0.02 second')
    if args.complete_id_scan and not args.first_pages_only:ap.error('Complete ID scanning requires --first-pages-only')
    if args.target_posts is not None and args.target_posts<1:ap.error('Target posts must be positive')
    if args.max_discovered_pages<1:ap.error('Queue size must be positive')
    if args.min_own_chars is not None and args.min_own_chars<1:ap.error('Minimum own-text length must be positive')
    if args.min_free_gb<0:ap.error('Free disk reserve cannot be negative')
    root=args.data;root.mkdir(parents=True,exist_ok=True)
    def stop(signum,frame):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    with transport.host_lock(), (root/'collector.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        (root/'worker.pid').write_text(str(os.getpid())+'\n')
        db=collect.connect(root);setup(db)
        with db:
            config={'max_attempts':args.max_attempts,'deadline':time.time()+args.hours*3600,
                    'max_body_bytes':args.max_body_mb*1000000,'listing_pages':args.listing_pages,'max_discovered_pages':args.max_discovered_pages}
            configure_access(db,args.cookies,args.posts_per_page)
            id_scan.configure(db,args.scan_start,args.scan_end)
            if args.include_archive_ids:
                db.execute("INSERT OR REPLACE INTO public_config VALUES('exclude_archive_ids','false')")
            if args.min_own_chars is not None:
                previous=pilot_policy.config(db).get('min_own_chars')
                if previous is not None and previous!=args.min_own_chars:
                    raise SystemExit('Retention threshold must match the saved crawl configuration')
                db.execute('INSERT OR IGNORE INTO public_config VALUES(?,?)',('min_own_chars',json.dumps(args.min_own_chars)))
            db.execute('INSERT OR IGNORE INTO public_config VALUES(?,?)',('min_free_bytes',json.dumps(int(args.min_free_gb*1000000000))))
            if args.target_posts is not None:
                db.execute('INSERT OR IGNORE INTO public_config VALUES(?,?)',('target_posts',json.dumps(args.target_posts)))
            for k,v in config.items():db.execute('INSERT OR IGNORE INTO public_config VALUES(?,?)',(k,json.dumps(v)))
            for key,enabled in [('stories_only',args.stories_only),('novel_pilot',args.novel_pilot),('complete_id_scan',args.complete_id_scan)]:
                if enabled:
                    db.execute('INSERT OR REPLACE INTO public_config VALUES(?,?)',(key,'true'))
            if args.novel_pilot:
                db.execute('INSERT OR REPLACE INTO public_config VALUES(?,?)',('candidate_limit',json.dumps(args.candidate_limit)))
            if args.workers is not None:
                db.execute('INSERT OR REPLACE INTO public_config VALUES(?,?)',('workers',json.dumps(args.workers)))
            if args.parser_workers is not None:
                db.execute('INSERT OR REPLACE INTO public_config VALUES(?,?)',('parser_workers',json.dumps(args.parser_workers)))
            if args.delay is not None:
                db.execute('INSERT OR REPLACE INTO public_config VALUES(?,?)',('delay_seconds',json.dumps(args.delay)))
            configure_pagination(db,args.first_pages_only,args.all_thread_pages)
            config={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM public_config')}
            for page in range(1,config['listing_pages']+1):db.execute('INSERT OR IGNORE INTO listing_jobs(page) VALUES(?)',(page,))
            for item in SOURCES:
                url=collect.canonical(f"showthread.php?t={item['thread_id']}")
                seed(db,url,item['evidence'],item['title'])
                db.execute('UPDATE pages SET priority=max(priority,200) WHERE url=?',(url,))
        if config.get('workers',1)>1 and not config.get('stories_only'):
            raise SystemExit('Parallel fetch requires stories-only mode to share one host limiter')
        if config.get('parser_workers') and config.get('workers',1)<2:
            raise SystemExit('Parser workers require parallel fetch')
        client=transport.session(args.cookies,posts_per_page=args.posts_per_page)
        fetcher=Fetcher(config.get('workers',1),config.get('delay_seconds',5),args.cookies,args.posts_per_page,config.get('min_own_chars'),pipeline=config.get('complete_id_scan',False),parser_workers=config.get('parser_workers',0)) if config.get('workers',1)>1 else None
        final_state,final_error='stopped',None
        try:
            while not STOP:
                if not config.get('complete_id_scan'):
                    postprocess(db,root)
                with db:
                    if not config.get('complete_id_scan'):
                        pilot_policy.apply(db,CURATED)
                    id_scan.fill(db,seed,config)
                report=progress(db,root,'running')
                reason=stop_reason(report,config)
                if reason:
                    final_state=reason;break
                listing=None if config.get('stories_only') else db.execute("SELECT page FROM listing_jobs WHERE state='pending' ORDER BY page LIMIT 1").fetchone()
                # Interleave discovery with collection; one listing per ten thread requests.
                if listing and (report['attempts']%11==0 or not report['pages'].get('pending')):
                    page=listing[0];url=LISTING.format(page)
                    try:
                        response=client.get(url,timeout=(10,45),allow_redirects=False)
                        digest=save_raw(root,response.content)
                        links=listing_threads(response.content) if response.status_code==200 else []
                        state='done' if links else 'blocked'
                        with db:
                            db.execute('INSERT INTO fetch_events(url,status,body_bytes,fetched_at) VALUES(?,?,?,?)',
                                       (url,response.status_code,len(response.content),time.time()))
                            db.execute('UPDATE listing_jobs SET state=?,attempts=attempts+1,status=?,sha256=?,fetched_at=?,error=? WHERE page=?',
                                       (state,response.status_code,digest,time.time(),None if links else 'Unexpected listing response or no thread titles',page))
                            db.execute('INSERT OR REPLACE INTO response_sizes VALUES(?,?)',(url,len(response.content)))
                            for target,title in links:seed(db,target,url,title)
                        print(json.dumps(dict(listing=page,state=state,status=response.status_code,discovered=len(links))),flush=True)
                    except requests.RequestException as error:
                        with db:db.execute("UPDATE listing_jobs SET state='blocked',attempts=attempts+1,error=? WHERE page=?",(type(error).__name__,page))
                    time.sleep(config.get('delay_seconds',5))
                elif report['pages'].get('pending'):
                    due=db.execute("SELECT min(due) FROM pages WHERE state='pending'").fetchone()[0]
                    if due>time.time():time.sleep(min(30,due-time.time()));continue
                    if fetcher:
                        fetcher.batch(db,root,config['max_attempts']-report['attempts'],config.get('first_pages_only',False),discover=config.get('novel_pilot',False))
                    else:
                        collect.run(db,root,1,config.get('delay_seconds',5),cookies=args.cookies,client=client,max_thread_pages=1 if config.get('first_pages_only') else None,min_own_chars=config.get('min_own_chars'))
                else:
                    final_state='queue_exhausted';break
        except BaseException as error:
            final_state,final_error='failed',type(error).__name__
            raise
        finally:
            try:
                if fetcher:fetcher.close()
                if final_state!='failed' and crawl_metrics.counts(db,'crawl_page_states').get('blocked'):
                    final_state='paused'
                progress(db,root,final_state,final_error)
            finally:
                client.close();db.close()


if __name__=='__main__':main()
