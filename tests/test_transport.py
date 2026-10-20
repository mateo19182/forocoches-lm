import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import authenticate
import collect
import transport
from test_collect import URL


class TransportTests(unittest.TestCase):
    def test_proxy_config_and_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'proxy.json'
            p.write_text(json.dumps({'http':'http://user:secret@example.test:80','https':'http://user:secret@example.test:80'}))
            p.chmod(0o644)
            with self.assertRaises(ValueError):transport.session(proxy_file=p)
            p.chmod(0o600)
            client=transport.session(proxy_file=p)
            self.assertFalse(client.trust_env)
            self.assertEqual(client.proxies['https'],'http://user:secret@example.test:80')
            client.close()

    def test_password_hash_matches_legacy_js(self):
        expected=hashlib.md5(b'example').hexdigest()
        self.assertEqual(authenticate.hashes(' example '),(expected,expected))
        self.assertEqual(authenticate.hashes('á')[0],hashlib.md5(bytes([225])).hexdigest())

    @patch('collect.time.sleep')
    @patch('collect.transport.session')
    def test_authentication_loss_pauses_instead_of_skipping(self, session, sleep):
        response=session.return_value.get.return_value
        response.status_code=302
        response.content=b''
        response.headers={'Location':'https://forocoches.com/foro/misc.php?do=page&template=Info&tid=10'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            db=collect.connect(root)
            with db:collect.enqueue(db,[URL])
            collect.run(db,root,1,5,cookies=Path('private.cookies'))
            self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'blocked')
            db.close()

    @patch('authenticate.transport.session')
    def test_existing_attempt_prevents_another_login(self, session):
        with tempfile.TemporaryDirectory() as directory:
            cookie_file=Path(directory)/'session.cookies'
            Path(str(cookie_file)+'.attempt.json').write_text('{}')
            with self.assertRaises(SystemExit):
                authenticate.login(Path('unused'),cookie_file,None)
            session.assert_not_called()


if __name__=='__main__':unittest.main()
