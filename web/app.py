"""forocoches-lm web: play with the model versions and browse the dataset.

    .venv-train/bin/python -m uvicorn web.app:app --host 0.0.0.0 --port 7862

Models are loaded on first use and unloaded after IDLE_UNLOAD_S without requests, so the GPU is
free for the other services (and training) when nobody is playing. Dataset reads go to
data/web/index.sqlite (web/build_index.py).
"""

import json
import re
import sqlite3
from collections import Counter
import threading
import time
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, StoppingCriteria, TextIteratorStreamer

from web.threads import AUTHOR_RE, CALIDADES, PAGINAS, TIPOS, header, next_forero, parse_thread, thread_text

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data/web/index.sqlite"
BASE_MODEL = "unsloth/gemma-3-1b-pt"
IDLE_UNLOAD_S = 10 * 60
MAX_LOADED = 2
# Browser weights published for the public site (forocoches.m19182.dev, through a Cloudflare tunnel). Public
# visitors only get these: the server GPU is reachable from Tailscale alone.
HF_REPOS = {"v1": "mateo-19182/forocoches-lm-v1-q4f16_1-MLC", "v2": "mateo-19182/forocoches-lm-v2-q4f16_1-MLC"}
# header fields each version was trained with; v2's mix was built with `--fields tipo`
ALL_FIELDS = ("tipo", "calidad", "etiquetas", "paginas")
HEADER_FIELDS = {"v2": ("tipo",)}


def header_fields(version):
    return HEADER_FIELDS.get(version, ALL_FIELDS)

app = FastAPI(title="forocoches-lm")


# ---------------------------------------------------------------- models

def versions():
    out = {"base": BASE_MODEL}
    for d in sorted(ROOT.glob("runs/*/final")):
        if (d / "config.json").exists() and d.parent.name not in ("smoke",):
            out[d.parent.name] = str(d)
    return out


class Store:
    """Loaded models by version name; one generation at a time on the GPU."""

    def __init__(self):
        self.loaded = {}  # name -> (tok, model, last_used)
        self.lock = threading.Lock()
        threading.Thread(target=self._reaper, daemon=True).start()

    def get(self, name):
        path = versions().get(name)
        if not path:
            raise HTTPException(404, f"unknown model version {name!r}")
        with self.lock:
            if name not in self.loaded:
                while len(self.loaded) >= MAX_LOADED:
                    self._unload(min(self.loaded, key=lambda k: self.loaded[k][2]))
                tok = AutoTokenizer.from_pretrained(path)
                model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16).cuda().eval()
                self.loaded[name] = [tok, model, time.time()]
            entry = self.loaded[name]
            entry[2] = time.time()
            return entry[0], entry[1]

    def _unload(self, name):
        del self.loaded[name]
        torch.cuda.empty_cache()

    def _reaper(self):
        while True:
            time.sleep(60)
            with self.lock:
                for name in [k for k, v in self.loaded.items() if time.time() - v[2] > IDLE_UNLOAD_S]:
                    self._unload(name)

    def status(self):
        return {k: round(time.time() - v[2]) for k, v in self.loaded.items()}


store = Store()


class Header(BaseModel):
    tipo: str = ""
    calidad: str = ""
    etiquetas: str = ""
    paginas: str = ""


class Sampling(BaseModel):
    temperature: float = 0.8
    top_p: float = 0.95
    repetition_penalty: float = 1.1
    max_new_tokens: int = 300


class GenerateReq(BaseModel):
    model: str = "v0"
    kind: str = "post"  # "title" | "post"
    who: str = "auto"  # for posts: "auto" | "OP" | "forero"
    header: Header = Header()
    title: str = ""
    posts: list[tuple[str, str]] = []
    sampling: Sampling = Sampling()


class Cancel(StoppingCriteria):
    def __init__(self):
        self.stop = threading.Event()

    def __call__(self, input_ids, scores, **kw):
        return self.stop.is_set()


def sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def generate(req: GenerateReq):
    tok, model = store.get(req.model)
    hdr = header(**{k: v for k, v in req.header.model_dump().items() if k in header_fields(req.model)})
    posts = list(req.posts)
    author = None  # None: the model picks who speaks
    if req.kind == "title":
        prefix, stop = hdr + "TÍTULO:", "\n\n"
    else:
        who = "OP" if not posts else req.who
        n = len(posts) + 1
        if who == "auto":
            # no trailing space: " forero_a" is one token in training, "#2 " + "forero_a" is not what the model saw
            prefix = f"{hdr}{thread_text(req.title, posts)}\n\n#{n}"
        else:
            author = "OP" if who == "OP" else next_forero(posts)
            prefix = f"{hdr}{thread_text(req.title, posts)}\n\n#{n} {author}:"
        stop = "\n\n#"

    cancel = Cancel()
    with store.lock:  # the GPU does one generation at a time
        ids = tok(prefix, return_tensors="pt").to("cuda")
        streamer = TextIteratorStreamer(tok, skip_prompt=True, skip_special_tokens=True)
        s = req.sampling
        kw = dict(**ids, streamer=streamer, do_sample=True, temperature=s.temperature, top_p=s.top_p,
                  repetition_penalty=s.repetition_penalty, max_new_tokens=min(s.max_new_tokens, 1500),
                  stop_strings=[stop], tokenizer=tok, stopping_criteria=[cancel])
        worker = threading.Thread(target=model.generate, kwargs=kw, daemon=True)
        worker.start()
        text = ""
        try:
            for piece in streamer:
                text += piece
                if stop in text:
                    text = text.split(stop)[0]
                    yield sse({"text": text})
                    break
                yield sse({"text": text})
        finally:
            cancel.stop.set()  # client went away or we are done: stop the GPU work
            worker.join()
    text = text.strip()
    if req.kind == "title":
        yield sse({"done": True, "title": text})
    else:
        if author is None:
            m = AUTHOR_RE.match(text)
            author, text = (m.group(1), text[m.end():]) if m else (next_forero(posts), text)
        yield sse({"done": True, "author": author, "body": text.strip()})


def is_public(request: Request):
    """Requests through the Cloudflare tunnel carry cf-connecting-ip; Tailscale requests do not."""
    return "cf-connecting-ip" in request.headers


