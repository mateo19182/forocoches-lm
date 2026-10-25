import contextlib
import gzip
import io
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import requests
import collect
from parallel_fetch import Fetcher
from test_collect import HTML

class ParallelTests(unittest.TestCase):
    def test_worker_preparation_keeps_notice_policy_and_exact_raw_bytes(self):
        url=collect.canonical('showthread.php?t=1')
        cookies=Path('session.cookies')
        logout='<a href="login.php?do=logout">Logout</a>'
        permission='No tienes permisos para acceder a esta página. Falta de validación de tu cuenta'
        empty='<input id="qr_threadid" value="1"><div id="posts"><div id="lastpost"></div></div>'
        cases=[(HTML,cookies,'done'),
               ('Tema especificado inválido.',cookies,'missing'),
               ('No has iniciado sesión o no tienes permiso para acceder a esta página.',cookies,'blocked'),
               ('No has iniciado sesión o no tienes permiso para acceder a esta página.',None,'restricted'),
               (permission+logout,cookies,'restricted'),(permission+logout,None,'blocked'),
               (permission+logout+'captcha',cookies,'blocked'),
               (empty+logout,cookies,'empty'),(empty+logout,None,'blocked'),
               (empty+logout+'<script src="/cf-chl-challenge.js"></script>',cookies,'blocked'),
               ('Unexpected page',cookies,'blocked')]
        for html,cookie_file,expected in cases:
            with self.subTest(expected=expected,html=html):
                content=html.encode() if isinstance(html,str) else html
                with patch('collect.page_soup',wraps=collect.page_soup) as soup:
                    parsed,compressed=collect.prepare_page(content,url)
                    self.assertEqual(soup.call_count,1)
                response=SimpleNamespace(status_code=200,headers={},content=content,
                                         _fc_parsed=parsed,_fc_raw_gzip=compressed)
                with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
                    root=Path(directory);db=collect.connect(root)
                    with db:collect.enqueue(db,[url])
                    with patch('collect.page_soup',side_effect=AssertionError('Duplicate page DOM')), \
                         patch('collect.compress_raw',side_effect=AssertionError('Duplicate compression')):
                        self.assertEqual(collect.save_response(db,root,url,0,response,cookies=cookie_file),expected)
                    raw=next(root.glob('raw/*/*.html.gz'))
                    self.assertEqual(gzip.decompress(raw.read_bytes()),content)
                    self.assertEqual(db.execute('SELECT body_bytes FROM fetch_events').fetchone()[0],len(content))
                    db.close()

    def test_prepared_missing_page_is_saved_without_building_another_dom(self):
        url=collect.canonical('showthread.php?t=1')
        content='<main>Información Tema especificado inválido.</main>'.encode()
        response=SimpleNamespace(status_code=200,headers={},content=content,
                                 _fc_parsed=collect.parse(content,url))
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[url])
            with patch('collect.page_soup',side_effect=AssertionError('Duplicate page DOM')):
                self.assertEqual(collect.save_response(db,root,url,0,response),'missing')
            db.close()

    def test_prepared_page_is_saved_without_parsing_again(self):
        url=collect.canonical('showthread.php?t=1')
        response=SimpleNamespace(status_code=200,headers={},content=HTML,_fc_parsed=collect.parse(HTML,url))
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[url])
            with patch('collect.parse',side_effect=AssertionError('Unexpected duplicate parse')):
                self.assertEqual(collect.save_response(db,root,url,0,response),'done')
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0],1)
            db.close()

    @patch('parallel_fetch.ProcessPoolExecutor')
    @patch('parallel_fetch.transport.session')
    def test_parser_worker_failure_stops_starts_and_preserves_raw_response(self,session,pool):
        pool.return_value.submit.return_value.result.side_effect=RuntimeError('Parser unavailable')
        session.return_value.get.return_value=SimpleNamespace(status_code=200,headers={},content=HTML)
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in (1,2)])
            fetcher=Fetcher(2,.1,pipeline=True,parser_workers=2)
            try:fetcher.batch(db,root,2)
            finally:fetcher.close()
            self.assertEqual(session.return_value.get.call_count,1)
            state,digest,error=db.execute("SELECT state,sha256,error FROM pages WHERE state='blocked'").fetchone()
            self.assertEqual(error,'Parser worker error: RuntimeError')
            self.assertEqual(gzip.decompress((root/'raw'/digest[:2]/(digest+'.html.gz')).read_bytes()),HTML)
            self.assertEqual(db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],1)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_connection_errors_keep_request_timing_for_outage_diagnostics(self,session):
        session.return_value.get.side_effect=requests.ConnectionError('Connection unavailable')
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            url=collect.canonical('showthread.php?t=1')
            with db:collect.enqueue(db,[url])
            fetcher=Fetcher(2,.01,pipeline=True)
            try:fetcher.batch(db,root,1)
            finally:fetcher.close()
            request_url,started,finished,status=db.execute('SELECT request_url,request_started_at,request_finished_at,status FROM fetch_events').fetchone()
            self.assertEqual(request_url,url)
            self.assertIsInstance(started,float)
            self.assertGreaterEqual(finished,started)
            self.assertIsNone(status)
            self.assertEqual(db.execute('SELECT state,error FROM pages').fetchone(),('pending','ConnectionError'))
            db.close()
    @patch('parallel_fetch.transport.session')
    def test_pipeline_shutdown_preserves_already_started_responses(self,session):
        release=threading.Event()
        first_started=threading.Event()
        def get(url,**kwargs):
            if 't=1&' in url:
                first_started.set();release.wait(2)
            else:self.assertTrue(first_started.wait(2))
            return SimpleNamespace(status_code=200,content=HTML,headers={})
        session.return_value.get.side_effect=get
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in (1,2)])
            fetcher=Fetcher(2,.01,pipeline=True)
            try:
                fetcher.batch(db,root,2)
                self.assertEqual(db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],1)
            finally:
                release.set();fetcher.close()
            self.assertEqual(db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='done'").fetchone()[0],2)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_pipeline_reserves_attempt_budget_for_inflight_requests(self,session):
        release=threading.Event()
        def get(url,**kwargs):
            if 't=1&' in url:release.wait(2)
            return SimpleNamespace(status_code=200,content=HTML,headers={})
        session.return_value.get.side_effect=get
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in (1,2,3)])
            fetcher=Fetcher(2,.01,pipeline=True)
            try:
                fetcher.batch(db,root,2)
                release.set()
                fetcher.batch(db,root,1)
                self.assertFalse(fetcher.inflight)
            finally:
                release.set();fetcher.close()
            self.assertEqual(session.return_value.get.call_count,2)
            self.assertEqual(db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],2)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_pipeline_access_stop_cancels_starts_and_archives_late_results(self,session):
        release=threading.Event()
        def get(url,**kwargs):
            if 't=1&' in url:
                release.wait(2)
                return SimpleNamespace(status_code=200,content=HTML,headers={})
            return SimpleNamespace(status_code=429,content=b'',headers={})
        session.return_value.get.side_effect=get
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in (1,2,3)])
            fetcher=Fetcher(3,.03,pipeline=True)
            try:
                fetcher.batch(db,root,3)
                self.assertTrue(fetcher.stopped.is_set())
            finally:
                release.set();fetcher.close()
            self.assertEqual(session.return_value.get.call_count,2)
            self.assertEqual(db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='blocked'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='pending'").fetchone()[0],1)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_slow_request_does_not_hold_up_free_workers(self,session):
        third_started=threading.Event()
        first_saw_third=[]
        starts=[]
        def get(url,**kwargs):
            starts.append(url)
            if 't=1&' in url:first_saw_third.append(third_started.wait(.5))
            if 't=3&' in url:third_started.set()
            return SimpleNamespace(status_code=200,content=HTML,headers={})
        session.return_value.get.side_effect=get
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in (1,2,3)])
            fetcher=Fetcher(2,.01);fetcher.pipeline=True
            try:
                while db.execute('SELECT count(*) FROM fetch_events').fetchone()[0]<3:
                    fetcher.batch(db,root,3-db.execute('SELECT count(*) FROM fetch_events').fetchone()[0])
            finally:fetcher.close()
            self.assertEqual(first_saw_third,[True])
            self.assertEqual(len(starts),len(set(starts)))
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='done'").fetchone()[0],3)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_pipeline_workers_store_raw_bodies_off_the_writer_thread(self,session):
        session.return_value.get.return_value=SimpleNamespace(status_code=200,content=HTML,headers={})
        writer=threading.get_ident();stored_on=[]
        store=collect.store_raw
        def tracked(root,response):
            stored_on.append(threading.get_ident());return store(root,response)
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()), \
             patch('collect.store_raw',side_effect=tracked):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in (1,2)])
            fetcher=Fetcher(2,.01);fetcher.pipeline=True
            try:
                while db.execute('SELECT count(*) FROM fetch_events').fetchone()[0]<2:
                    fetcher.batch(db,root,2-db.execute('SELECT count(*) FROM fetch_events').fetchone()[0])
            finally:fetcher.close()
            self.assertEqual(len(stored_on),2)
            self.assertNotIn(writer,stored_on)
            self.assertEqual(len(list(root.glob('raw/*/*.html.gz'))),1)
            self.assertEqual(list(root.glob('raw/*/*.tmp')),[])
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='done'").fetchone()[0],2)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_challenge_header_stops_even_when_http_status_is_200(self,session):
        session.return_value.get.return_value=SimpleNamespace(status_code=200,content=HTML,headers={'cf-mitigated':'challenge'})
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in (1,2)])
            fetcher=Fetcher(8,.1)
            try:fetcher.batch(db,root,2)
            finally:fetcher.close()
            self.assertEqual(session.return_value.get.call_count,1)
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='blocked'").fetchone()[0],1)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_eight_workers_still_share_one_request_start_interval(self,session):
        starts=[];lock=threading.Lock()
        def get(url,**kwargs):
            with lock:starts.append(time.monotonic())
            time.sleep(.08)
            return SimpleNamespace(status_code=200,content=HTML,headers={})
        session.return_value.get.side_effect=get
        fetcher=Fetcher(8,.02)
        futures=[fetcher.pool.submit(fetcher.fetch,collect.canonical(f'showthread.php?t={tid}')) for tid in range(1,9)]
        try:
            for future in futures:self.assertEqual(future.result().status_code,200)
        finally:fetcher.close()
        self.assertEqual(len(starts),8)
        self.assertTrue(all(b-a>=.015 for a,b in zip(starts,starts[1:])))

    def test_discovery_reserves_slot_without_starving_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            db=collect.connect(Path(directory))
            first=collect.canonical('showthread.php?t=9000001')
            later=collect.canonical('showthread.php?t=9000002&page=2')
            other=collect.canonical('showthread.php?t=9000003')
            collect.enqueue(db,[first,later,other])
            db.execute('UPDATE pages SET priority=500 WHERE url=?',(later,))
            jobs=Fetcher.jobs(db,2,True)
            self.assertEqual([x[0] for x in jobs],[first,later])
            db.execute("UPDATE pages SET due=? WHERE url IN (?,?)",(time.time()+100,first,other))
            self.assertEqual(Fetcher.jobs(db,2,True),[(later,0)])
            self.assertEqual(Fetcher.jobs(db,0,True),[])
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_two_workers_share_start_limiter_and_single_writer(self,session):
        starts=[]
        both_started=threading.Event()
        lock=threading.Lock()
        def get(url,**kwargs):
            with lock:
                starts.append(time.monotonic())
                if len(starts)==2:both_started.set()
            self.assertTrue(both_started.wait(2))
            body=HTML if 't=10&' in url else HTML.replace(b'post_message_123',b'post_message_456')
            return SimpleNamespace(status_code=200,content=body,headers={})
        session.return_value.get.side_effect=get
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in [10,20]])
            fetcher=Fetcher(2,.03)
            try:fetcher.batch(db,root,2)
            finally:fetcher.close()
            self.assertEqual(len(starts),2)
            self.assertGreaterEqual(starts[1]-starts[0],.025)
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],2)
            db.close()

    @patch('parallel_fetch.transport.session')
    def test_block_cancels_waiting_start(self,session):
        session.return_value.get.return_value=SimpleNamespace(status_code=429,content=b'',headers={})
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical(f'showthread.php?t={tid}') for tid in [10,20]])
            fetcher=Fetcher(2,.1)
            try:fetcher.batch(db,root,2)
            finally:fetcher.close()
            self.assertEqual(session.return_value.get.call_count,1)
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='blocked'").fetchone()[0],1)
            db.close()

if __name__=='__main__':unittest.main()
