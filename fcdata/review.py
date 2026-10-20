"""Prepare a bounded story-label audit alongside all new/changed openers."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random


def prepare(queue, previous, out):
    out.mkdir(parents=True, exist_ok=False)
    rows = {r['thread_id']: r for r in map(json.loads, queue.open())}
    pending = set(rows)
    rng = random.Random(42)
    samples = {'media': [], 'alta': []}
    counts = Counter()
    known = []
    for row in map(json.loads, previous.open()):
        if row['thread_id'] in {7228287, 7578802}:
            known.append(row)
        if row['label']['tipo'] != 'historia' or row['thread_id'] in pending:
            continue
        quality = row['label']['calidad']
        counts[quality] += 1
        pool = samples[quality]
        if len(pool) < 100:
            pool.append(row)
        else:
            index = rng.randrange(counts[quality])
            if index < 100:
                pool[index] = row
    audit = {r['thread_id']: r for r in sum(samples.values(), []) + known}
    for tid, row in audit.items():
        rows.setdefault(tid, row)
    with (out/'threads.jsonl').open('w') as f:
        for tid, row in sorted(rows.items()):
            f.write(json.dumps(row, ensure_ascii=False)+'\n')
    manifest = dict(seed=42, pending_ids=sorted(pending), audit_labels={str(tid): r['label'] for tid,r in audit.items() if tid not in pending},
                    requests=len(rows), input_sha256=hashlib.sha256((out/'threads.jsonl').read_bytes()).hexdigest(),
                    note='100 existing stories per quality tier plus known suspects; not a population-weighted error estimate')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(dict(requests=len(rows),pending=len(pending),audit=len(manifest['audit_labels']))))


def report(root):
    manifest = json.loads((root/'manifest.json').read_text())
    labels = {str(r['thread_id']):r for r in map(json.loads,(root/'review.jsonl').open())}
    excluded = {tid:r for tid,r in labels.items() if r.get('decision') == 'exclude'}
    labels = {tid:r for tid,r in labels.items() if tid not in excluded}
    changes = []
    for tid, old in manifest['audit_labels'].items():
        new = labels.get(tid)
        if new and any(old[k] != new[k] for k in ('tipo','calidad')):
            changes.append(dict(thread_id=int(tid),old=old,new=new))
    result = dict(requests=manifest['requests'],received=len(labels),excluded=len(excluded),excluded_ids=sorted(map(int,excluded)),audit_reviewed=sum(t in labels for t in manifest['audit_labels']),
                  audit_changes=len(changes),changes=changes,
                  note='Model relabelling is not human ground truth; targeted sample includes known suspects')
    (root/'audit-report.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='changes'}))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--queue',type=Path)
    ap.add_argument('--previous',type=Path,default=Path('data/curated/v2/threads.jsonl'))
    ap.add_argument('--report',action='store_true')
    args=ap.parse_args()
    if args.report:
        report(args.out)
    else:
        prepare(args.queue,args.previous,args.out)
