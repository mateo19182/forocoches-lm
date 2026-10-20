import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import collect
import sample
from test_collect import HTML, URL


class SampleTests(unittest.TestCase):
    @patch('collect.time.sleep')
    @patch('collect.requests.Session.get')
    def test_partial_thread_export_retains_op(self, get, sleep):
        get.return_value.status_code = 200
        get.return_value.headers = {}
        get.return_value.content = HTML
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root/'seeds.json').write_text(json.dumps({'selected':[{'url':URL}]}))
            db = collect.connect(root)
            with db:
                collect.enqueue(db,[URL])
            collect.run(db,root,1,5,max_thread_pages=1)
            sample.export(root)
            doc = json.loads((root/'threads.jsonl').read_text())
            self.assertFalse(doc['complete_observed_pagination'])
            self.assertEqual(doc['last_page_seen'],100)
            self.assertTrue(doc['posts'][0]['is_op'])
            self.assertEqual(doc['posts'][0]['author'],'Author')
            db.close()

    @patch('collect.time.sleep')
    @patch('collect.requests.Session.get')
    def test_registered_only_redirect_does_not_get_followed(self, get, sleep):
        get.return_value.status_code = 302
        get.return_value.headers = {'Location':'https://forocoches.com/foro/misc.php?do=page&template=Info&tid=10'}
        get.return_value.content = b''
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            db = collect.connect(root)
            with db:
                collect.enqueue(db,[URL])
            collect.run(db,root,1,5)
            self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'restricted')
            self.assertEqual(get.call_count,1)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            db.close()


if __name__ == '__main__':
    unittest.main()
