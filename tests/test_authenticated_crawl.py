import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

import collect
from monitor_crawl import assess
from parallel_fetch import Fetcher
import pilot_policy
import public_crawl
import transport
from test_collect import HTML, URL


class AuthenticatedCrawlTests(unittest.TestCase):
    def test_first_page_batch_does_not_queue_pagination_links(self):
        response=SimpleNamespace(status_code=200,headers={},content=HTML)
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            with collect.connect(root) as db:
                collect.enqueue(db,[URL])
                collect.save_response(db,root,URL,0,response,max_thread_pages=1)
                self.assertEqual(db.execute('SELECT url FROM pages').fetchall(),[(URL,)])
                self.assertEqual(db.execute('SELECT page_count_observed FROM threads').fetchone()[0],100)

    def test_session_pins_page_size_without_logging_cookie_values(self):
        with transport.session(posts_per_page=30) as client:
            request=client.prepare_request(requests.Request('GET',URL))
            self.assertIn('pp=30',request.url)

    def test_host_lock_rejects_a_second_collector(self):
        with tempfile.TemporaryDirectory() as directory, patch('transport.HOST_LOCK',Path(directory)/'host.lock'):
            with transport.host_lock():
                with self.assertRaises(SystemExit),transport.host_lock():
                    self.fail('Second collector acquired the host lock')
            with transport.host_lock():
                pass

    def test_resume_cannot_silently_drop_authentication_or_change_page_size(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                public_crawl.configure_access(db,Path('private.cookies'),30)
                public_crawl.configure_access(db,Path('private.cookies'),30)
                with self.assertRaises(SystemExit):public_crawl.configure_access(db,None,30)
                with self.assertRaises(SystemExit):public_crawl.configure_access(db,Path('private.cookies'),20)

    def test_airvpn_crawl_rejects_a_direct_route_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                with patch.dict('os.environ',{'FC_NETWORK_ROUTE':'airvpn'}):
                    public_crawl.configure_access(db,Path('private.cookies'),30)
                with patch.dict('os.environ',{'FC_NETWORK_ROUTE':'direct'}):
                    with self.assertRaises(SystemExit):public_crawl.configure_access(db,Path('private.cookies'),30)

    @patch('parallel_fetch.transport.session')
    def test_authenticated_permission_loss_stops_waiting_requests(self,session):
        response=SimpleNamespace(status_code=302,content=b'',headers={
            'Location':'https://forocoches.com/foro/misc.php?do=page&template=Info&tid=10'})
        session.return_value.get.return_value=response
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            with collect.connect(root) as db:
                collect.enqueue(db,[URL,collect.canonical('showthread.php?t=20')])
                fetcher=Fetcher(2,.2,Path('private.cookies'),30)
                try:fetcher.batch(db,root,2)
                finally:fetcher.close()
                session.assert_called_with(Path('private.cookies'),posts_per_page=30)
                self.assertEqual(session.return_value.get.call_count,1)
                self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(URL,)).fetchone()[0],'blocked')

    def test_legacy_index_discovers_body_links_and_records_actual_request_url(self):
        index=collect.canonical('showthread.php?t=9000001')
        target=collect.canonical('showthread.php?t=9000002')
        response=SimpleNamespace(status_code=200,headers={},url=index+'&pp=30',content=b'''
          <title>Recopilacion de historias</title><table id="post123">
          <tr><td class="thead">Hoy</td></tr><tr><td>
          <a href="member.php?u=4">Author</a><div id="post_message_123" />
          <a href="showthread.php?t=9000002">click</a></div></td></tr></table>''')
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            with collect.connect(root) as db:
                public_crawl.setup(db)
                db.executemany('INSERT INTO public_config VALUES(?,?)',[('novel_pilot','true'),('stories_only','true')])
                public_crawl.seed(db,index,'test')
                collect.save_response(db,root,index,0,response,cookies=Path('private.cookies'))
                public_crawl.postprocess(db,root)
                self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(target,)).fetchone()[0],'pending')
                self.assertEqual(db.execute('SELECT request_url FROM fetch_events').fetchone()[0],response.url)

    def test_long_reply_releases_continuations_even_with_short_opener(self):
        first=collect.canonical('showthread.php?t=9000001')
        later=collect.canonical('showthread.php?t=9000001&page=21')
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                db.execute("INSERT INTO public_config VALUES('novel_pilot','true')")
                public_crawl.seed(db,first,'test')
                collect.enqueue(db,[later])
                for pid,position,text in ((1,1,'Short opener'),(2,2,'Narrative '*300)):
                    payload=json.dumps({'position':position,'text':text,'html':f'<div>{text}</div>'})
                    db.execute('INSERT INTO posts VALUES(?,?,?,?,?)',(pid,9000001,first,payload,0))
                db.execute("UPDATE pages SET state='deferred_cap' WHERE url=?",(later,))
                self.assertTrue(pilot_policy.observe(db,9000001,first))
                pilot_policy.apply(db,set())
                self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(later,)).fetchone()[0],'pending')

    def test_target_counts_distinct_ids_and_access_blocks_take_precedence(self):
        settings={'target_posts':500000,'max_attempts':100000,'deadline':1000,'max_body_bytes':1000000}
        report={'posts':499999,'attempts':10,'saved_response_bytes':100,'pages':{},'listing_pages':{}}
        self.assertIsNone(public_crawl.stop_reason(report,settings,now=10))
        report['posts']=500001
        self.assertEqual(public_crawl.stop_reason(report,settings,now=10),'target_reached')
        self.assertEqual(assess({'state':'target_reached'},False,10)[0],'target_reached')
        report['pages']['blocked']=1
        self.assertEqual(public_crawl.stop_reason(report,settings,now=10),'paused')

    def test_disk_reserve_stops_the_crawl_before_more_requests(self):
        settings={'min_free_bytes':100,'max_attempts':100,'deadline':100,'max_body_bytes':10000}
        report={'posts':0,'attempts':0,'saved_response_bytes':0,'pages':{},'listing_pages':{},'free_disk_bytes':99}
        self.assertEqual(public_crawl.stop_reason(report,settings,now=10),'disk_limit')
        self.assertEqual(assess({'state':'disk_limit'},False,10)[1],'warning')


if __name__=='__main__':
    unittest.main()
