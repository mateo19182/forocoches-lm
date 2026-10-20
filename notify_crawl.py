"""Send one Telegram notification when the currently armed crawl stops."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(os.environ.get('FC_CRAWL_DATA', Path(__file__).resolve().parent / 'data/public-2026-10-16'))
TIMER = 'forocoches-public-notify.timer'


def message(status):
    counts = f"{status.get('posts', 0):,} posts, {status.get('long_posts', 0):,} long-post candidates"
    if status.get('state') == 'queue_exhausted':
        return f'forocoches-lm: crawl finished: {counts}. No action needed.'
    return (f"forocoches-lm: crawl stopped early ({status.get('state', 'unknown')}): "
            f"{counts}, {status.get('pages', {}).get('pending', 0):,} pages pending. "
            'Check crawl status before resuming.')


def main():
    with (ROOT/'telegram-notify.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sent = ROOT/'telegram-crawl-completion.sent.json'
        if sent.exists():
            return
        result = subprocess.run(['systemctl','--user','show','forocoches-public.service','--property=ActiveState','--value'],capture_output=True,text=True,check=True)
        state = result.stdout.strip()
        if state not in {'inactive','failed'}:
            return
        status = json.loads((ROOT/'status.json').read_text())
        if status.get('state') == 'running':
            status['state'] = 'worker exited unexpectedly'
        text = message(status)
        result = subprocess.run([str(Path.home() / '.local/bin/hermes'),'send','-q','--to','telegram',text],capture_output=True,timeout=90)
        if result.returncode:
            raise SystemExit('Telegram delivery failed; timer will retry.')
        sent.write_text(json.dumps(dict(sent_at=time.time(),message=text),indent=2))
        subprocess.run(['systemctl','--user','disable','--now',TIMER],check=True,capture_output=True)


if __name__ == '__main__':
    main()
