import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import collect

HTML = b'''<title>Thread</title><div class="postbit_wrapper"><span class="postdate">Today</span>
<a href="member.php?u=4"><img src="avatar.png"></a>
<a href="member.php?u=4">Author</a>
<a onclick="copyToClipboard()">#1</a><div id="post_message_123">Hello<br>world
<a href="showthread.php?p=12#post12">quote</a></div></div>
<a href="showthread.php?t=10&page=2">Next</a>
<a href="showthread.php?t=999">Other thread</a>
<a href="showthread.php?t=10&page=100">Last</a>'''
URL = collect.canonical('showthread.php?t=10')

class CollectorTests(unittest.TestCase):
    def test_parser_and_host_boundary(self):
        parsed = collect.parse(HTML, URL)
        self.assertEqual(parsed['posts'][0]['id'], 123)
        self.assertEqual(parsed['posts'][0]['author'], 'Author')
        self.assertEqual(parsed['posts'][0]['author_id'], 4)
        self.assertEqual(parsed['posts'][0]['position'], 1)
        self.assertEqual(parsed['posts'][0]['quote_post_ids'], [12])
        self.assertEqual(parsed['posts'][0]['posted_at_raw'], 'Today')
        self.assertIn(collect.canonical('showthread.php?t=10&page=2'), parsed['links'])
        self.assertIsNone(collect.canonical('https://example.com/foro/showthread.php?t=10'))
        self.assertIsNone(collect.canonical('showthread.php?t=-1'))
        self.assertNotIn(collect.canonical('showthread.php?t=999'), parsed['links'])

    def test_spanish_utf8(self):
        html = HTML.replace(b'Hello', 'pensión, año, ¿qué?'.encode('utf-8'))
        parsed = collect.parse(html, URL)
        self.assertIn('pensión, año, ¿qué?', parsed['posts'][0]['text'])

    @patch('collect.time.sleep')
    @patch('collect.requests.Session.get')
    def test_resume_and_global_block(self, get, sleep):
        class Response:
            headers = {}
            content = HTML
            status_code = 200
        get.return_value = Response()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = collect.connect(root)
            with db:
                collect.enqueue(db, [URL, URL])
            collect.run(db, root, 1, 5, max_thread_pages=2)
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM pages').fetchone()[0], 2)
            with db:
                db.execute("UPDATE pages SET state='pending'")
            collect.run(db, root, 1, 5, max_thread_pages=2)
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM pages').fetchone()[0], 2)
            get.return_value.status_code = 429
            collect.run(db, root, 1, 5, max_thread_pages=2)
            with self.assertRaises(SystemExit):
                collect.run(db, root, 1, 5, max_thread_pages=2)
            self.assertEqual(len(list(root.glob('raw/*/*.gz'))), 1)
            db.close()

if __name__ == '__main__':
    unittest.main()
