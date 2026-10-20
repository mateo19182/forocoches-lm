"""Discover outside-archive thread links from saved story indexes, without network requests."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from bs4 import BeautifulSoup
import collect
from public_crawl import thread_link
from pilot_policy import identity


def is_index(title):
    title = ''.join(c for c in unicodedata.normalize('NFD', title or '').lower()
                    if unicodedata.category(c) != 'Mn')
    return bool(re.search(r'recopil|indice|biblioteca', title) and
                re.search(r'relat|historias|hilos miticos|mejores hilos', title))


def discover(archive):
    sources, candidates = [], {}
    with sqlite3.connect(Path(archive).resolve().as_uri()+'?mode=ro', uri=True) as db:
        for tid, title in db.execute('SELECT id,title FROM threads'):
            if not is_index(title):
                continue
            body = db.execute('SELECT body FROM threads WHERE id=?', (tid,)).fetchone()[0]
            found = set()
            for a in BeautifulSoup(body or '', 'html.parser').select('a[href]'):
                url = thread_link(a['href'])
                if not url:
                    continue
                target, _ = identity(url)
                if target == tid or 7000000 <= target < 8000000:
                    continue
                first = collect.canonical(f'showthread.php?t={target}')
                row = candidates.setdefault(target, dict(thread_id=target, url=first, sources=[]))
                evidence = dict(source=f'archive-index:{tid}', title=collect.clean_unicode(a.get_text(' ',strip=True)))
                if evidence not in row['sources']:
                    row['sources'].append(evidence)
                found.add(target)
            sources.append(dict(thread_id=tid, title=title, outside_links=len(found)))
    return dict(indexes=sources, candidates=list(candidates.values()),
                note='Links only; no archive bodies copied or live accessibility checked.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,default=Path('data/fc_dataset_7-8.sqlite'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=discover(args.archive)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.out.with_suffix('.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(args.out)
    print(json.dumps(dict(indexes=len(result['indexes']),candidates=len(result['candidates']))))


if __name__=='__main__':main()
