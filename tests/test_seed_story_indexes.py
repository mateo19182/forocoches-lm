from pathlib import Path
import sqlite3
import tempfile
import unittest
from seed_story_indexes import discover,is_index


class SavedIndexesTest(unittest.TestCase):
    def test_only_outside_links_from_story_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'archive.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE threads(id INTEGER PRIMARY KEY,title TEXT,body TEXT)')
                db.execute('INSERT INTO threads VALUES(?,?,?)',(7100000,'Recopilación de historias',
                    '<a href="showthread.php?t=8000000&page=4">story</a>'
                    '<a href="showthread.php?t=8000000">story</a>'
                    '<a href="showthread.php?t=7999999">old</a>'))
            before=path.read_bytes()
            rows=discover(path)['candidates']
            self.assertEqual([r['thread_id'] for r in rows],[8000000])
            self.assertEqual(len(rows[0]['sources']),1)
            self.assertEqual(before,path.read_bytes())
            self.assertFalse(is_index('Recopilación de ofertas'))
