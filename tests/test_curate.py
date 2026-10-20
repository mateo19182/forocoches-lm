from collections import Counter
from pathlib import Path
import sqlite3
import unittest
import importlib.util
import hashlib
import json
import tempfile
from unittest.mock import patch

if importlib.util.find_spec('datasketch'):
    from fcdata.curate import deduplicate, eligible, merge_thread, reviewed_label, main
else:
    deduplicate = None


@unittest.skipIf(deduplicate is None, 'Run with .venv/bin/python for datasketch')
class CurateTests(unittest.TestCase):
    def test_reviewed_revision_replaces_archive_without_pending_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            archive=sqlite3.connect(root/'archive.sqlite')
            archive.executescript('CREATE TABLE threads(id,post_id,username,n_pages,title,body); CREATE TABLE comments(post_id,thread_id,username,body);')
            old='Old archive article. '*10
            fresh='Ayer me pasó algo que os quiero contar. '*10
            archive.execute('INSERT INTO threads VALUES(1,10,"author",1,"Title",?)',(old,))
            archive.commit()
            post=dict(id=10,position=1,author='author',html=fresh,_url='url',_observed=1)
            row,_=merge_thread(archive,1,[post],Counter(),root)
            archive.close()
            crawl=sqlite3.connect(root/'crawl.sqlite')
            crawl.executescript('CREATE TABLE posts(thread_id,page_url,payload,observed_at,id); CREATE TABLE pages(url,sha256);')
            crawl.execute('INSERT INTO posts VALUES(1,"url",?,1,10)',(json.dumps(post),))
            crawl.execute('INSERT INTO pages VALUES("url","unused")')
            crawl.commit()
            crawl.close()
            (root/'threads.jsonl').write_text(json.dumps(dict(row,text=row['text'].replace(fresh,old)))+'\n')
            (root/'labels').mkdir()
            (root/'labels'/'old.jsonl').write_text(json.dumps(dict(thread_id=1,tipo='noticia',calidad='media'))+'\n')
            label=dict(thread_id=1,tipo='historia',calidad='alta',text_sha256=hashlib.sha256(row['text'].encode()).hexdigest())
            (root/'review.jsonl').write_text(json.dumps(label)+'\n')
            with patch('sys.argv',['curate','--archive',str(root/'archive.sqlite'),'--public',str(root),'--threads',str(root/'threads.jsonl'),'--labels',str(root/'labels'),'--review-labels',str(root/'review.jsonl'),'--out',str(root/'out'),'--workers','1']):
                main()
            result=json.loads((root/'out'/'threads.jsonl').read_text())
            self.assertIn(fresh.strip(),result['text'])
            self.assertEqual(result['label']['tipo'],'historia')
            self.assertEqual((root/'out'/'needs-labels.jsonl').read_text(),'')
            exclusion = dict(thread_id=1,decision='exclude',reason='classifier_refusal',text_sha256=label['text_sha256'])
            (root/'review.jsonl').write_text(json.dumps(exclusion)+'\n')
            with patch('sys.argv',['curate','--archive',str(root/'archive.sqlite'),'--public',str(root),'--threads',str(root/'threads.jsonl'),'--labels',str(root/'labels'),'--review-labels',str(root/'review.jsonl'),'--out',str(root/'excluded-out'),'--workers','1']):
                main()
            self.assertEqual((root/'excluded-out'/'threads.jsonl').read_text(),'')
            self.assertEqual((root/'excluded-out'/'needs-labels.jsonl').read_text(),'')
            excluded=json.loads((root/'excluded-out'/'excluded.jsonl').read_text())
            self.assertEqual(excluded['reason'],'classifier_refusal')

    def test_refused_review_is_ineligible(self):
        self.assertFalse(eligible(dict(decision='exclude', reason='classifier_refusal')))

    def test_review_is_bound_to_exact_text_revision(self):
        import hashlib
        row = dict(thread_id=1,text='A revised story')
        label = dict(tipo='historia',calidad='alta',text_sha256=hashlib.sha256(row['text'].encode()).hexdigest())
        self.assertEqual(reviewed_label(row,{1:label}),label)
        self.assertIsNone(reviewed_label(dict(row,text='Old story'),{1:label}))
        self.assertIsNone(reviewed_label(row,{1:dict(tipo='historia',calidad='alta')}))

    def test_merge_updates_post_ids_and_preserves_archive_replies(self):
        db = sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE threads(id,post_id,username,n_pages,title,body); CREATE TABLE comments(post_id,thread_id,username,body);')
        db.execute('INSERT INTO threads VALUES(1,10,"author",3,"Story","<p>Ayer me pasó algo</p>")')
        db.executemany('INSERT INTO comments VALUES(?,?,?,?)',[(11,1,'reader','Old reply'),(12,1,'reader','Still here')])
        fresh = [dict(id=10,position=1,author='author',html='<div>Ayer me pasó algo</div>',_url='url',_observed=1),
                 dict(id=11,position=2,author='reader',html='Updated reply',_observed=2),
                 dict(id=13,position=4,author='author',html='Another long reply',_observed=3)]
        stats = Counter()
        row, same = merge_thread(db,1,fresh,stats,Path('.'))
        self.assertTrue(same)
        self.assertEqual(row['post_ids'],[10,11,12,13])
        self.assertEqual(stats['fresh_new_post_ids'],1)
        self.assertIn('Updated reply',row['text'])
        self.assertNotIn('Old reply',row['text'])
        fresh[0]['html']='Changed opener'
        self.assertFalse(merge_thread(db,1,fresh,Counter(),Path('.'))[1])
        db.close()

    def test_quality_and_near_duplicate_groups(self):
        self.assertFalse(eligible(dict(tipo='historia',calidad='baja')))
        self.assertFalse(eligible(dict(tipo='noticia',calidad='alta')))
        self.assertTrue(eligible(dict(tipo='humor',calidad='media')))
        base=' '.join(f'palabra{i}' for i in range(100))
        def row(text):return dict(text='TÍTULO: title\n\n#1 OP: '+text)
        rows={1:row(base),2:row(base+' final diferente'),3:row(' '.join(f'otro{i}' for i in range(100)))}
        groups=deduplicate(rows,workers=1)
        self.assertEqual(sorted(map(sorted,groups.values())),[[1,2],[3]])


if __name__ == '__main__':unittest.main()
