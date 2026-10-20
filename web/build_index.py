"""Build the SQLite index the web reads: every processed thread with its label, title and length,
plus an FTS5 index on titles.

    .venv-train/bin/python web/build_index.py            # -> data/web/index.sqlite
    .venv-train/bin/python web/build_index.py --threads data/processed/threads.v0.jsonl --labels data/labels/full.jsonl
"""

import argparse
import json
import sqlite3
from pathlib import Path

from web.threads import parse_thread

SCHEMA = """
CREATE TABLE threads (
    thread_id INTEGER PRIMARY KEY,
    tipo TEXT, calidad TEXT, title TEXT,
    n_posts INTEGER, chars INTEGER, pages_bucket TEXT, tags TEXT,
    text TEXT
);
CREATE INDEX idx_tipo_calidad ON threads(tipo, calidad);
CREATE VIRTUAL TABLE titles USING fts5(title, content='threads', content_rowid='thread_id', tokenize='unicode61 remove_diacritics 2');
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", default="data/processed/threads.v0.jsonl")
    ap.add_argument("--labels", default="data/labels/full.jsonl")
    ap.add_argument("--out", default="data/web/index.sqlite")
    args = ap.parse_args()

    labels = {}
    with open(args.labels) as f:
        for line in f:
            d = json.loads(line)
            labels[d["thread_id"]] = (d["tipo"], d["calidad"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)

    n = 0
    with open(args.threads) as f:
        batch = []
        for line in f:
            row = json.loads(line)
            tipo, calidad = labels.get(row["thread_id"], (None, None))
            title, posts = parse_thread(row["text"])
            batch.append((row["thread_id"], tipo, calidad, title, len(posts), len(row["text"]),
                          row["pages_bucket"], " ".join(row["tags"]), row["text"]))
            if len(batch) >= 5000:
                db.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)", batch)
                n += len(batch)
                batch = []
                print(f"\r{n:,} threads", end="", flush=True)
        if batch:
            db.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)", batch)
            n += len(batch)
    print(f"\r{n:,} threads; building title index...")
    db.execute("INSERT INTO titles(titles) VALUES ('rebuild')")
    db.commit()
    db.close()
    tmp.replace(out)
    print(f"wrote {out} ({out.stat().st_size / 1e9:.1f} GB)")


if __name__ == "__main__":
    main()
