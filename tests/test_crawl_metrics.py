import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import collect
import crawl_metrics
import public_crawl
from test_collect import HTML,URL


class CounterTests(unittest.TestCase):
    def test_persisted_server_backoff_survives_network_health_checks(self):
        settings={'complete_id_scan':True,'max_attempts':100,'deadline':1000,'max_body_bytes':100000}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with collect.connect(root) as db:
                public_crawl.setup(db)
                public_crawl.id_scan.configure(db,1,10)
                db.executemany('INSERT INTO public_config VALUES(?,?)',[(k,json.dumps(v)) for k,v in settings.items()])
                collect.enqueue(db,[URL])
                db.execute("UPDATE pages SET due=550,status=503,error='Server error'")
                db.execute('INSERT INTO fetch_events(url,status,body_bytes,fetched_at) VALUES(?,503,0,200)',(URL,))
                with patch('public_crawl.time.time',return_value=500),patch('public_crawl.RUN_STARTED_AT',100):
                    report=public_crawl.progress(db,root,'running')
                self.assertEqual(report['next_request_due_at'],550)
                self.assertEqual(report['pending_retry_error'],'Server error')
                self.assertEqual(report['pending_retry_http_status'],503)
                self.assertEqual(report['last_fetch_status'],503)
                self.assertIsNone(public_crawl.stop_reason(report,settings,now=500))
                self.assertIsNone(public_crawl.stop_reason(report,settings,now=551))
                self.assertEqual(public_crawl.stop_reason(report,settings,now=611),'network_unavailable')

    def test_disconnected_vpn_stops_requests_and_gives_a_fresh_run_grace(self):
        settings={'max_attempts':100,'deadline':1000,'max_body_bytes':100000}
        report={'posts':0,'attempts':10,'saved_response_bytes':0,'pages':{},'listing_pages':{},
                'route':'airvpn','network_ready':False,'run_started_at':490,'last_http_response_at':100}
        self.assertEqual(public_crawl.stop_reason(report,settings,now=500),'network_unavailable')
        report['network_ready']=True
        self.assertIsNone(public_crawl.stop_reason(report,settings,now=500))
        report['run_started_at']=100
        self.assertEqual(public_crawl.stop_reason(report,settings,now=500),'network_unavailable')

    def assert_matches_tables(self,db):
        self.assertEqual(crawl_metrics.total(db,'posts'),db.execute('SELECT count(*) FROM posts').fetchone()[0])
        self.assertEqual(crawl_metrics.total(db,'candidates'),db.execute('SELECT count(*) FROM pilot_candidates').fetchone()[0])
        self.assertEqual(crawl_metrics.page_count(db),db.execute('SELECT count(*) FROM pages').fetchone()[0])
        self.assertEqual(crawl_metrics.counts(db,'crawl_page_states'),dict(db.execute('SELECT state,count(*) FROM pages GROUP BY state')))
        self.assertEqual(crawl_metrics.counts(db,'crawl_scan_states'),dict(db.execute('SELECT state,count(*) FROM pages WHERE scan_order IS NOT NULL GROUP BY state')))
        self.assertEqual(crawl_metrics.total(db,'attempts'),sum(db.execute(f'SELECT coalesce(sum(attempts),0) FROM {table}').fetchone()[0] for table in ('pages','listing_jobs')))
        self.assertEqual(crawl_metrics.total(db,'saved_response_bytes'),db.execute('SELECT coalesce(sum(body_bytes),0) FROM fetch_events').fetchone()[0])
        self.assertEqual(crawl_metrics.total(db,'page_post_observations'),db.execute('SELECT coalesce(sum(posts_seen),0) FROM retention_pages').fetchone()[0])
        self.assertEqual(db.execute('SELECT count(*) FROM crawl_thread_posts').fetchone()[0],db.execute('SELECT count(distinct thread_id) FROM posts').fetchone()[0])
        self.assertEqual(crawl_metrics.counts(db,'crawl_retention_states'),dict(db.execute('SELECT reason,count(*) FROM post_retention_meta GROUP BY reason')))

    def test_insert_update_upsert_delete_and_reinitialisation(self):
        response=SimpleNamespace(status_code=200,headers={},content=HTML.replace(b'Hello',b'Story '*500))
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            with collect.connect(root) as db:
                public_crawl.setup(db);public_crawl.seed(db,URL,'test')
                db.execute('UPDATE pages SET scan_order=10 WHERE url=?',(URL,))
                self.assert_matches_tables(db)
                for attempt in (0,1):
                    collect.save_response(db,root,URL,attempt,response,1,min_own_chars=2000)
                    self.assert_matches_tables(db)
                db.execute('UPDATE posts SET thread_id=20');self.assert_matches_tables(db)
                db.execute('DELETE FROM posts');self.assert_matches_tables(db)
                db.execute('DELETE FROM pages');self.assert_matches_tables(db)
                public_crawl.setup(db);self.assert_matches_tables(db)

    def test_initialises_from_existing_archive_without_changing_data(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            with collect.connect(root) as db:
                collect.enqueue(db,[URL])
                response=SimpleNamespace(status_code=200,headers={},content=HTML)
                collect.save_response(db,root,URL,0,response,1)
                before=db.execute('SELECT * FROM posts').fetchall()
                public_crawl.setup(db);self.assert_matches_tables(db)
                self.assertEqual(before,db.execute('SELECT * FROM posts').fetchall())

    def test_full_range_mode_ignores_post_milestone_and_stops_at_scan_end(self):
        settings={'complete_id_scan':True,'target_posts':10,'max_attempts':100,'deadline':1000,'max_body_bytes':100000}
        report={'posts':20,'attempts':1,'saved_response_bytes':1,'pages':{},'listing_pages':{},'id_scan':{'complete':False}}
        self.assertIsNone(public_crawl.stop_reason(report,settings,now=10))
        report['id_scan']['complete']=True
        self.assertEqual(public_crawl.stop_reason(report,settings,now=10),'scan_complete')
        report['pages']['blocked']=1
        self.assertEqual(public_crawl.stop_reason(report,settings,now=10),'paused')


if __name__=='__main__':unittest.main()
