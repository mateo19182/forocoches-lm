import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import collect
import public_crawl
from test_collect import HTML, URL


class PublicCrawlTests(unittest.TestCase):
    @patch('public_crawl.signal.signal')
    @patch('public_crawl.transport.host_lock')
    @patch('public_crawl.transport.session')
    def test_complete_scan_pipeline_publishes_final_persisted_counts(self,session,host_lock,signal):
        session.return_value.get.side_effect=lambda url,**kwargs:SimpleNamespace(status_code=200,content=HTML,headers={},url=url)
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            args=['public_crawl.py','--data',directory,'--stories-only','--workers','2','--parser-workers','2','--delay','0.028571428572',
                  '--scan-start','1','--scan-end','3','--first-pages-only','--complete-id-scan','--max-attempts','3']
            with patch('sys.argv',args),patch('public_crawl.SOURCES',[]),patch('public_crawl.STOP',False),patch('public_crawl.LAST_EXPORT_ATTEMPT',None):
                public_crawl.main()
            status=json.loads((root/'status.json').read_text())
            with sqlite3.connect(root/'crawl.sqlite') as db:
                self.assertEqual(status['state'],'scan_complete')
                self.assertEqual(status['attempts'],3)
                self.assertEqual(db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],3)
                self.assertEqual(status['pages'],{'done':3})
                self.assertEqual(status['limits']['parser_workers'],2)
                self.assertTrue(status['id_scan']['complete'])
            self.assertEqual(session.return_value.get.call_count,3)

    def test_discovery_uses_listing_titles_only(self):
        html=b'''<a href="showthread.php?t=99">navigation</a>
        <a id="thread_title_10" href="showthread.php?t=10">Story</a>
        <a id="thread_title_11" href="https://example.test/foro/showthread.php?t=11">External</a>'''
        self.assertEqual(public_crawl.listing_threads(html),[(URL,'Story')])
        self.assertIsNone(public_crawl.thread_link('https://evil.test/foro/showthread.php?t=10'))
        self.assertEqual(public_crawl.thread_link('https://www.forocoches.com/foro/showthread.php?t=10'),URL)

    @patch('public_crawl.source_yield')
    @patch('public_crawl.export_long_posts')
    def test_exports_continue_when_parallel_attempt_counts_are_odd(self,export,yield_report):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=collect.connect(root);public_crawl.setup(db)
            with db:
                collect.enqueue(db,[URL])
                db.execute('UPDATE pages SET attempts=9')
            public_crawl.progress(db,root,'running')
            with db:db.execute('UPDATE pages SET attempts=109')
            public_crawl.progress(db,root,'running')
            self.assertEqual(export.call_count,2)
            db.close()

    def test_focused_discovery_requires_index_or_story_hint(self):
        index=collect.canonical('showthread.php?t=1979428')
        story=collect.canonical('showthread.php?t=123')
        self.assertTrue(public_crawl.relevant_link(index,'https://forocoches.com/foro/showthread.php?t=20'))
        self.assertTrue(public_crawl.relevant_link(story,'Mi relato de ayer'))
        self.assertFalse(public_crawl.relevant_link(story,'Noticias del día'))
        self.assertFalse(public_crawl.relevant_link('https://example.test','relato'))
        self.assertFalse(public_crawl.relevant_link('https://forocoches.com/foro/forumdisplay.php?f=2','historia'))

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_long_openers_prioritize_replies_without_dropping_short_posts(self,session,sleep):
        response=session.return_value.get.return_value
        response.status_code=200;response.headers={}
        response.content=HTML.replace(b'Hello',b'A'*2500+b'<a href="https://www.forocoches.com/foro/showthread.php?t=20&page=3">relato</a>')
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root);public_crawl.setup(db)
            with db:public_crawl.seed(db,URL,'test source')
            collect.run(db,root,1,5)
            public_crawl.postprocess(db,root)
            next_url=collect.canonical('showthread.php?t=10&page=2')
            self.assertGreater(db.execute('SELECT priority FROM pages WHERE url=?',(next_url,)).fetchone()[0],100)
            other=collect.canonical('showthread.php?t=20')
            self.assertEqual(db.execute('SELECT source FROM discoveries WHERE url=?',(other,)).fetchone()[0],URL)
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM long_posts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT sum(body_bytes) FROM fetch_events').fetchone()[0],len(response.content))
            before=db.execute('SELECT count(*) FROM pages').fetchone()[0]
            public_crawl.postprocess(db,root)
            self.assertEqual(db.execute('SELECT count(*) FROM pages').fetchone()[0],before)
            db.close()


if __name__=='__main__':unittest.main()
