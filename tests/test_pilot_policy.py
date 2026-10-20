import json
from pathlib import Path
import tempfile
import unittest

import collect
import pilot_policy
import public_crawl


class PilotTests(unittest.TestCase):
    def test_boundary_limit_and_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            db = collect.connect(Path(directory))
            public_crawl.setup(db)
            db.executemany('INSERT INTO public_config VALUES(?,?)', [('novel_pilot','true'),('candidate_limit','2')])
            url = lambda tid, page=1: collect.canonical(f'showthread.php?t={tid}&page={page}')
            self.assertFalse(pilot_policy.allow_seed(db,url(7000000)))
            self.assertFalse(pilot_policy.allow_seed(db,url(7999999)))
            self.assertTrue(pilot_policy.allow_seed(db,url(8000000)))
            public_crawl.seed(db,url(8000000),'test')
            public_crawl.seed(db,url(6999999),'test')
            self.assertFalse(pilot_policy.allow_seed(db,url(9000000)))
            self.assertTrue(pilot_policy.allow_seed(db,url(8000000)))
            collect.enqueue(db,[url(8000000,21),url(6999999,21),url(7100000)])
            payload = json.dumps({'position':1,'html':'<div>'+'story '*500+'</div>'})
            db.execute('INSERT INTO posts VALUES(?,?,?,?,?)',(1,8000000,url(8000000),payload,0))
            pilot_policy.apply(db,set())
            states=dict(db.execute('SELECT url,state FROM pages'))
            self.assertEqual(states[url(8000000,21)],'pending')
            self.assertEqual(states[url(6999999,21)],'deferred_cap')
            self.assertEqual(states[url(7100000)],'excluded_archive')
            db.close()

    def test_later_compilation_pages_accept_unlabelled_links(self):
        self.assertTrue(public_crawl.relevant_link(collect.canonical('showthread.php?t=1979428&page=3'),'click'))

    def test_index_upgrade_revisits_saved_pages_and_releases_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            db=collect.connect(Path(directory));public_crawl.setup(db)
            db.execute("INSERT INTO public_config VALUES('novel_pilot','true')")
            first=collect.canonical('showthread.php?t=9000001')
            later=collect.canonical('showthread.php?t=9000001&page=21')
            public_crawl.seed(db,first,'test','click here')
            collect.enqueue(db,[later])
            db.execute("UPDATE pages SET state='deferred_cap' WHERE url=?",(later,))
            db.execute('INSERT INTO processed_pages VALUES(?)',(first,))
            public_crawl.seed(db,first,'test','Recopilación de relatos')
            self.assertEqual(db.execute('SELECT role FROM pilot_candidates').fetchone()[0],'index')
            self.assertEqual(db.execute('SELECT count(*) FROM processed_pages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(later,)).fetchone()[0],'pending')
            public_crawl.seed(db,first,'test','plain link')
            self.assertEqual(db.execute('SELECT role FROM pilot_candidates').fetchone()[0],'index')
            db.close()

    def test_redirect_cannot_bypass_candidate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = collect.connect(root)
            public_crawl.setup(db)
            db.executemany('INSERT INTO public_config VALUES(?,?)', [('novel_pilot','true'),('candidate_limit','1')])
            first = collect.canonical('showthread.php?t=9000000')
            public_crawl.seed(db,first,'test')
            target = collect.canonical('showthread.php?t=9000001&page=99')
            collect.enqueue(db,[target])
            public_crawl.seed(db,target,first)
            pilot_policy.apply(db,set())
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(target,)).fetchone()[0],'excluded_limit')
            db.close()
