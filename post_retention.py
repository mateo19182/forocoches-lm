"""Keep long own-text posts and the original author's related sections."""
import gzip
from urllib.parse import parse_qs, urlparse

from fcdata.build import core_text
from fcdata.parse import parse_post

PAGE_COUNTS_SQL = '''INSERT INTO retention_pages VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET
  posts_seen=excluded.posts_seen,posts_kept=excluded.posts_kept,observed_at=excluded.observed_at'''


def setup(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS thread_authors(thread_id INTEGER PRIMARY KEY,
        author_id INTEGER,opener_post_id INTEGER,long_op INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS retention_pages(url TEXT PRIMARY KEY,
        posts_seen INTEGER NOT NULL,posts_kept INTEGER NOT NULL,observed_at REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS post_retention_meta(post_id INTEGER PRIMARY KEY,reason TEXT NOT NULL,own_chars INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS retention_reason ON post_retention_meta(reason);
    ''')


def own_chars(post):
    return len(core_text(parse_post(post['html']).text))


def select_posts(db,tid,posts,minimum):
    opener=next((p for p in posts if p.get('position')==1),None)
    if opener:
        db.execute('''INSERT INTO thread_authors(thread_id,author_id,opener_post_id) VALUES(?,?,?)
          ON CONFLICT(thread_id) DO UPDATE SET
          author_id=coalesce(excluded.author_id,thread_authors.author_id),
          opener_post_id=excluded.opener_post_id''',(tid,opener.get('author_id'),opener['id']))
    author=db.execute('SELECT author_id,long_op FROM thread_authors WHERE thread_id=?',(tid,)).fetchone()
    lengths={p['id']:own_chars(p) for p in posts if len(p.get('text',''))>=minimum}
    was_long=bool(author and author[1])
    long_op=was_long or bool(author and author[0] is not None and any(
        p.get('author_id')==author[0] and lengths.get(p['id'],0)>=minimum for p in posts))
    if long_op and not was_long:
        db.execute('UPDATE thread_authors SET long_op=1 WHERE thread_id=?',(tid,))
    kept=[]
    for post in posts:
        count=lengths.get(post['id'])
        if count is not None and count>=minimum:
            reason='long'
        elif long_op and post.get('author_id')==author[0]:
            count=own_chars(post)
            reason='op_context' if post.get('position')==1 else 'op_followup'
        else:
            continue
        kept.append({**post,'own_text_chars':count,'retention_reason':reason})
    return kept,long_op and not was_long


def retain_page(db,root,url,parsed,observed_at,minimum,parser):
    tid=int(parse_qs(urlparse(url).query)['t'][0])
    kept,activated=select_posts(db,tid,parsed['posts'],minimum)
    records=[(post,url,observed_at) for post in kept]
    db.execute(PAGE_COUNTS_SQL,
               (url,len(parsed['posts']),len(kept),observed_at))
    if activated:
        # Earlier short sections may precede the first long OP section.
        prefix=url.split('?',1)[0]+f'?t={tid}&page='
        # Filter state after the URL range lookup to avoid scanning every completed page.
        earlier=db.execute('SELECT url,sha256,fetched_at,state FROM pages WHERE url>=? AND url<? AND url!=?',(prefix,prefix+'~',url)).fetchall()
        for previous,digest,when,state in earlier:
            if state!='done' or not digest:
                continue
            raw=root/'raw'/digest[:2]/(digest+'.html.gz')
            old=parser(gzip.decompress(raw.read_bytes()),previous)
            selected,_=select_posts(db,tid,old['posts'],minimum)
            records.extend((post,previous,when) for post in selected)
            db.execute(PAGE_COUNTS_SQL,
                       (previous,len(old['posts']),len(selected),when))
    return records
