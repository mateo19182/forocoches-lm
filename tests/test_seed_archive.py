import contextlib
import io
import sqlite3
import unittest
import collect
from seed_archive import select_candidates

class ArchiveSeedTests(unittest.TestCase):
    def test_lower_threshold_admits_shorter_html(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE threads(id INTEGER PRIMARY KEY,title TEXT,body TEXT)')
        db.execute('INSERT INTO threads VALUES(1,?,?)',('Story','<p>'+('Ayer me pasó algo. '*150)+'</p>'))
        with contextlib.redirect_stdout(io.StringIO()):
            candidates,_=select_candidates(db,set(),1,2000,buckets=1)
        self.assertEqual(len(candidates),1)
        self.assertLess(candidates[0]['archive_own_chars'],4000)
        db.close()

    def test_selection_spreads_ids_and_excludes_known_urls(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE threads(id INTEGER PRIMARY KEY,title TEXT,body TEXT)')
        for tid in range(1,11):
            db.execute('INSERT INTO threads VALUES(?,?,?)',(tid,'Story '+str(tid),'<p>'+('Ayer me pasó una historia. '*500)+'</p>'))
        known={collect.canonical('showthread.php?t=4')}
        with contextlib.redirect_stdout(io.StringIO()):
            candidates,stats=select_candidates(db,known,10,4000)
        self.assertEqual(len(candidates),9)
        self.assertNotIn(4,[x['archive_thread_id'] for x in candidates])
        self.assertEqual(len({x['url'] for x in candidates}),9)
        self.assertEqual(len(stats),10)
        self.assertTrue(all(x['archive_own_chars']>=4000 for x in candidates))
        self.assertEqual(db.execute('SELECT count(*) FROM threads').fetchone()[0],10)
        db.close()

if __name__=='__main__':unittest.main()
