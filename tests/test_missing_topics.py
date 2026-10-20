import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import collect

class MissingTopicTests(unittest.TestCase):
    def test_signed_in_thread_permission_notice_is_restricted(self):
        url=collect.canonical('showthread.php?t=121178')
        notice='''<main>Información No tienes permisos para acceder a esta página.
            Esto podria deberse a una de estas razones:
            Falta de validación de tu cuenta: Si estás intentando acceder a un tema especial.</main>'''
        logout='<a href="login.php?do=logout">Logout</a>'
        cookies=Path('session.cookies')
        cases=[(notice+logout,200,{},cookies,'restricted'),
               (notice,200,{},cookies,'blocked'),
               (notice+logout,200,{},None,'blocked'),
               (notice+logout,403,{},cookies,'blocked'),
               (notice+logout,429,{},cookies,'blocked'),
               (notice+logout,200,{'cf-mitigated':'challenge'},cookies,'blocked'),
               (notice+logout+'<script src="/cf-chl-challenge.js"></script>',200,{},cookies,'blocked'),
               (notice+logout+'<div>captcha</div>',200,{},cookies,'blocked'),
               (notice+logout+'No has iniciado sesión o no tienes permiso para acceder a esta página.',200,{},cookies,'blocked')]
        for html,status,headers,cookie_file,expected in cases:
            with self.subTest(status=status,headers=headers,expected=expected,html=html):
                with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
                    root=Path(directory)
                    with collect.connect(root) as db:
                        collect.enqueue(db,[url])
                        response=SimpleNamespace(status_code=status,headers=headers,content=html.encode())
                        self.assertEqual(collect.save_response(db,root,url,0,response,cookies=cookie_file),expected)

    def test_authenticated_empty_thread_requires_complete_matching_structure(self):
        url=collect.canonical('showthread.php?t=5371')
        html=b'<a href="login.php?do=logout">Logout</a><input id="qr_threadid" value="5371"><div id="posts"><div id="lastpost"></div></div>'
        cases=[(html,{},Path('session.cookies'),'empty'),
               (html.replace(b'5371',b'999'),{},Path('session.cookies'),'blocked'),
               (html.replace(b'login.php?do=logout',b'login.php'),{},Path('session.cookies'),'blocked'),
               (html.replace(b'<div id="lastpost"></div>',b'<div id="lastpost"></div><table>Unparsed post</table>'),{},Path('session.cookies'),'blocked'),
               (html,{'cf-mitigated':'challenge'},Path('session.cookies'),'blocked'),
               (html,{},None,'blocked')]
        for content,headers,cookies,expected in cases:
            with self.subTest(expected=expected,content=content,headers=headers,cookies=bool(cookies)):
                with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
                    root=Path(directory);db=collect.connect(root)
                    with db:collect.enqueue(db,[url])
                    response=SimpleNamespace(status_code=200,headers=headers,content=content)
                    actual=collect.save_response(db,root,url,0,response,cookies=cookies)
                    self.assertEqual(actual,expected)
                    db.close()

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_no_topic_specified_notice_is_missing(self,session,sleep):
        session.return_value.get.return_value=SimpleNamespace(status_code=200,headers={},content='<main>Información Ningún Tema especificado.</main>'.encode())
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical('showthread.php?t=7931183')])
            collect.run(db,root,1,5)
            self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'missing')
            db.close()

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_explicit_public_login_notice_is_restricted(self,session,sleep):
        content='<main>Información No has iniciado sesión o no tienes permiso para acceder a esta página.</main>'.encode()
        session.return_value.get.return_value=SimpleNamespace(status_code=200,headers={},content=content)
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical('showthread.php?t=7132027')])
            collect.run(db,root,1,5)
            self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'restricted')
            db.close()

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_explicit_missing_notice_is_not_global_block(self,session,sleep):
        response=SimpleNamespace(status_code=200,headers={},content='<main>Información Tema especificado inválido.</main>'.encode())
        session.return_value.get.return_value=response
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical('showthread.php?t=1983792')])
            collect.run(db,root,1,5)
            self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'missing')
            db.close()

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_unrecognized_empty_page_still_pauses(self,session,sleep):
        session.return_value.get.return_value=SimpleNamespace(status_code=200,headers={},content=b'<main>Unexpected page</main>')
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);db=collect.connect(root)
            with db:collect.enqueue(db,[collect.canonical('showthread.php?t=1983792')])
            collect.run(db,root,1,5)
            self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'blocked')
            db.close()

if __name__=='__main__':unittest.main()
