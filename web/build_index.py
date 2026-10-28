"""Build the SQLite index the web reads: every thread we have with its label, year, source, title and length,
plus an FTS5 index on titles.

    .venv-train/bin/python -m web.build_index            # -> data/web/index.sqlite

Sources: the historical archive (all types and qualities), the 2026 long-post crawl (threads not in the archive)
and the public-crawl threads of the curated v3 pool. Labels: curated v3, then the v3 crawl labels, then the archive's
original labels. Text gets the same nick masking as the published dataset.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from fcdata.crawl_v3 import YearIndex, mask_unmapped
from web.threads import parse_thread

SCHEMA = """
CREATE TABLE threads (
    thread_id INTEGER PRIMARY KEY,
    tipo TEXT, calidad TEXT, title TEXT,
    n_posts INTEGER, chars INTEGER, pages_bucket TEXT, tags TEXT,
    text TEXT, year INTEGER, source TEXT
);
CREATE INDEX idx_tipo_calidad ON threads(tipo, calidad);
CREATE INDEX idx_year ON threads(year);
CREATE VIRTUAL TABLE titles USING fts5(title, content='threads', content_rowid='thread_id', tokenize='unicode61 remove_diacritics 2');
"""


def rows(path):
    with open(path) as f:
        for line in f:
            yield json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="data/processed/threads.v0.jsonl")
    ap.add_argument("--archive-labels", default="data/labels/full.jsonl")
    ap.add_argument("--crawl", default="data/processed/crawl-2026/docs.jsonl")
    ap.add_argument("--crawl-labels", type=Path, default=Path("data/labels/v3"))
    ap.add_argument("--curated", default="data/curated/v3/threads.jsonl")
    ap.add_argument("--year-ref", default="data/processed/crawl-2026-all/docs.jsonl")
    ap.add_argument("--out", default="data/web/index.sqlite")
    args = ap.parse_args()

    labels = {d["thread_id"]: (d["tipo"], d["calidad"]) for d in rows(args.archive_labels)}
    for p in sorted(args.crawl_labels.glob("*.jsonl")):
        labels.update((d["thread_id"], (d.get("tipo"), d.get("calidad"))) for d in rows(p))
    public = []
    for r in rows(args.curated):
        labels[r["thread_id"]] = (r["label"]["tipo"], r["label"]["calidad"])
        if r["sources"][-1] == "public":
            public.append(r)
    year_of = YearIndex(Path(args.year_ref))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)

    seen, batch = set(), []
    def flush():
        db.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
        batch.clear()
        print(f"\r{len(seen):,} threads", end="", flush=True)

    for source, it in (("crawl-2026", rows(args.crawl)), ("public", iter(public)), ("archive", rows(args.archive))):
        for row in it:
            tid = row["thread_id"]
            if tid in seen:
                continue
            seen.add(tid)
            text = mask_unmapped(row["text"])
            tipo, calidad = labels.get(tid, (None, None))
            title, posts = parse_thread(text)
            batch.append((tid, tipo, calidad, title, len(posts), len(text), row["pages_bucket"], " ".join(row["tags"]),
                          text, row.get("year") or year_of(tid), source))
            if len(batch) >= 5000:
                flush()
    flush()
    print(f"\r{len(seen):,} threads; building title index...")
    db.execute("INSERT INTO titles(titles) VALUES ('rebuild')")
    db.commit()
    db.close()
    tmp.replace(out)
    print(f"wrote {out} ({out.stat().st_size / 1e9:.1f} GB)")


if __name__ == "__main__":
    main()
