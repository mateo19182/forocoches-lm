"""Read-only completion report for the public crawl."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from fcdata.parse import parse_post
from fcdata.build import core_text


def report(root):
    db=sqlite3.connect(f'file:{root}/crawl.sqlite?mode=ro',uri=True)
    db.execute('BEGIN')
    result=dict(created_at=time.time(),pages=dict(db.execute('SELECT state,count(*) FROM pages GROUP BY state')),
                threads=db.execute('SELECT count(DISTINCT thread_id) FROM posts').fetchone()[0],
                posts=db.execute('SELECT count(*) FROM posts').fetchone()[0],
                registered_fetches=db.execute('SELECT count(*) FROM fetch_events').fetchone()[0],
                response_body_bytes=db.execute('SELECT coalesce(sum(body_bytes),0) FROM fetch_events').fetchone()[0],
                http_statuses={str(k):v for k,v in db.execute('SELECT status,count(*) FROM fetch_events GROUP BY status')},
                quality=dict(raw_long_posts=0,own_long_posts=0,own_long_openers=0,own_long_replies=0,own_4000_plus=0,parse_errors=0))
    hashes=set()
    for (payload,) in db.execute("SELECT payload FROM posts WHERE length(json_extract(payload,'$.text'))>=2000"):
        p=json.loads(payload);result['quality']['raw_long_posts']+=1
        try:own=core_text(parse_post(p['html']).text)
        except Exception:result['quality']['parse_errors']+=1;continue
        if len(own)>=2000:
            result['quality']['own_long_posts']+=1
            result['quality']['own_long_openers' if p.get('position')==1 else 'own_long_replies']+=1
            hashes.add(hashlib.sha256(own.encode('utf-8',errors='replace')).hexdigest())
            if len(own)>=4000:result['quality']['own_4000_plus']+=1
    result['quality']['distinct_exact_own_long_texts']=len(hashes)
    db.close()
    try:result['worker_status']=json.loads((root/'status.json').read_text())
    except (OSError,ValueError):result['worker_status']=None
    temp=root/'completion-report.tmp';temp.write_text(json.dumps(result,indent=2));temp.replace(root/'completion-report.json')
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data',type=Path,default=Path('data/public-2026-10-16'))
    args=ap.parse_args();print(json.dumps(report(args.data),indent=2))

if __name__=='__main__':main()
