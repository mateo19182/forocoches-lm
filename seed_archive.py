"""Queue fresh public first pages using long archive openers as a URL index."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import sqlite3
import time
from fcdata.parse import parse_post
from fcdata.build import core_text
import collect
import public_crawl


def select_candidates(reader, known, limit, min_chars, buckets=10):
    lower,upper=reader.execute('SELECT min(id),max(id) FROM threads').fetchone()
    selected=[];stats=[]
    width=(upper-lower+1+buckets-1)//buckets
    for bucket in range(buckets):
        start=lower+bucket*width;end=min(upper,start+width-1)
        quota=limit//buckets+(bucket<limit%buckets)
        found=examined=0
        if not quota:continue
        for tid,title,body in reader.execute('SELECT id,title,body FROM threads WHERE id BETWEEN ? AND ? AND length(body)>=? ORDER BY id',(start,end,min_chars)):
            examined+=1
            url=collect.canonical(f'showthread.php?t={tid}')
            if url in known:continue
            own=core_text(parse_post(body).text)
            if len(own)>=min_chars and re.search(r'\b(me|mi|ayer|recuerdo|estaba|fui|trabajaba|pas[oó])\b',own,re.I):
                selected.append(collect.clean_unicode(dict(url=url,title=title,archive_own_chars=len(own),archive_thread_id=tid,bucket=bucket)))
                known.add(url);found+=1
            if found>=quota or examined>=max(3000,quota*20):break
        stats.append(dict(min_id=start,max_id=end,examined=examined,selected=found))
        print(json.dumps(stats[-1]),flush=True)
    return selected,stats


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive',type=Path,default=Path('data/fc_dataset_7-8.sqlite'))
    ap.add_argument('--data',type=Path,default=Path('data/public-2026-10-16'))
    ap.add_argument('--limit',type=int,default=2000)
    ap.add_argument('--min-chars',type=int,default=4000)
    args=ap.parse_args()
    if args.limit<1 or args.min_chars<1:ap.error('Limits must be positive')
    root=args.data;root.mkdir(parents=True,exist_ok=True)
    with (root/'collector.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=collect.connect(root);public_crawl.setup(db)
        capacity=50000-db.execute('SELECT count(*) FROM pages').fetchone()[0]
        if capacity<args.limit:raise SystemExit('Requested batch exceeds remaining queue capacity')
        reader=sqlite3.connect(f'file:{args.archive}?mode=ro',uri=True)
        known={u for u, in db.execute('SELECT url FROM pages')}
        candidates,stats=select_candidates(reader,known,args.limit,args.min_chars)
        reader.close()
        with db:
            for item in candidates:
                public_crawl.seed(db,item['url'],f'archive:{args.archive.name}',item['title'])
                db.execute('UPDATE pages SET priority=max(priority,?) WHERE url=?',(170+min(item['archive_own_chars'],30000)/30000,item['url']))
        stamp=str(time.time_ns())
        report=dict(created_at=time.time(),archive=str(args.archive),minimum_own_chars=args.min_chars,
                    selection='Long own text and first-person cue; unverified narrative candidates, spread over 10 ID buckets',
                    added=len(candidates),buckets=stats,candidates=candidates)
        (root/f'archive-enrichment-{stamp}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(dict(added=len(candidates),pending=db.execute("SELECT count(*) FROM pages WHERE state='pending'").fetchone()[0])),flush=True)
        db.close()


if __name__=='__main__':main()
