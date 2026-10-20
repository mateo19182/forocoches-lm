import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import collect
from test_collect import HTML,URL

class UnicodePostTests(unittest.TestCase):
    def test_numeric_surrogate_entities_do_not_crash_sqlite(self):
        response=SimpleNamespace(status_code=200,headers={},content=HTML.replace(b'Hello',b'Hello &#55357;&#56834; &#55357;'))
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[URL])
            collect.save_response(db,root,URL,0,response,1)
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0],1)
            import json
            post=json.loads(db.execute('SELECT payload FROM posts').fetchone()[0])
            self.assertIn('\U0001f602',post['text'])
            self.assertNotIn('\ud83d',post['text'])
            db.close()

if __name__=='__main__':unittest.main()
