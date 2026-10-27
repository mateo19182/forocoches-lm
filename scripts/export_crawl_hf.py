"""Write the pseudonymised 2026 crawl as Parquet for Hugging Face (posts + thread documents, with labels when present).

    .venv-train/bin/python scripts/export_crawl_hf.py --src data/processed/crawl-2026-all --labels data/labels/v3 \
        --out data/hf/forocoches-crawl-2026
"""
import argparse
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROWS_PER_SHARD = 200_000
# Explicit types so shards without labels do not infer a null column.
SCHEMAS = dict(
    threads=pa.schema([('thread_id', pa.int64()), ('title', pa.string()), ('year', pa.int32()), ('has_op', pa.bool_()),
                       ('tags', pa.list_(pa.string())), ('text', pa.string()), ('post_ids', pa.list_(pa.int64())),
                       ('page_url', pa.string()), ('collected_at', pa.float64()), ('tipo', pa.string()),
                       ('calidad', pa.string()), ('label_model', pa.string())]),
    posts=pa.schema([('thread_id', pa.int64()), ('post_id', pa.int64()), ('position', pa.int32()), ('author', pa.string()),
                     ('retention', pa.string()), ('posted_at', pa.string()), ('own_chars', pa.int32()), ('text', pa.string()),
                     ('title', pa.string())]))


def rows(path):
    with path.open() as source:
        yield from map(json.loads, source)


def write(name, records, out):
    (out/name).mkdir(parents=True, exist_ok=True)
    shard, n, total = [], 0, 0
    for record in records:
        shard.append(record)
        if len(shard) == ROWS_PER_SHARD:
            pq.write_table(pa.Table.from_pylist(shard, schema=SCHEMAS[name]), out/name/f'{n:05d}.parquet', compression='zstd')
            total += len(shard)
            shard, n = [], n + 1
    if shard:
        pq.write_table(pa.Table.from_pylist(shard, schema=SCHEMAS[name]), out/name/f'{n:05d}.parquet', compression='zstd')
        total += len(shard)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--src', type=Path, required=True)
    ap.add_argument('--labels', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    labels = {}
    for path in sorted(args.labels.glob('*.jsonl')) if args.labels else []:
        for lab in rows(path):
            if lab.get('tipo'):
                labels[lab['thread_id']] = dict(tipo=lab['tipo'], calidad=lab['calidad'], model=lab.get('model'))
    titles = {}

    def threads():
        for d in rows(args.src/'docs.jsonl'):
            titles[d['thread_id']] = d['text'].split('\n', 1)[0].removeprefix('TÍTULO: ')
            lab = labels.get(d['thread_id'])
            yield dict(thread_id=d['thread_id'], title=titles[d['thread_id']], year=d['year'], has_op=d['has_op'],
                       tags=d['tags'], text=d['text'], post_ids=d['post_ids'], page_url=d['page_url'],
                       collected_at=d['collected_at'], tipo=lab and lab['tipo'], calidad=lab and lab['calidad'],
                       label_model=lab and lab['model'])

    counts = dict(threads=write('threads', threads(), args.out))
    counts['posts'] = write('posts', (dict(p, title=titles.get(p['thread_id'])) for p in rows(args.src/'posts.jsonl')), args.out)
    counts['labelled_threads'] = sum(1 for t in titles if t in labels)
    (args.out/'summary.json').write_text(json.dumps(counts, indent=2))
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
