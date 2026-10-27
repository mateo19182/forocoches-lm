"""Write a curated threads.jsonl for Hugging Face with leftover nicks masked.

    .venv/bin/python -m scripts.export_threads_hf --curated data/curated/v3 --out data/hf/forocoches-threads-v3
"""
import argparse
import json
from pathlib import Path
import shutil

from fcdata.crawl_v3 import mask_unmapped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--curated', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    changed = n = 0
    with (args.curated/'threads.jsonl').open() as source, (args.out/'threads.jsonl').open('w') as out:
        for line in source:
            row = json.loads(line)
            masked = mask_unmapped(row['text'])
            changed += masked != row['text']
            row['text'] = masked
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
            n += 1
    shutil.copy(args.curated/'summary.json', args.out/'summary.json')
    print(json.dumps(dict(threads=n, masked_threads=changed)))


if __name__ == '__main__':
    main()
