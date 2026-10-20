import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import collect
from test_collect import HTML,URL

class MalformedLinkTests(unittest.TestCase):
    def test_bad_external_link_does_not_discard_posts(self):
        html=HTML+b'<a href="https://[broken">broken link</a>'
        parsed=collect.parse(html,URL)
        self.assertEqual(len(parsed['posts']),1)
        self.assertIsNone(collect.canonical('https://[broken'))

    def test_bad_redirect_is_recorded_as_block_instead_of_crashing(self):
        response=SimpleNamespace(status_code=302,content=b'',headers={'Location':'https://[broken'})
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[URL])
            collect.save_response(db,root,URL,0,response)
            self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'blocked')
            db.close()

if __name__=='__main__':unittest.main()