@app.post("/api/generate")
def api_generate(req: GenerateReq, request: Request):
    if is_public(request):
        raise HTTPException(403, "en la web pública la generación corre en tu navegador")
    return StreamingResponse(generate(req), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/models")
def api_models(request: Request):
    public = is_public(request)
    return {"public": public, "versions": [] if public else list(versions()), "loaded": {} if public else store.status(),
            "webgpu": webgpu_versions(), "hf": HF_REPOS, "fields": HEADER_FIELDS,
            "tipos": TIPOS, "calidades": CALIDADES, "paginas": PAGINAS}


_curves = {"at": 0, "data": None}


@app.get("/api/curves")
def api_curves():
    """Loss histories for the intro: the newest checkpoint's trainer_state.json per run (final/ has none), or, for runs
    whose checkpoints were deleted (v0, v1), the trainer's log dicts in runs/<v>.log, placed by epoch fraction."""
    if time.time() - _curves["at"] > 600:
        out = {}
        for run in sorted(ROOT.glob("runs/v*/")):
            cks = sorted(run.glob("checkpoint-*/trainer_state.json"), key=lambda p: int(p.parent.name.split("-")[1]))
            if cks:
                st = json.loads(cks[-1].read_text())
                h, max_steps = st["log_history"], st.get("max_steps")
            else:
                h, max_steps = curves_from_log(run.name)
            if not h:
                continue
            out[run.name] = {"train": [[x["step"], round(x["loss"], 4)] for x in h if "loss" in x],
                             "eval": [[x["step"], round(x["eval_loss"], 4)] for x in h if "eval_loss" in x],
                             "max_steps": max_steps, "tokens_per_step": 16 * 4096}
        _curves.update(at=time.time(), data=out)
    return _curves["data"]


LOG_DICT_RE = re.compile(r"\{'(?:loss|eval_loss)': '[^}]*\}")


def curves_from_log(name):
    """trainer_state-like history from the console logs (sparser: only what the terminal captured)."""
    logs = [ROOT / f"runs/{name}.log", ROOT / f"runs/restart_{name}.log"]
    text = "\n".join(p.read_text(errors="replace").replace("\r", "\n") for p in logs if p.exists())
    # the training bar ("| 123/4219 [... 12.3s/it]") runs at seconds per step; dataset map and eval bars at it/s
    m = re.findall(r"\| \d+/(\d+) \[[^\]]*s/it\]", text)
    if not m:
        return [], None
    max_steps = int(Counter(m).most_common(1)[0][0])
    h = []
    for d in LOG_DICT_RE.findall(text):
        d = {k: float(v) for k, v in re.findall(r"'(\w+)': '([-0-9.e]+)'", d)}
        d["step"] = round(d.pop("epoch", 0) * max_steps)
        h.append(d)
    h.sort(key=lambda x: x["step"])
    return h, max_steps


# ---------------------------------------------------------------- in-browser generation (WebLLM)
MLC_DIR = ROOT / "data/web/mlc"


def webgpu_versions():
    """Versions converted to q4f16 MLC weights by scripts/export_webllm.py, served to WebLLM in the browser."""
    return sorted(p.parent.name for p in MLC_DIR.glob("*/tensor-cache.json"))


@app.get("/mlc/{version}/resolve/main/{path:path}")
def mlc_file(version: str, path: str):
    # WebLLM appends resolve/main/ to every model URL (it expects a Hugging Face repo)
    f = (MLC_DIR / version / path).resolve()
    if not f.is_relative_to(MLC_DIR) or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ---------------------------------------------------------------- dataset

def db():
    con = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def where(tipo, calidad):
    conds, params = [], []
    if tipo:
        conds.append("t.tipo = ?")
        params.append(tipo)
    if calidad:
        conds.append("t.calidad = ?")
        params.append(calidad)
    return conds, params


@app.get("/api/threads")
def api_threads(tipo: str = "", calidad: str = "", q: str = "", limit: int = 24, offset: int = 0):
    con = db()
    cols = "t.thread_id, t.tipo, t.calidad, t.title, t.n_posts, t.chars, t.pages_bucket"
    conds, params = where(tipo, calidad)
    if q.strip():
        # FTS prefix match on every word of the query
        match = " ".join(f'"{w}"*' for w in q.replace('"', "").split())
        sql = f"SELECT {cols} FROM titles JOIN threads t ON t.thread_id = titles.rowid WHERE titles MATCH ?"
        params = [match] + params
        sql += "".join(f" AND {c}" for c in conds) + " ORDER BY rank LIMIT ? OFFSET ?"
    else:
        sql = f"SELECT {cols} FROM threads t"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY random() LIMIT ? OFFSET ?"
    rows = con.execute(sql, params + [min(limit, 100), offset]).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/api/threads/{thread_id}")
def api_thread(thread_id: int):
    con = db()
    r = con.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404)
    title, posts = parse_thread(r["text"])
    return {"thread_id": r["thread_id"], "tipo": r["tipo"], "calidad": r["calidad"], "title": title,
            "posts": posts, "pages_bucket": r["pages_bucket"], "tags": r["tags"], "chars": r["chars"]}


_stats = None


