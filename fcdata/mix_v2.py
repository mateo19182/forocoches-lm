"""Build a token-counted story/conversation/replay mix from curated threads."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re

from tokenizers import Tokenizer

from .mix import header


def chunks(text, prompt, tokenizer, max_tokens=4096):
    remaining, previous = text, ''
    while remaining:
        context = ''
        if previous:
            encoded = tokenizer.encode(previous, add_special_tokens=False)
            start = encoded.offsets[max(0, len(encoded.ids)-256)][0]
            context = previous[start:]
        prefix = prompt + context
        room = max_tokens - len(tokenizer.encode(prefix, add_special_tokens=False).ids) - 8
        if room < 32:
            raise ValueError('Header/context leaves no room for the completion')
        encoded = tokenizer.encode(remaining, add_special_tokens=False)
        if len(encoded.ids) <= room:
            end = len(remaining)
        else:
            end = encoded.offsets[room-1][1]
            boundaries = [m.start() for m in re.finditer(r'\n\n(?=#\d+ )', remaining[:end])]
            if not boundaries:
                boundaries = [m.end() for m in re.finditer(r'[.!?]\s+', remaining[:end])]
            if boundaries and boundaries[-1] > end // 4:
                end = boundaries[-1]
        completion = remaining[:end]
        # Verify the combined encoding, including boundary merges and BOS/EOS allowance.
        while len(tokenizer.encode(prefix+completion, add_special_tokens=False).ids)+2 > max_tokens:
            end = max(1, end-32)
            completion = remaining[:end]
        if not completion:
            raise ValueError('Tokenizer produced an empty chunk')
        yield dict(prompt=prefix,completion=completion), len(tokenizer.encode(completion,add_special_tokens=False).ids)
        previous += completion
        remaining = remaining[end:]


def select(rows, target, rng):
    ordered = list(rows)
    rng.shuffle(ordered)
    chosen, total = [], 0
    for item in ordered:
        if total >= target:
            break
        chosen.append(item)
        total += item[1]
    return chosen


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--threads',type=Path,required=True)
    ap.add_argument('--replay',type=Path,default=Path('data/replay/fineweb2_spa.jsonl'))
    ap.add_argument('--old-val',type=Path,default=Path('data/mix/v1/val.jsonl'))
    ap.add_argument('--tokenizer',type=Path,default=Path('runs/v0/final/tokenizer.json'))
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--shares',type=float,nargs=3,default=(.6,.3,.1))
    ap.add_argument('--seed',type=int,default=0)
    args = ap.parse_args()
    if any(x <= 0 for x in args.shares) or abs(sum(args.shares)-1) > 1e-8:
        ap.error('Shares must be positive and sum to one')
    args.out.mkdir(parents=True,exist_ok=False)
    tok = Tokenizer.from_file(str(args.tokenizer))
    tok.no_truncation()
    tok.no_padding()
    rng = random.Random(args.seed)
    pools = {k:[] for k in ('stories','conversation','replay')}
    validation = []
    for n, line in enumerate(args.threads.open(), 1):
        row = json.loads(line)
        lab = row['label']
        group = 'stories' if lab['tipo']=='historia' else 'conversation'
        prompt = header(row,lab,rng,dropout=row['split']=='train')
        for index, (doc, tokens) in enumerate(chunks(row['text'],prompt,tok)):
            doc['src'] = lab['tipo']
            meta = dict(thread_id=row['thread_id'],group_id=row['group_id'],chunk=index,
                        sources=row['sources'],quality=lab['calidad'],pool=group)
            (pools[group] if row['split']=='train' else validation).append((doc,tokens,meta))
        if n % 10000 == 0:
            print(json.dumps(dict(stage='tokenize',threads=n)),flush=True)
    old_replay = set()
    for line in args.old_val.open():
        row = json.loads(line)
        if row['src']=='replay':
            old_replay.add(hashlib.sha256(row['completion'].encode()).hexdigest())
            for index,(doc,tokens) in enumerate(chunks(row['completion'],'',tok)):
                doc['src']='replay'
                validation.append((doc,tokens,dict(pool='replay',replay_id=hashlib.sha256(row['completion'].encode()).hexdigest(),chunk=index)))
    seen_replay = set()
    for line in args.replay.open():
        text = json.loads(line)['text']
        digest = hashlib.sha256(text[:13000].encode()).hexdigest()
        if digest in old_replay or digest in seen_replay:
            continue
        seen_replay.add(digest)
        for index,(doc,tokens) in enumerate(chunks(text,'',tok)):
            doc['src']='replay'
            pools['replay'].append((doc,tokens,dict(pool='replay',replay_id=digest,chunk=index)))
    available = {k:sum(x[1] for x in rows) for k,rows in pools.items()}
    if not all(available.values()):
        raise ValueError(f'Cannot build requested mix: empty pool in {available}')
    # A single pass through available stories sets the size; never pad with repeats.
    total = min(available[k]/share for k,share in zip(pools,args.shares))
    training = []
    for k,share in zip(pools,args.shares):
        training.extend(select(pools[k],total*share,rng))
    rng.shuffle(training)
    summary = dict(available_completion_tokens=available,target_shares=dict(zip(pools,args.shares)),
                   tokenizer=str(args.tokenizer),max_sequence_tokens=4096,seed=args.seed,
                   note='One copy per chunk; 256-token masked continuation context; old replay holdout retained')
    for split,rows in (('train',training),('val',validation)):
        counts, tokens = Counter(), Counter()
        with (args.out/f'{split}.jsonl').open('w') as output, (args.out/f'{split}.manifest.jsonl').open('w') as manifest:
            for doc,n,meta in rows:
                output.write(json.dumps(doc,ensure_ascii=False)+'\n')
                manifest.write(json.dumps(dict(**meta,completion_tokens=n,sha256=hashlib.sha256((doc['prompt']+doc['completion']).encode()).hexdigest()))+'\n')
                counts[meta['pool']] += 1
                tokens[meta['pool']] += n
        summary[split] = dict(rows=dict(counts),completion_tokens=dict(tokens),total_completion_tokens=sum(tokens.values()))
    train_groups = {m['group_id'] for _,_,m in training if 'group_id' in m}
    val_groups = {m['group_id'] for _,_,m in validation if 'group_id' in m}
    if train_groups & val_groups:
        raise ValueError('Duplicate group crossed train/validation')
    summary['overlapping_groups'] = 0
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
