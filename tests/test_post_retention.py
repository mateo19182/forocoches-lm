import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import collect


def page(posts):
    parts=['<title>Story</title>']
    for pid,author,position,body in posts:
        parts.append(f'''<div class="postbit_wrapper"><span class="postdate">Today</span>
          <a href="member.php?u={author}">Author {author}</a>
          <a class="post-counter">#{position}</a><div id="post_message_{pid}">{body}</div></div>''')
    return SimpleNamespace(status_code=200,headers={},content=''.join(parts).encode())


class RetentionTests(unittest.TestCase):
    def test_deep_formatting_keeps_complete_long_post_and_own_length(self):
        story='A long story with nested formatting. '*100
        body='<span>'*600+story+'</span>'*600
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);url=collect.canonical('showthread.php?t=1')
            with collect.connect(root) as db:
                collect.enqueue(db,[url])
                self.assertEqual(collect.save_response(db,root,url,0,page([(1,10,1,body)]),min_own_chars=2000),'done')
                kept=json.loads(db.execute('SELECT payload FROM posts WHERE id=1').fetchone()[0])
                self.assertEqual(kept['own_text_chars'],len(story.strip()))
                self.assertEqual(kept['retention_reason'],'long')
                self.assertEqual(kept['text'],story.strip())

    def test_keeps_long_posts_and_op_sections_but_drops_short_replies_and_quotes(self):
        long='My story '*400
        quote='<div style="margin:20px; margin-top:5px">'+'Quoted story '*400+'</div>Short reply'
        response=page([(1,10,1,long),(2,20,2,'Short reply'),(3,10,3,'And then...'),
                       (4,30,4,long),(5,20,5,quote)])
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);url=collect.canonical('showthread.php?t=1')
            with collect.connect(root) as db:
                collect.enqueue(db,[url]);collect.save_response(db,root,url,0,response,min_own_chars=2000)
                kept={pid:json.loads(payload) for pid,payload in db.execute('SELECT id,payload FROM posts')}
                self.assertEqual(set(kept),{1,3,4})
                self.assertEqual(kept[3]['retention_reason'],'op_followup')
                self.assertEqual(db.execute('SELECT posts_seen,posts_kept FROM retention_pages').fetchone(),(5,3))

    def test_later_long_op_section_backfills_earlier_intro_with_original_provenance(self):
        first=collect.canonical('showthread.php?t=1');later=collect.canonical('showthread.php?t=1&page=2')
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            with collect.connect(root) as db:
                collect.enqueue(db,[first,later])
                collect.save_response(db,root,first,0,page([(1,10,1,'An introduction'),(2,20,2,'Following')]),min_own_chars=2000)
                observed=db.execute('SELECT fetched_at FROM pages WHERE url=?',(first,)).fetchone()[0]
                self.assertEqual(db.execute('SELECT count(*) FROM posts').fetchone()[0],0)
                collect.save_response(db,root,later,0,page([(3,10,31,'Long section '*300),(4,20,32,'Great!')]),min_own_chars=2000)
                self.assertEqual([r[0] for r in db.execute('SELECT id FROM posts ORDER BY id')],[1,3])
                self.assertEqual(db.execute('SELECT page_url,observed_at FROM posts WHERE id=1').fetchone(),(first,observed))
                self.assertEqual(json.loads(db.execute('SELECT payload FROM posts WHERE id=1').fetchone()[0])['retention_reason'],'op_context')

    def test_a_long_reply_does_not_promote_short_posts_by_an_unrelated_op(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);url=collect.canonical('showthread.php?t=1')
            with collect.connect(root) as db:
                collect.enqueue(db,[url])
                collect.save_response(db,root,url,0,page([(1,10,1,'Question'),(2,20,2,'Long reply '*300),(3,10,3,'Thanks')]),min_own_chars=2000)
                self.assertEqual([r[0] for r in db.execute('SELECT id FROM posts')],[2])
                self.assertEqual(db.execute('SELECT state FROM pages').fetchone()[0],'done')


if __name__=='__main__':unittest.main()
