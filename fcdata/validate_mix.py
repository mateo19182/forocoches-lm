"""Check every output row against its manifest, token budget and split."""
import argparse
import hashlib
from itertools import zip_longest
import json
from pathlib import Path
import time

from tokenizers import Tokenizer


def validate(root, tokenizer):
    tok = Tokenizer.from_file(str(tokenizer))
    tok.no_padding()
    tok.no_truncation()
    sets, report = {}, {}
    for split in ('train','val'):
        groups, replay, contents = set(), set(), set()
        count = maximum = 0
        with (root/f'{split}.jsonl').open() as data, (root/f'{split}.manifest.jsonl').open() as metadata:
            for line, meta in zip_longest(data,metadata):
                if line is None or meta is None:
                    raise ValueError('Data/manifest row count mismatch')
                row, m = json.loads(line), json.loads(meta)
                if set(row) != {'prompt','completion','src'}:
                    raise ValueError('Unexpected training fields')
                text = row['prompt']+row['completion']
                if hashlib.sha256(text.encode()).hexdigest() != m['sha256']:
                    raise ValueError('Manifest content mismatch')
                tokens = len(tok.encode(text,add_special_tokens=False).ids)+2
                if tokens > 4096:
                    raise ValueError('Sequence exceeds token limit')
                if len(tok.encode(row['completion'],add_special_tokens=False).ids) != m['completion_tokens']:
                    raise ValueError('Completion token count mismatch')
                count += 1
                maximum = max(maximum,tokens)
                if 'group_id' in m:
                    groups.add(m['group_id'])
                if 'replay_id' in m:
                    replay.add(m['replay_id'])
                normalized = ' '.join(row['completion'].casefold().split())
                if len(normalized) > 200:
                    contents.add(hashlib.sha256(normalized.encode()).hexdigest())
        sets[split] = (groups,replay,contents)
        report[split] = dict(rows=count,max_sequence_tokens=maximum)
    for key, left, right in zip(('groups','replay_ids','long_completions'),sets['train'],sets['val']):
        overlap = len(left & right)
        report['overlapping_'+key] = overlap
        if overlap:
            raise ValueError(f'Train/validation overlap: {key}={overlap}')
    report['checked_at'] = time.time()
    (root/'validation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--tokenizer',type=Path,default=Path('runs/v0/final/tokenizer.json'))
    args=ap.parse_args()
    validate(args.data,args.tokenizer)
