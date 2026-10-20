"""Persist a bounded numeric thread walk and stage a small queue window."""
import collect
import crawl_metrics


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS id_scan(
      id INTEGER PRIMARY KEY CHECK(id=1),start_id INTEGER NOT NULL,end_id INTEGER NOT NULL,
      next_id INTEGER NOT NULL,step INTEGER NOT NULL,admitted INTEGER NOT NULL DEFAULT 0,
      skipped_known INTEGER NOT NULL DEFAULT 0,skipped_archive INTEGER NOT NULL DEFAULT 0)''')


def configure(db,start,end):
    if start is None and end is None:
        return
    if start is None or end is None or min(start,end)<1:
        raise ValueError('Specify positive scan start and end IDs together')
    current=db.execute('SELECT start_id,end_id FROM id_scan WHERE id=1').fetchone()
    if current and current!=(start,end):
        old_start,old_end=current
        extending=start==old_start and ((old_end>=old_start and end>=old_end) or (old_end<old_start and end<=old_end))
        if not extending:
            raise ValueError('Scan bounds can only extend the saved range without changing its direction')
        db.execute('UPDATE id_scan SET end_id=? WHERE id=1',(end,))
    db.execute('INSERT OR IGNORE INTO id_scan(id,start_id,end_id,next_id,step) VALUES(1,?,?,?,?)',
               (start,end,start,1 if end>=start else -1))


def fill(db,seed,settings,window=32):
    row=db.execute('SELECT start_id,end_id,next_id,step FROM id_scan WHERE id=1').fetchone()
    if not row:
        return
    start,end,current,step=row
    pending=db.execute("SELECT count(*) FROM pages WHERE state='pending' AND scan_order IS NOT NULL").fetchone()[0]
    while pending<window and (current<=end if step==1 else current>=end):
        if settings.get('novel_pilot') and settings.get('exclude_archive_ids',True) and 7000000<=current<8000000:
            following=min(end+1,8000000) if step==1 else max(end-1,6999999)
            db.execute('UPDATE id_scan SET next_id=?,skipped_archive=skipped_archive+? WHERE id=1',(following,abs(following-current)))
            current=following
            continue
        url=collect.canonical(f'showthread.php?t={current}')
        page=db.execute('SELECT state FROM pages WHERE url=?',(url,)).fetchone()
        if page and page[0]!='pending':
            db.execute('UPDATE id_scan SET skipped_known=skipped_known+1 WHERE id=1')
        else:
            if crawl_metrics.page_count(db)>=settings.get('max_discovered_pages',50000):
                break
            if not seed(db,url,f'id-scan:{start}:{end}'):
                break
            db.execute('UPDATE pages SET scan_order=? WHERE url=?',(current*step,url))
            db.execute('UPDATE id_scan SET admitted=admitted+1 WHERE id=1')
            pending+=1
        current+=step
        db.execute('UPDATE id_scan SET next_id=? WHERE id=1',(current,))


def summary(db):
    columns=('start_id','end_id','next_to_queue','step','admitted','skipped_known','skipped_archive')
    row=db.execute('SELECT start_id,end_id,next_id,step,admitted,skipped_known,skipped_archive FROM id_scan WHERE id=1').fetchone()
    if not row:
        return None
    result=dict(zip(columns,row))
    result['page_states']=crawl_metrics.counts(db,'crawl_scan_states')
    result['complete']=(result['next_to_queue']>result['end_id'] if result['step']==1 else result['next_to_queue']<result['end_id']) and not result['page_states'].get('pending')
    return result
