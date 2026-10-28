"""Write the intro and Archivo numbers as static JSON, so the page loads them without touching the 4 GB index.

    .venv/bin/python -m web.export_static            # after web.build_index or a training run
    .venv/bin/python -m web.export_static curves     # only the loss curves (scripts/vast_live_log.sh, every minute)

-> web/static/data/stats.json, web/static/data/curves.json
"""

import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data/web/index.sqlite"
CRAWL_ALL = ROOT / "data/processed/crawl-2026-all/docs.jsonl"
OUT = ROOT / "web/static/data"


def curves():
    """Loss histories for the intro: the newest checkpoint's trainer_state.json per run (final/ has none), or, for runs
    whose checkpoints were deleted (v0, v1), the trainer's log dicts in runs/<v>.log, placed by epoch fraction."""
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
                         "max_steps": max_steps, "tokens_per_step": 16 * 4096, "live": live_progress(run.name)}
    return out


LOG_DICT_RE = re.compile(r"\{'(?:loss|eval_loss)': '[^}]*\}")


def curves_from_log(name):
    """trainer_state-like history from the console logs (sparser: only what the terminal captured)."""
    logs = [ROOT / f"runs/{name}.log", ROOT / f"runs/restart_{name}.log", ROOT / f"runs/{name}.vast.log"]
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


def live_progress(name):
    """Last training bar of a run whose Vast log (copied every minute while it trains) changed in the last 5 min."""
    log = ROOT / f"runs/{name}.vast.log"
    if not log.exists() or time.time() - log.stat().st_mtime > 300 or (ROOT / f"runs/{name}/final").exists():
        return None
    with log.open("rb") as f:
        f.seek(max(0, log.stat().st_size - 20000))
        bars = re.findall(r"\| (\d+)/(\d+) \[[^<\]]*<([0-9:]+), *[0-9.]+s/it\]", f.read().decode(errors="replace"))
    if not bars:
        return None
    step, total, left = bars[-1]
    secs = sum(int(x) * 60 ** i for i, x in enumerate(reversed(left.split(":"))))
    return {"step": int(step), "max_steps": int(total), "eta": int(log.stat().st_mtime + secs)}


def stats():
    con = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    by = [dict(r) for r in con.execute(
        "SELECT coalesce(tipo, '?') tipo, coalesce(calidad, '?') calidad, count(*) n, sum(chars) chars "
        "FROM threads GROUP BY 1, 2")]
    years = [dict(r) for r in con.execute("SELECT year, source, count(*) n FROM threads GROUP BY 1, 2")]
    total = con.execute("SELECT count(*) n, sum(chars) chars, sum(n_posts) posts FROM threads").fetchone()
    con.close()
    # only fcdata.mix summaries ({tipo: {docs, mtok}, _total_mtok, ...}); mix_v2 writes a different shape
    mixes = {p.parent.name: s for p in sorted(ROOT.glob("data/mix/v*/summary.json"))
             if "_total_mtok" in (s := json.loads(p.read_text()))}
    mixes["v3"] = mix_v2_stats(ROOT / "data/mix/v3/summary.json")
    return {"total": dict(total), "by_tipo_calidad": by, "years": years,
            "crawl_years": crawl_years(), "mixes": mixes}


def crawl_years():
    """Threads per OP year in the whole 2026 crawl: the index files 2019–2020 ones under the archive, which has them too."""
    with CRAWL_ALL.open() as f:
        c = Counter(json.loads(line)["year"] for line in f)
    return {y: c[y] for y in sorted(y for y in c if y)}


MIX_V2_POOLS = {"stories": "historias (×2)", "conversation": "hilos del crawl 2026", "archive_conversation": "hilos del archivo",
                "replay": "español general"}


def mix_v2_stats(path):
    """fcdata.mix_v2's summary in the shape the page draws for fcdata.mix ({pool: {mtok}}, _total_mtok, ...)."""
    s = json.loads(path.read_text())
    out = {MIX_V2_POOLS.get(k, k): {"mtok": round(v / 1e6, 1)} for k, v in s["train"]["completion_tokens"].items()}
    val = sum(n for k, n in s["val"]["rows"].items() if k != "replay")
    return out | {"_total_mtok": round(s["train"]["total_completion_tokens"] / 1e6, 1), "_val_threads": val}


def write(name, data):
    OUT.mkdir(exist_ok=True)
    tmp = OUT / f".{name}.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(OUT / f"{name}.json")


if __name__ == "__main__":
    for name in sys.argv[1:] or ["stats", "curves"]:
        write(name, globals()[name]())
