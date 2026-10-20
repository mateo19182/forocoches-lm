"""Incremental counters keep per-batch reporting independent of archive size."""


TOTALS = {
    'attempts': [('pages','attempts'),('listing_jobs','attempts')],
    'posts': [('posts',None)],
    'threads_seen': [('threads',None)],
    'saved_response_bytes': [('fetch_events','body_bytes')],
    'page_post_observations': [('retention_pages','posts_seen')],
    'candidates': [('pilot_candidates',None)],
}


def total(db,key):
    return db.execute('SELECT value FROM crawl_totals WHERE name=?',(key,)).fetchone()[0]


def counts(db,table):
    return dict(db.execute(f'SELECT value,n FROM {table} WHERE n>0'))


def page_count(db):
    return db.execute('SELECT coalesce(sum(n),0) FROM crawl_page_states').fetchone()[0]


def _category(db,table,column,counter,condition=None):
    db.execute(f'CREATE TABLE IF NOT EXISTS {counter}(value TEXT PRIMARY KEY,n INTEGER NOT NULL)')
    marker='_'+counter+'_ready'
    if not db.execute('SELECT 1 FROM crawl_totals WHERE name=?',(marker,)).fetchone():
        where=f'WHERE {condition}' if condition else ''
        db.execute(f'INSERT INTO {counter} SELECT {column},count(*) FROM {table} {where} GROUP BY {column}')
        db.execute('INSERT INTO crawl_totals VALUES(?,1)',(marker,))
    new_condition=f'NEW.{condition}' if condition else '1'
    old_condition=f'OLD.{condition}' if condition else '1'
    insert=f'''INSERT INTO {counter}(value,n) SELECT NEW.{column},1 WHERE {new_condition}
      ON CONFLICT(value) DO UPDATE SET n=n+1;'''
    delete=f'UPDATE {counter} SET n=n-1 WHERE value=OLD.{column} AND {old_condition};'
    updated_columns=column+',scan_order' if condition else column
    for event,body in (('INSERT',insert),('DELETE',delete),(f'UPDATE OF {updated_columns}',delete+insert)):
        name=counter+'_'+event.split()[0].lower()
        db.execute(f'CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON {table} BEGIN {body} END')


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS crawl_totals(name TEXT PRIMARY KEY,value INTEGER NOT NULL)')
    for name,sources in TOTALS.items():
        if not db.execute('SELECT 1 FROM crawl_totals WHERE name=?',(name,)).fetchone():
            baseline=sum(db.execute(f'SELECT coalesce(sum({field}),0) FROM {table}' if field else f'SELECT count(*) FROM {table}').fetchone()[0]
                         for table,field in sources)
            db.execute('INSERT INTO crawl_totals VALUES(?,?)',(name,baseline))
        for table,field in sources:
            new=f'coalesce(NEW.{field},0)' if field else '1'
            old=f'coalesce(OLD.{field},0)' if field else '1'
            events=[('INSERT',f'+({new})'),('DELETE',f'-({old})')]
            if field:events.append((f'UPDATE OF {field}',f'+({new})-({old})'))
            for event,delta in events:
                trigger='metrics_'+name+'_'+table+'_'+event.split()[0].lower()
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {event} ON {table}
                  BEGIN UPDATE crawl_totals SET value=value{delta} WHERE name='{name}'; END''')
    _category(db,'pages','state','crawl_page_states')
    _category(db,'pages','state','crawl_scan_states','scan_order IS NOT NULL')
    _category(db,'post_retention_meta','reason','crawl_retention_states')
    db.execute('CREATE TABLE IF NOT EXISTS crawl_thread_posts(thread_id INTEGER PRIMARY KEY,n INTEGER NOT NULL)')
    if not db.execute("SELECT 1 FROM crawl_totals WHERE name='_thread_posts_ready'").fetchone():
        db.execute('INSERT INTO crawl_thread_posts SELECT thread_id,count(*) FROM posts GROUP BY thread_id')
        db.execute("INSERT INTO crawl_totals VALUES('_thread_posts_ready',1)")
    add='''INSERT INTO crawl_thread_posts VALUES(NEW.thread_id,1)
      ON CONFLICT(thread_id) DO UPDATE SET n=n+1;'''
    remove='''UPDATE crawl_thread_posts SET n=n-1 WHERE thread_id=OLD.thread_id;
      DELETE FROM crawl_thread_posts WHERE thread_id=OLD.thread_id AND n=0;'''
    db.execute(f'CREATE TRIGGER IF NOT EXISTS metrics_thread_insert AFTER INSERT ON posts BEGIN {add} END')
    db.execute(f'CREATE TRIGGER IF NOT EXISTS metrics_thread_delete AFTER DELETE ON posts BEGIN {remove} END')
    db.execute(f'''CREATE TRIGGER IF NOT EXISTS metrics_thread_move AFTER UPDATE OF thread_id ON posts
      WHEN OLD.thread_id!=NEW.thread_id BEGIN {remove}{add} END''')
