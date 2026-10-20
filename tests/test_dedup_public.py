import json
from pathlib import Path
import tempfile
import unittest
import collect
from dedup_public import export, fingerprint


class DedupTests(unittest.TestCase):
    def test_normalization_preserves_accents_and_content(self):
        self.assertEqual(fingerprint('Ａyer\n ME pasó'), fingerprint('ayer me paso\u0301'))
        self.assertNotEqual(fingerprint('si'),fingerprint('sí'))

    def test_duplicates_keep_provenance_and_raw_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = collect.connect(root)
            own = 'Ayer me ocurrió algo extraño. ' * 100
            for pid, html in [(1,own),(2,own.upper()),(3,'Una historia distinta. '*120)]:
                payload = dict(id=pid,html='<p>'+html+'</p>',text=html)
                db.execute('INSERT INTO posts VALUES(?,?,?,?,?)',(pid,pid,'https://example.test/'+str(pid),json.dumps(payload),1))
            db.commit()
            stats = export(root)
            self.assertEqual(stats['unique'],2)
            self.assertEqual(stats['duplicates'],1)
            duplicate = json.loads((root/'duplicates.jsonl').read_text())
            self.assertEqual((duplicate['post_id'],duplicate['duplicate_of']),(2,1))
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0],3)
            db.close()


if __name__ == '__main__':unittest.main()
