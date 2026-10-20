"""Fetch workers share one start limiter and one SQLite writer."""
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
import multiprocessing
import threading
import time

import requests
import collect
import transport


class Fetcher:
    def __init__(self, workers=2, interval=2, cookies=None, posts_per_page=None,min_own_chars=None,pipeline=False,parser_workers=0):
        # Parser children must not inherit live network sessions or thread locks.
        self.parsers=ProcessPoolExecutor(parser_workers,mp_context=multiprocessing.get_context('spawn')) if parser_workers else None
        self.pool=ThreadPoolExecutor(max_workers=workers)
        self.workers=workers
        self.interval=interval
        self.cookies=cookies
        self.posts_per_page=posts_per_page
        self.min_own_chars=min_own_chars
        self.lock=threading.Lock()
        self.stopped=threading.Event()
        self.next_start=0
        self.local=threading.local()
        self.clients=[]
        self.clients_lock=threading.Lock()
        self.pipeline=pipeline
        self.inflight={}
        self.context=None

    def fetch(self,url):
        if not hasattr(self.local,'client'):
            self.local.client=transport.session(self.cookies,posts_per_page=self.posts_per_page)
            with self.clients_lock:self.clients.append(self.local.client)
        with self.lock:
            wait=max(0,self.next_start-time.monotonic())
            if self.stopped.wait(wait):return None
            self.next_start=time.monotonic()+self.interval
        if self.stopped.is_set():return None
        try:
            started=time.time()
            response=self.local.client.get(url,timeout=(10,45),allow_redirects=False)
            response._fc_started_at=started
            response._fc_finished_at=time.time()
            if response.status_code in (401,403,429) or response.headers.get('cf-mitigated')=='challenge':self.stopped.set()
            if self.parsers and response.status_code==200 and response.headers.get('cf-mitigated')!='challenge':
                try:
                    response._fc_parsed=self.parsers.submit(collect.parse,response.content,url).result()
                except Exception as error:
                    response._fc_parse_error=error
                    self.stopped.set()
            return response
        except requests.RequestException as error:
            error._fc_started_at=started
            error._fc_finished_at=time.time()
            return error

    def batch(self,db,root,remaining,first_pages=True,discover=False):
        if self.pipeline:
            self.context=(db,root,first_pages)
            limit=max(0,min(self.workers,remaining)-len(self.inflight))
            if limit and not self.stopped.is_set():
                active={url for url,_ in self.inflight.values()}
                candidates=self.jobs(db,limit+len(active),discover,first_pages)
                jobs=[(url,attempts) for url,attempts in candidates if url not in active][:limit]
                for url,attempts in jobs:
                    self.inflight[self.pool.submit(self.fetch,url)]=(url,attempts)
            if self.inflight:
                completed,_=wait(self.inflight,return_when=FIRST_COMPLETED)
                for future in completed:
                    url,attempts=self.inflight.pop(future)
                    self.save(db,root,url,attempts,future.result(),first_pages)
            return
        jobs=self.jobs(db,min(self.workers,remaining),discover,first_pages)
        futures=[(url,attempts,self.pool.submit(self.fetch,url)) for url,attempts in jobs]
        for url,attempts,future in futures:
            self.save(db,root,url,attempts,future.result(),first_pages)

    def save(self,db,root,url,attempts,response,first_pages):
        if response is None:return
        if isinstance(response,requests.RequestException):
            with db:
                db.execute('INSERT INTO fetch_events(url,status,body_bytes,fetched_at,request_url,request_started_at,request_finished_at) VALUES(?,NULL,0,?,?,?,?)',
                           (url,time.time(),url,getattr(response,'_fc_started_at',None),getattr(response,'_fc_finished_at',None)))
                db.execute("UPDATE pages SET attempts=attempts+1,state=?,due=?,error=? WHERE url=?",
                           ('failed' if attempts>=7 else 'pending',time.time()+min(3600,30*2**min(attempts,7)),type(response).__name__,url))
        else:
            state=collect.save_response(db,root,url,attempts,response,1 if first_pages else None,self.cookies,self.min_own_chars)
            if state=='blocked':self.stopped.set()

    @staticmethod
    def jobs(db,limit,discover=False,first_pages=False):
        if first_pages:
            return db.execute("SELECT url,attempts FROM pages WHERE state='pending' AND due<=? AND url LIKE '%&page=1' ORDER BY (scan_order IS NULL),scan_order,priority DESC,due,url LIMIT ?",
                              (time.time(),limit)).fetchall()
        if discover:
            # Reserve one slot for new openers, leaving the other for continuations/indexes.
            first=db.execute("SELECT url,attempts FROM pages WHERE state='pending' AND due<=? AND scan_order IS NOT NULL ORDER BY scan_order LIMIT 1",(time.time(),)).fetchall()
            if not first:
                first=db.execute("SELECT url,attempts FROM pages WHERE state='pending' AND due<=? AND url LIKE '%&page=1' ORDER BY priority DESC,due,url LIMIT 1",(time.time(),)).fetchall()
            if first:
                rest=db.execute("SELECT url,attempts FROM pages WHERE state='pending' AND due<=? AND url!=? ORDER BY (url NOT LIKE '%&page=1') DESC,priority DESC,due,url LIMIT ?",
                                (time.time(),first[0][0],max(0,limit-1))).fetchall()
                return first+rest if limit else []
        return db.execute("SELECT url,attempts FROM pages WHERE state='pending' AND due<=? ORDER BY priority DESC,due,url LIMIT ?",
                        (time.time(),limit)).fetchall()

    def close(self):
        self.stopped.set()
        self.pool.shutdown(wait=True,cancel_futures=True)
        try:
            for future,(url,attempts) in list(self.inflight.items()):
                if not future.cancelled():
                    db,root,first_pages=self.context
                    self.save(db,root,url,attempts,future.result(),first_pages)
                del self.inflight[future]
        finally:
            if self.parsers:self.parsers.shutdown(wait=True,cancel_futures=True)
            for client in self.clients:client.close()
