import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import collect
from test_collect import HTML


class RedirectTests(unittest.TestCase):
    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_same_site_thread_move_is_queued_and_collected(self,session,sleep):
        source=collect.canonical('showthread.php?t=7305737')
        target=collect.canonical('showthread.php?t=7305752')
        session.return_value.get.side_effect=[
            SimpleNamespace(status_code=302,content=b'',headers={'Location':'https://forocoches.com/foro/showthread.php?t=7305752'}),
            SimpleNamespace(status_code=200,content=HTML,headers={})]
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[source])
            collect.run(db,root,2,5,max_thread_pages=1)
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(source,)).fetchone()[0],'redirected')
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(target,)).fetchone()[0],'done')
            db.close()

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_private_thread_does_not_stop_other_public_threads(self, session, sleep):
        private=collect.canonical('showthread.php?t=1749074')
        public=collect.canonical('showthread.php?t=2000000')
        session.return_value.get.side_effect=[
            SimpleNamespace(status_code=302,content=b'',headers={'Location':'https://forocoches.com/foro/misc.php?do=page&template=InfoPRV&tid=1749074'}),
            SimpleNamespace(status_code=200,content=HTML,headers={})]
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[private,public])
            collect.run(db,root,2,5)
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(private,)).fetchone()[0],'restricted')
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(public,)).fetchone()[0],'done')
            self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0],1)
            db.close()

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_authenticated_private_thread_is_skipped_without_pausing(self, session, sleep):
        private=collect.canonical('showthread.php?t=5346')
        public=collect.canonical('showthread.php?t=5347')
        session.return_value.get.side_effect=[
            SimpleNamespace(status_code=302,content=b'',headers={'Location':'https://forocoches.com/foro/misc.php?do=page&template=InfoPRV&tid=5346'}),
            SimpleNamespace(status_code=200,content=HTML,headers={})]
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[private,public])
            collect.run(db,root,2,5,cookies=Path('authenticated.cookies'))
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(private,)).fetchone()[0],'restricted')
            self.assertEqual(db.execute('SELECT state FROM pages WHERE url=?',(public,)).fetchone()[0],'done')
            db.close()

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_other_redirects_and_authentication_loss_still_pause(self, session, sleep):
        source=collect.canonical('showthread.php?t=1749074')
        known='https://forocoches.com/foro/misc.php?do=page&template=InfoPRV&tid=1749074'
        cases=[(known.replace('InfoPRV','Info'),Path('authenticated.cookies')),
               (known.replace('1749074','999'),None),
               (known.replace('forocoches.com','example.test'),None),
               (known.replace('InfoPRV','Unknown'),None)]
        for location,cookies in cases:
            with self.subTest(location=location,cookies=bool(cookies)):
                session.return_value.get.side_effect=None
                session.return_value.get.return_value=SimpleNamespace(status_code=302,content=b'',headers={'Location':location})
                with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
                    root=Path(directory);db=collect.connect(root)
                    with db:collect.enqueue(db,[source])
                    collect.run(db,root,1,5,cookies=cookies)
                    state,target=db.execute('SELECT state,redirect_target FROM pages').fetchone()
                    self.assertEqual(state,'blocked')
                    self.assertEqual(target,location)
                    db.close()


if __name__=='__main__':unittest.main()
