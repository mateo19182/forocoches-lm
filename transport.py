"""Private session configuration shared by login and collection."""
from contextlib import contextmanager
import fcntl
import http.cookiejar
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import requests

HOST_LOCK = Path(os.environ.get('FC_HOST_LOCK',Path.home()/'.cache/forocoches-lm/collector.lock'))


@contextmanager
def host_lock():
    HOST_LOCK.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(HOST_LOCK,os.O_WRONLY|os.O_CREAT,0o600)
    with os.fdopen(fd,'w') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another collector holds the host request lock') from None
        yield


def private_file(path):
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError('Secret files must have mode 0600 or stricter')
    return path


def session(cookies=None, proxy_file=None, posts_per_page=None):
    client = requests.Session()
    client.trust_env = False
    client.headers['User-Agent'] = 'forocoches-lm-archive/0.1'
    if posts_per_page is not None:
        if posts_per_page not in (20,30,40,50,100):
            raise ValueError('Unsupported posts-per-page setting')
        client.params['pp'] = posts_per_page
    if proxy_file:
        proxies = json.loads(private_file(proxy_file).read_text())
        if set(proxies) != {'http', 'https'} or any(urlparse(u).scheme not in ('http','https','socks5h') for u in proxies.values()):
            raise ValueError('Proxy config must specify http and https routes')
        client.proxies.update(proxies)
    if cookies:
        jar = http.cookiejar.MozillaCookieJar(str(private_file(cookies)))
        jar.load(ignore_discard=True, ignore_expires=False)
        client.cookies = jar
    return client
