"""Wait for the submitted review batch, then build and validate its dataset."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import anthropic


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--version',required=True)
    args=ap.parse_args()
    version=args.version
    review=Path('data/labels')/version
    prepared=Path('data/curated')/(version+'-review')
    output=Path('data/curated')/version
    mix=Path('data/mix')/version
    status=review/'build-status.json'
    provenance=dict(started_at=time.time(),commands=[],files={})

    def state(stage, **extra):
        temporary=status.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(stage=stage,updated_at=time.time(),**extra),indent=2))
        temporary.replace(status)
        print(stage,flush=True)

    def run(command):
        provenance['commands'].append(command)
        (review/'build-provenance.json').write_text(json.dumps(provenance,indent=2))
        subprocess.run(command,check=True,env={**os.environ,'TOKENIZERS_PARALLELISM':'false'})

    try:
        state('waiting_for_labels')
        batches=[b for b in json.loads((review/'batches.json').read_text()) if b['name']=='review']
        if not batches:
            raise ValueError('No submitted review batches')
        client=anthropic.Anthropic()
        deadline=time.monotonic()+24*3600
        while not all(client.messages.batches.retrieve(b['id']).processing_status=='ended' for b in batches):
            if time.monotonic()>deadline:
                raise TimeoutError('Review batch did not finish within 24 hours')
            state('waiting_for_labels')
            time.sleep(60)
        state('collecting_labels')
        run(['.venv/bin/python','-m','fcdata.label','--label-dir',str(review),'collect','--name','review'])
        requested={r['thread_id']:hashlib.sha256(r['text'].encode()).hexdigest() for r in map(json.loads,(review/'threads.jsonl').open())}
        received={r['thread_id']:r for r in map(json.loads,(review/'review.jsonl').open())}
        if requested.keys()!=received.keys() or any(received[tid].get('text_sha256')!=digest for tid,digest in requested.items()):
            raise ValueError('Review results are incomplete or do not match submitted text; build stopped')
        run(['.venv/bin/python','-m','fcdata.review','--out',str(review),'--report'])
        state('recording_provenance')
        sources=review/'source'
        sources.mkdir(exist_ok=True)
        files=[Path('data/fc_dataset_7-8.sqlite'),Path('data/processed/threads.v0.jsonl'),prepared/'public-snapshot.sqlite',
               review/'threads.jsonl',review/'review.jsonl',Path('runs/v0/final/tokenizer.json'),
               Path('data/replay/fineweb2_spa.jsonl'),Path('data/mix/v1/val.jsonl')]
        files+=list(Path('data/labels').glob('*.jsonl'))
        for path in sorted(Path('fcdata').glob('*.py')):
            shutil.copy2(path,sources/path.name)
            files.append(path)
        for path in files:
            with path.open('rb') as f:
                digest=hashlib.file_digest(f,'sha256').hexdigest()
            provenance['files'][str(path)]=digest
        state('curating')
        run(['.venv/bin/python','-m','fcdata.curate','--out',str(output),'--workers','2',
             '--snapshot',str(prepared/'public-snapshot.sqlite'),'--review-labels',str(review/'review.jsonl')])
        if (output/'needs-labels.jsonl').stat().st_size:
            raise ValueError('Unexpected pending labels after review; inspect before mixing')
        state('mixing')
        run(['.venv-train/bin/python','-m','fcdata.mix_v2','--threads',str(output/'threads.jsonl'),'--out',str(mix)])
        state('validating')
        run(['.venv-train/bin/python','-m','fcdata.validate_mix','--data',str(mix)])
        state('complete',curated=str(output),mix=str(mix))
    except Exception as error:
        state('failed',error_type=type(error).__name__)
        raise


if __name__=='__main__':
    main()
