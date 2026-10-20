from pathlib import Path
import tempfile
import unittest

import collect
import id_scan
from parallel_fetch import Fetcher
import pilot_policy
import public_crawl


class IdScanTests(unittest.TestCase):
    def test_full_range_can_include_historical_archive_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                db.executemany('INSERT INTO public_config VALUES(?,?)',[('novel_pilot','true'),('exclude_archive_ids','false')])
                id_scan.configure(db,6999999,7000001)
                id_scan.fill(db,public_crawl.seed,pilot_policy.config(db),window=3)
                self.assertEqual([r[0] for r in db.execute('SELECT scan_order FROM pages ORDER BY scan_order')],[6999999,7000000,7000001])
                self.assertEqual(id_scan.summary(db)['skipped_archive'],0)

    def test_range_can_extend_without_rewinding_or_reversing(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db);id_scan.configure(db,1,10)
                id_scan.fill(db,public_crawl.seed,{},window=2)
                id_scan.configure(db,1,20)
                self.assertEqual(id_scan.summary(db)['next_to_queue'],3)
                self.assertEqual(id_scan.summary(db)['end_id'],20)
                with self.assertRaises(ValueError):id_scan.configure(db,1,5)

    def test_first_page_scan_uses_both_slots_for_numeric_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db);id_scan.configure(db,1,3)
                id_scan.fill(db,public_crawl.seed,{})
                later=collect.canonical('showthread.php?t=9000001&page=2')
                story=collect.canonical('showthread.php?t=9000002')
                collect.enqueue(db,[later,story])
                db.execute('UPDATE pages SET priority=1000 WHERE url IN (?,?)',(later,story))
                self.assertEqual([u for u,_ in Fetcher.jobs(db,2,True,True)],
                                 [collect.canonical('showthread.php?t=1'),collect.canonical('showthread.php?t=2')])

    def test_first_page_mode_persists_and_holds_existing_continuations(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                first=collect.canonical('showthread.php?t=9000001')
                later=collect.canonical('showthread.php?t=9000001&page=2')
                collect.enqueue(db,[first,later])
                public_crawl.configure_pagination(db,all_pages=True)
                public_crawl.configure_pagination(db,first_pages=True)
                public_crawl.configure_pagination(db)
                self.assertFalse(pilot_policy.config(db)['all_thread_pages'])
                self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(later,)).fetchone()[0],'deferred')
                self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(first,)).fetchone()[0],'pending')

    def test_cursor_resumes_without_refetching_known_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                id_scan.configure(db,1,5)
                known=collect.canonical('showthread.php?t=2')
                collect.enqueue(db,[known]);db.execute("UPDATE pages SET state='missing' WHERE url=?",(known,))
                id_scan.fill(db,public_crawl.seed,{},window=2)
                self.assertEqual(id_scan.summary(db)['next_to_queue'],4)
                self.assertEqual(id_scan.summary(db)['skipped_known'],1)
                db.execute("UPDATE pages SET state='done' WHERE scan_order=1")
                id_scan.configure(db,1,5)
                id_scan.fill(db,public_crawl.seed,{},window=2)
                orders=[r[0] for r in db.execute('SELECT scan_order FROM pages WHERE scan_order IS NOT NULL ORDER BY scan_order')]
                self.assertEqual(orders,[1,3,4])
                self.assertEqual(id_scan.summary(db)['next_to_queue'],5)
                with self.assertRaises(ValueError):id_scan.configure(db,2,5)

    def test_scanner_reserves_a_slot_despite_high_priority_story_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db);id_scan.configure(db,1,3)
                id_scan.fill(db,public_crawl.seed,{})
                story=collect.canonical('showthread.php?t=9000001')
                later=collect.canonical('showthread.php?t=9000002&page=2')
                collect.enqueue(db,[story,later])
                db.execute('UPDATE pages SET priority=1000 WHERE url IN (?,?)',(story,later))
                self.assertEqual([u for u,_ in Fetcher.jobs(db,2,True)],
                                 [collect.canonical('showthread.php?t=1'),later])

    def test_archive_block_is_skipped_and_admission_failure_keeps_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                db.execute("INSERT INTO public_config VALUES('novel_pilot','true')")
                id_scan.configure(db,6999999,8000001)
                id_scan.fill(db,public_crawl.seed,{'novel_pilot':True},window=3)
                self.assertEqual(id_scan.summary(db)['skipped_archive'],1000000)
                self.assertEqual([r[0] for r in db.execute('SELECT scan_order FROM pages ORDER BY scan_order')],
                                 [6999999,8000000,8000001])
                db.execute('DELETE FROM id_scan');id_scan.configure(db,10,20)
                id_scan.fill(db,lambda *args:False,{},window=4)
                self.assertEqual(id_scan.summary(db)['next_to_queue'],10)

    def test_all_page_mode_does_not_filter_short_threads(self):
        with tempfile.TemporaryDirectory() as directory:
            with collect.connect(Path(directory)) as db:
                public_crawl.setup(db)
                db.executemany('INSERT INTO public_config VALUES(?,?)',[('novel_pilot','true'),('all_thread_pages','true')])
                first=collect.canonical('showthread.php?t=9000001')
                later=collect.canonical('showthread.php?t=9000001&page=100')
                public_crawl.seed(db,first,'scan');collect.enqueue(db,[later])
                pilot_policy.apply(db,set())
                self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(later,)).fetchone()[0],'pending')


if __name__=='__main__':unittest.main()