@app.get("/api/stats")
def api_stats():
    global _stats
    if _stats is None:
        con = db()
        by = [dict(r) for r in con.execute(
            "SELECT coalesce(tipo, '?') tipo, coalesce(calidad, '?') calidad, count(*) n, sum(chars) chars "
            "FROM threads GROUP BY 1, 2")]
        hist = [dict(r) for r in con.execute(
            "SELECT coalesce(tipo, '?') tipo, min(n_posts, 60) n_posts, count(*) n FROM threads GROUP BY 1, 2")]
        pages = [dict(r) for r in con.execute("SELECT pages_bucket, count(*) n FROM threads GROUP BY 1")]
        total = con.execute("SELECT count(*) n, sum(chars) chars, sum(n_posts) posts FROM threads").fetchone()
        con.close()
        # only fcdata.mix summaries ({tipo: {docs, mtok}, _total_mtok, ...}); mix_v2 writes a different shape
        mixes = {p.parent.name: s for p in sorted(ROOT.glob("data/mix/v*/summary.json"))
                 if "_total_mtok" in (s := json.loads(p.read_text()))}
        _stats = {"total": dict(total), "by_tipo_calidad": by, "posts_hist": hist, "pages": pages, "mixes": mixes}
    return _stats


# ---------------------------------------------------------------- threads generated by visitors, shared to the intro's Trending

GEN_DB = ROOT / "data/web/generated.sqlite"
_gen_lock = threading.Lock()
_gen_rate = {}  # client -> share timestamps in the last hour


def gen_db():
    con = sqlite3.connect(GEN_DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS generated (id INTEGER PRIMARY KEY, created REAL, model TEXT, tipo TEXT, title TEXT, posts TEXT)")
    return con


class GeneratedReq(BaseModel):
    model: str = ""
    tipo: str = ""
    title: str
    posts: list[tuple[str, str]]


@app.post("/api/generated")
def api_generated_add(req: GeneratedReq, request: Request):
    client = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    now = time.time()
    with _gen_lock:  # no accounts: a per-client hourly cap is the only brake
        ts = [t for t in _gen_rate.get(client, []) if now - t < 3600]
        if len(ts) >= 20:
            raise HTTPException(429, "demasiados hilos compartidos en una hora")
        ts.append(now)
        _gen_rate[client] = ts
    title = req.title.strip()[:200]
    posts = [(a, t.strip()[:4000]) for a, t in req.posts[:30] if t.strip() and AUTHOR_RE.match(a + ":")]
    if not title or len(posts) < 2:
        raise HTTPException(400, "hace falta un título y al menos dos posts")
    con = gen_db()
    cur = con.execute("INSERT INTO generated VALUES (NULL, ?, ?, ?, ?, ?)",
                      (now, req.model[:20], req.tipo[:20], title, json.dumps(posts, ensure_ascii=False)))
    con.commit()
    gid = cur.lastrowid
    con.close()
    return {"id": gid}


@app.get("/api/generated")
def api_generated(limit: int = 5):
    con = gen_db()
    rows = con.execute("SELECT id, model, tipo, title, posts FROM generated ORDER BY random() LIMIT ?", (min(limit, 50),)).fetchall()
    con.close()
    return [{"id": r["id"], "model": r["model"], "tipo": r["tipo"], "title": r["title"], "n_posts": len(json.loads(r["posts"]))} for r in rows]


@app.get("/api/generated/{gid}")
def api_generated_one(gid: int):
    con = gen_db()
    r = con.execute("SELECT * FROM generated WHERE id = ?", (gid,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404)
    return {"id": r["id"], "model": r["model"], "tipo": r["tipo"], "title": r["title"], "posts": json.loads(r["posts"])}


# ---------------------------------------------------------------- static

app.mount("/static", StaticFiles(directory=ROOT / "web/static"), name="static")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(ROOT / "web/static/favicon-32.png", media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/")
def index():
    # Cloudflare caches /static/*.js and *.css by extension; stamping the URLs with the file mtime busts that on deploy
    html = (ROOT / "web/static/index.html").read_text()
    for name in ("style.css", "app.js"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((ROOT / 'web/static' / name).stat().st_mtime)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
