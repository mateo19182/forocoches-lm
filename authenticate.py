"""One normal login attempt with a dedicated account file. Never rotates accounts."""
import argparse
import fcntl
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests
import transport

LOGIN = 'https://forocoches.com/foro/misc.php?do=page&template=ident'


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, indent=2)


def hashes(password):
    # Match the site's vbulletin_md5.js, including 8-bit JS string hashing.
    units = password.strip(' ').encode('utf-16-le', errors='surrogatepass')
    units = [int.from_bytes(units[i:i+2], 'little') for i in range(0,len(units),2)]
    entities = ''.join(chr(c) if c <= 255 else f'&#{c};' for c in units)
    return (hashlib.md5(entities.encode('latin-1')).hexdigest(),
            hashlib.md5(bytes(c & 255 for c in units)).hexdigest())


def login(account_file, cookie_file, proxy_file):
    state_path = Path(str(cookie_file) + '.attempt.json')
    if state_path.exists():
        raise SystemExit('An attempt is already recorded. Inspect it before any further login.')
    account = json.loads(transport.private_file(account_file).read_text())
    client = transport.session(proxy_file=proxy_file)
    state = dict(started_at=time.time(), route='configured_proxy' if proxy_file else 'direct', attempts=0)
    write_private(state_path, state)
    try:
        page = client.get(LOGIN, timeout=(15,30), allow_redirects=False)
        if page.status_code != 200:
            state.update(result='login_page_unavailable',status=page.status_code,
                         server=page.headers.get('Server'),
                         challenge_header_present=bool(page.headers.get('cf-mitigated')))
            return state
        soup = BeautifulSoup(page.content.decode('utf-8',errors='replace'),'html.parser')
        form = soup.select_one('form[action="login.php"]')
        if not form or soup.select('[class*=captcha], [id*=captcha]'):
            state.update(result='unexpected_login_form_or_challenge')
            return state
        data = {i['name']:i.get('value','') for i in form.select('input[type=hidden][name]')}
        data.update(vb_login_username=account['username'], vb_login_password='', cookieuser='1')
        data['vb_login_md5password'],data['vb_login_md5password_utf'] = hashes(account['password'])
        target = urljoin(LOGIN,form['action'])
        if target != 'https://forocoches.com/foro/login.php':
            state.update(result='unexpected_login_target')
            return state
        time.sleep(5)
        state['attempts'] = 1
        write_private(state_path,state)
        response = client.post(target,data=data,timeout=(15,30),allow_redirects=False)
        state['status'] = response.status_code
        # The cookie ID is checked in memory; never print cookie values or account identity.
        authenticated = any(c.name.endswith('userid') and c.value.isdigit() and int(c.value)>0 for c in client.cookies)
        if authenticated and response.status_code in (200,301,302,303):
            fd = os.open(cookie_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.close(fd)
            jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
            for cookie in client.cookies:
                jar.set_cookie(cookie)
            jar.save(ignore_discard=True,ignore_expires=True)
            state['result'] = 'session_saved_pending_protected_page_check'
        else:
            text = BeautifulSoup(response.content.decode('utf-8',errors='replace'),'html.parser').get_text(' ',strip=True).lower()
            state['result'] = 'login_not_confirmed'
            state['signals'] = {key: any(term in text for term in terms) for key,terms in {
                'invalid_credentials':['contraseña incorrect','datos incorrect','nombre de usuario o contraseña','usuario o contraseña','invalid username'],
                'account_restricted':['baneado','expulsado','cuenta suspendida'],
                'challenge':['captcha','verify you are human','just a moment'],
                'rate_limit':['demasiados intentos','too many','excedido el número'],
            }.items()}
            private = Path(str(cookie_file)+'.response.html')
            private.write_bytes(response.content);private.chmod(0o600)
        return state
    except requests.RequestException as error:
        state.update(result='network_error', error_type=type(error).__name__)
        return state
    finally:
        state['finished_at'] = time.time()
        write_private(state_path,state)
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-file',type=Path,required=True)
    parser.add_argument('--cookies',type=Path,required=True)
    parser.add_argument('--proxy-file',type=Path)
    args=parser.parse_args()
    lock_path=Path(str(args.cookies)+'.lock')
    fd=os.open(lock_path,os.O_WRONLY|os.O_CREAT,0o600)
    with transport.host_lock(), os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=login(args.account_file,args.cookies,args.proxy_file)
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
