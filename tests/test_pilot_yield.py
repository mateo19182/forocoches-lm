import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pilot_yield import atomic_json, existing_ids, report


def url(tid, page=1):
    return f'https://forocoches.com/foro/showthread.php?t={tid}&page={page}'


class PilotYieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'archive.sqlite'
        self.previous = self.root / 'previous.sqlite'
        with sqlite3.connect(self.archive) as db:
            db.executescript('CREATE TABLE threads(id INTEGER PRIMARY KEY, post_id INTEGER, body TEXT);'
                             'CREATE TABLE comments(post_id INTEGER PRIMARY KEY, thread_id INTEGER, body TEXT);')
            # Matches outside the pilot thread must still exclude these IDs.
            db.execute('INSERT INTO threads VALUES(999,1,?)', ('old',))
            db.execute('INSERT INTO comments VALUES(2,999,?)', ('old',))
        with sqlite3.connect(self.previous) as db:
            db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
            db.executemany('INSERT INTO posts VALUES(?)', [(2,), (3,)])
        self.live = sqlite3.connect(self.root / 'crawl.sqlite')
        self.addCleanup(self.live.close)
        self.live.executescript('PRAGMA journal_mode=WAL;'
                                'CREATE TABLE posts(id INTEGER PRIMARY KEY, thread_id INTEGER, payload TEXT);'
                                'CREATE TABLE pages(url TEXT PRIMARY KEY, state TEXT);'
                                'CREATE TABLE discoveries(url TEXT, source TEXT);')
        long = 'á' * 2000
        quote = '<div style="margin:20px; margin-top:5px"><td class="alt2">quoted words</td></div>'
        entries = [(1, 10, 'old'), (2, 10, 'old'), (3, 10, 'old'),
                   (4, 10, long), (5, 10, long), (6, 10, '<td>' + quote + 'sí</td>'),
                   (7, 10, '<img src="x.png">'), (8, 20, 'b' * 40)]
        self.live.executemany('INSERT INTO posts VALUES(?,?,?)',
                              [(pid, tid, json.dumps({'html': html})) for pid, tid, html in entries])
        self.live.execute('INSERT INTO posts VALUES(9,10,?)', ('invalid JSON',))
        self.live.executemany('INSERT INTO pages VALUES(?,?)',
                              [(url(10), 'done'), (url(10, 2), 'done'),
                               (url(10, 3), 'pending'), (url(20), 'restricted'),
                               (url(30), 'held_source')])
        self.live.executemany('INSERT INTO discoveries VALUES(?,?)',
                              [(url(10), 'A'), (url(10, 2), 'A'), (url(10), 'B'), (url(20), 'A')])
        self.live.commit()

    def test_snapshot_overlap_cleaning_sources_and_coverage(self):
        before = {p: p.read_bytes() for p in (self.archive, self.previous)}
        result = report(self.root, self.archive, self.previous)
        self.assertEqual(result['overlap'], dict(archive_posts=2, previous_posts=2,
                                                  archive_and_previous_posts=1))
        totals = result['totals']
        self.assertEqual(totals['collected_posts'], 9)
        self.assertEqual(totals['new_id_posts'], 6)
        self.assertEqual(totals['useful_new_posts'], 3)
        self.assertEqual(totals['long_new_posts'], 2)
        self.assertEqual(totals['new_id_parse_errors'], 1)
        self.assertEqual(totals['new_id_own_text_chars'], 4042)
        self.assertEqual(totals['exact_distinct_new_id_own_text_chars'], 2042)
        self.assertEqual(totals['exact_distinct_new_id_own_texts'], 3)
        self.assertEqual(totals['exact_distinct_long_new_id_own_text_chars'], 2000)
        a, b = result['per_source']
        self.assertEqual(a['threads'], 2)
        self.assertEqual(a['pagination']['restricted_pages'], 1)
        self.assertEqual(b['new_id_posts'], 5)
        self.assertEqual(b['pagination']['done_fraction'], 2 / 3)
        self.assertEqual(b['pagination']['later_page_states'], {'done': 1, 'pending': 1})
        self.assertEqual(result['unattributed']['pagination']['states'], {'held_source': 1})
        self.assertIn('do not establish novel text', result['definitions']['novelty_caveat'])
        self.assertEqual(json.loads((self.root / 'pilot-yield.json').read_text()), result)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(self.live.execute('SELECT count(*) FROM posts').fetchone()[0], 9)

    def test_index_batches_and_unindexed_single_scan_never_select_bodies(self):
        with sqlite3.connect(self.archive) as db:
            queries = []
            db.set_trace_callback(queries.append)
            with patch('pilot_yield.BATCH_SIZE', 2):
                found, plan = existing_ids(db, 'comments', 'post_id', {1, 2, 3, 4, 5})
            self.assertEqual(found, {2})
            self.assertEqual(plan['strategy'], 'batched_index_lookup')
            self.assertEqual(len(queries), 3)
            queries.clear()
            found, plan = existing_ids(db, 'threads', 'post_id', {1, 2, 3})
            self.assertEqual(found, {1})
            self.assertEqual(plan['strategy'], 'single_id_only_scan')
            self.assertEqual(queries, ['SELECT post_id FROM threads'])

    def test_missing_baseline_does_not_create_db_or_replace_report(self):
        output = self.root / 'pilot-yield.json'
        output.write_text('previous report')
        missing = self.root / 'missing.sqlite'
        with self.assertRaises(sqlite3.OperationalError):
            report(self.root, missing, self.previous)
        self.assertFalse(missing.exists())
        self.assertEqual(output.read_text(), 'previous report')

    def test_atomic_failure_preserves_report_and_cleans_temp(self):
        output = self.root / 'pilot-yield.json'
        output.write_text('previous report')
        with patch('pilot_yield.os.replace', side_effect=OSError('failure')):
            with self.assertRaises(OSError):
                atomic_json(output, {'new': True})
        self.assertEqual(output.read_text(), 'previous report')
        self.assertEqual(list(self.root.glob('.pilot-yield-*.tmp')), [])

    def test_empty_crawl(self):
        for table in ('posts', 'pages', 'discoveries'):
            self.live.execute(f'DELETE FROM {table}')
        self.live.commit()
        result = report(self.root, self.archive, self.previous)
        self.assertEqual(result['totals']['new_id_posts'], 0)
        self.assertIsNone(result['pagination']['done_fraction'])
        self.assertEqual(result['per_source'], [])


if __name__ == '__main__':
    unittest.main()
