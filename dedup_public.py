"""Export unique long own-text posts without deleting collected evidence."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import unicodedata

from fcdata.build import core_text
from fcdata.parse import parse_post


def fingerprint(text):
    normalized = ' '.join(unicodedata.normalize('NFKC', text).casefold().split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def export(root, min_chars=2000):
    with (root / 'dedup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect(f'file:{root}/crawl.sqlite?mode=ro', uri=True)
        db.execute('BEGIN')
        seen = {}
        stats = dict(created_at=time.time(),minimum_own_chars=min_chars,
                     scanned=0,unique=0,duplicates=0,below_threshold=0,
                     method='NFKC, casefold and whitespace normalization of quote/media-stripped own text; exact SHA256 match')
        with (root / 'long-posts.unique.tmp').open('w') as output, (root / 'duplicates.tmp').open('w') as duplicates:
            rows = db.execute("SELECT thread_id,page_url,payload,observed_at FROM posts WHERE length(json_extract(payload,'$.text'))>=? ORDER BY id", (min_chars,))
            for tid, url, payload, observed in rows:
                post = json.loads(payload)
                own = core_text(parse_post(post['html']).text)
                stats['scanned'] += 1
                if len(own) < min_chars:
                    stats['below_threshold'] += 1
                    continue
                digest = fingerprint(own)
                if digest in seen:
                    stats['duplicates'] += 1
                    duplicates.write(json.dumps(dict(post_id=post['id'],duplicate_of=seen[digest],text_sha256=digest))+'\n')
                    continue
                seen[digest] = post['id']
                stats['unique'] += 1
                output.write(json.dumps(dict(thread_id=tid,page_url=url,observed_at=observed,**post,own_text=own,text_sha256=digest),ensure_ascii=False)+'\n')
        db.close()
        for name in ('long-posts.unique','duplicates'):
            (root / f'{name}.tmp').replace(root / f'{name}.jsonl')
        (root / 'dedup-report.tmp').write_text(json.dumps(stats,indent=2))
        (root / 'dedup-report.tmp').replace(root / 'dedup-report.json')
        return stats


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data',type=Path,default=Path('data/public-2026-10-16'))
    args = ap.parse_args()
    print(json.dumps(export(args.data)))
