"""Build cleaned, pseudonymised thread documents from the FC SQLite dump.

    python -m fcdata.build --db data/fc_dataset_7-8.sqlite --out data/processed/threads.v0.jsonl \
        --sample-every 500 --sample-txt samples/v0_sample.txt

Each JSONL row has the thread body plus metadata; the conditioning header is built at
training time (so field dropout can be resampled per epoch).
"""

import argparse
import collections
import hashlib
import json
import random
import re
import sqlite3
from multiprocessing import Pool
from pathlib import Path

from .parse import parse_post, title_tags

LOW_EFFORT_KEEP_P = 0.35
LOW_EFFORT_MAX_CHARS = 15
OP_MEDIA_MIN_CHARS = 40

_PLACEHOLDER_RE = re.compile(r"\[cita=\x00(\d+)\x00\]")
_NON_CORE_RE = re.compile(r"\[cita[^\]]*\].*?\[/cita\]|\[(?:img|video[^\]]*|enlace[^\]]*|email|teléfono)\]|:[\w-]+:", re.S)


def core_text(text: str) -> str:
    """Text the poster actually wrote: no quotes, media, links or smilies."""
    return re.sub(r"\s+", " ", _NON_CORE_RE.sub(" ", text)).strip()


def alias(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return f"forero_{s}"


def keep_low_effort(post_id: int) -> bool:
    h = int(hashlib.md5(str(post_id).encode()).hexdigest()[:8], 16)
    return h / 0xFFFFFFFF < LOW_EFFORT_KEEP_P


def pages_bucket(n: int) -> str:
    for hi, label in ((1, "1"), (2, "2"), (5, "3-5"), (10, "6-10"), (30, "11-30")):
        if n <= hi:
            return label
    return "31+"


def page_name_re(names):
    names = sorted((n for n in names if n and len(n) >= 5), key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<!\w)(" + "|".join(map(re.escape, names)) + r")(?!\w)")


def build_thread(thread, comments, stats):
    tid, op_post_id, op_user, n_pages, title, op_html = thread
    op = parse_post(op_html)
    op_core = core_text(op.text)
    if len(op_core) < OP_MEDIA_MIN_CHARS and (op.n_img + op.n_video + op.n_link) > 0:
        stats["drop_thread_op_media"] += 1
        return None

    names = {op_user: "OP"}
    post_author = {op_post_id: op_user}

    def name_for(user):
        if user not in names:
            names[user] = alias(len(names) - 1)
        return names[user]

    posts = [(op_post_id, op_user, op)]
    for post_id, user, html in comments:
        post_author[post_id] = user
        posts.append((post_id, user, parse_post(html)))

    kept = []
    for i, (post_id, user, p) in enumerate(posts):
        if i > 0:
            core = core_text(p.text)
            if not p.text.strip():
                stats["drop_reply_empty"] += 1
                continue
            if not core and not re.search(r":[\w-]+:", p.text):
                stats["drop_reply_media_only"] += 1
                continue
            if len(core) <= LOW_EFFORT_MAX_CHARS and i > 1 and not keep_low_effort(post_id):
                stats["drop_reply_low_effort"] += 1
                continue
        kept.append((post_id, user, p))

    # Assign pseudonyms in reading order, including authors who only appear inside quotes.
    lines = []
    for n, (post_id, user, p) in enumerate(kept, start=1):
        who = name_for(user)

        def resolve(m, p=p):
            q_user, q_post = p.quotes[int(m.group(1))]
            author = post_author.get(q_post, q_user)
            return f"[cita={name_for(author)}]"

        text = _PLACEHOLDER_RE.sub(resolve, p.text)
        lines.append((n, who, text))

    name_re = page_name_re(names)
    body = [f"TÍTULO: {re.sub(r'\s+', ' ', title or '').strip()}"]
    for n, who, text in lines:
        if name_re:
            text = name_re.sub(lambda m: names[m.group(1)], text)
        body.append(f"#{n} {who}: {text}")

    stats["threads_kept"] += 1
    stats["replies_kept"] += len(kept) - 1
    doc = "\n\n".join(body)
    stats["chars"] += len(doc)
    return {
        "thread_id": tid,
        "n_pages": n_pages,
        "pages_bucket": pages_bucket(n_pages or 1),
        "tags": title_tags(title),
        "n_replies_raw": len(comments),
        "n_replies": len(kept) - 1,
        "op_chars": len(op_core),
        "text": doc,
    }


_DB = None


def _init(db_path):
    global _DB
    _DB = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _work(tids):
    stats = collections.Counter()
    out = []
    for tid in tids:
        thread = _DB.execute(
            "select id, post_id, username, n_pages, title, body from threads where id=?", (tid,)
        ).fetchone()
        comments = _DB.execute(
            "select post_id, username, body from comments where thread_id=? order by post_id", (tid,)
        ).fetchall()
        stats["threads_seen"] += 1
        stats["replies_seen"] += len(comments)
        try:
            row = build_thread(thread, comments, stats)
        except Exception as e:  # noqa: BLE001 - one bad thread shouldn't kill a 500k-thread run
            stats[f"error_{type(e).__name__}"] += 1
            continue
        if row:
            out.append(row)
    return out, stats


def ensure_index(db_path):
    con = sqlite3.connect(db_path)
    con.execute("create index if not exists idx_comments_thread on comments(thread_id, post_id)")
    con.commit()
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample-every", type=int, default=1, help="only threads with id %% N == 0")
    ap.add_argument("--sample-txt", help="also write N random threads as plain text for reading")
    ap.add_argument("--sample-n", type=int, default=50)
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()

    ensure_index(args.db)
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    tids = [r[0] for r in con.execute("select id from threads where id % ? = 0 order by id", (args.sample_every,))]
    chunks = [tids[i : i + 200] for i in range(0, len(tids), 200)]

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    stats = collections.Counter()
    rows = []
    with Pool(args.workers, initializer=_init, initargs=(args.db,)) as pool, open(args.out, "w") as f:
        for out, st in pool.imap_unordered(_work, chunks):
            stats.update(st)
            for row in out:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if args.sample_txt:
                rows.extend(out)

    stats["est_tokens_M"] = round(stats["chars"] / 3.5 / 1e6, 1)
    print(json.dumps(dict(sorted(stats.items())), indent=1))
    Path(args.out).with_suffix(".stats.json").write_text(json.dumps(stats, indent=1))

    if args.sample_txt:
        random.seed(0)
        Path(args.sample_txt).parent.mkdir(parents=True, exist_ok=True)
        pick = random.sample(rows, min(args.sample_n, len(rows)))
        with open(args.sample_txt, "w") as f:
            for r in pick:
                meta = f"thread {r['thread_id']} | páginas {r['n_pages']} | tags {' '.join(r['tags'])} | replies {r['n_replies']}/{r['n_replies_raw']}"
                f.write(f"{'=' * 100}\n{meta}\n{'=' * 100}\n{r['text']}\n\n")


if __name__ == "__main__":
    main()
