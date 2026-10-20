"""Label thread type (`tipo`) and OP quality with Claude Haiku via the Message Batches API.

    set -a; . ./.env; set +a
    .venv/bin/python -m fcdata.label submit --threads data/processed/threads.v0.jsonl --name pilot --sample 300
    .venv/bin/python -m fcdata.label status
    .venv/bin/python -m fcdata.label collect --name pilot

State (batch ids) lives in data/labels/batches.json; labels in data/labels/<name>.jsonl.
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

MODEL = "claude-haiku-5-5"
# Batch prices, $/MTok (50% of $0.10 / $0.50 for prompts <= 100K tokens).
PRICE_IN, PRICE_OUT = 0.05, 0.25
# ~5 KB per request (system + excerpt); 20k keeps each batch well under the 256 MB limit.
BATCH_SIZE = 20_000
OP_MAX_CHARS = 1500
REPLY_MAX_CHARS = 150
N_REPLIES = 3

LABEL_DIR = Path("data/labels")
STATE = LABEL_DIR / "batches.json"

TIPOS = ["historia", "pregunta", "debate", "hipotetico", "noticia", "humor", "compraventa", "otro"]
CALIDAD = ["baja", "media", "alta"]

SYSTEM = """Clasificas hilos de ForoCoches (foro español). Recibes el título, el primer mensaje (OP) y las primeras respuestas.

tipo, según la intención del OP:
- historia: el OP narra algo que le pasó a él o a alguien cercano (anécdota, experiencia, "os cuento"), real o inventado.
- pregunta: pide ayuda, consejo, recomendación o un dato concreto.
- debate: plantea un tema, opinión, queja o reflexión para discutir.
- hipotetico: escenario hipotético, encuesta o juego ("qué haríais si...", "os dan 1M pero...").
- noticia: comparte o pega una noticia o contenido externo, con o sin comentario.
- humor: broma, troll, meme o hilo de coña sin intención seria.
- compraventa: venta, referidos, sorteos, spam o autopromoción.
- otro: nada de lo anterior.

calidad, como ejemplo del estilo ForoCoches escrito por el propio OP:
- alta: texto propio elaborado, con gracia, personalidad o una buena historia que engancha.
- media: correcto pero normal.
- baja: mínimo, genérico, copiado o casi vacío.

Si el hilo es ofensivo, clasifícalo igual: solo etiquetas, no juzgas."""

SCHEMA = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": TIPOS},
        "calidad": {"type": "string", "enum": CALIDAD},
    },
    "required": ["tipo", "calidad"],
    "additionalProperties": False,
}

_POST_RE = re.compile(r"\n\n(?=#\d+ (?:OP|forero_[a-z]+): )")


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def prompt_for(row: dict) -> str:
    parts = _POST_RE.split(row["text"])
    title, op, replies = parts[0], parts[1] if len(parts) > 1 else "", parts[2 : 2 + N_REPLIES]
    out = [title, _clip(op, OP_MAX_CHARS)]
    out += [_clip(r, REPLY_MAX_CHARS) for r in replies]
    return "\n\n".join(out)


def make_request(row: dict) -> Request:
    return Request(
        custom_id=f"t{row['thread_id']}",
        params=MessageCreateParamsNonStreaming(
            model=MODEL,
            max_tokens=64,
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            # Identical across requests and >512 tokens, so batch requests can hit the cache
            # (best effort; reads bill at 0.1x).
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            messages=[{"role": "user", "content": prompt_for(row)}],
        ),
    )


def load_state() -> list:
    return json.loads(STATE.read_text()) if STATE.exists() else []


def save_state(state: list):
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def submitted_ids() -> set:
    """Threads already labelled or sitting in a submitted batch (so we never pay twice)."""
    done = set()
    for p in LABEL_DIR.glob("*.jsonl"):
        with p.open() as source:
            for row in map(json.loads, source):
                if row.get('tipo') in TIPOS and row.get('calidad') in CALIDAD:
                    done.add(row['thread_id'])
    for p in LABEL_DIR.glob("msgbatch_*.ids.json"):
        done |= set(json.loads(p.read_text()))
    return done


def cmd_submit(args):
    rows = [json.loads(l) for l in open(args.threads)]
    if args.ids:
        # Explicit retry list (e.g. <name>.failed.json): resubmit exactly these.
        want = set(json.loads(Path(args.ids).read_text()))
        rows = [r for r in rows if r["thread_id"] in want]
    else:
        done = submitted_ids()
        rows = [r for r in rows if r["thread_id"] not in done]
    if args.sample:
        random.seed(args.seed)
        # Half the pilot from long OPs, where historias live; half uniform.
        long = [r for r in rows if r["op_chars"] >= 500]
        rows = random.sample(long, args.sample // 2) + random.sample(rows, args.sample - args.sample // 2)
        rows = list({r["thread_id"]: r for r in rows}.values())

    prompts_chars = sum(len(prompt_for(r)) for r in rows) + len(SYSTEM) * len(rows)
    # ~3 chars/token for Spanish on the current tokenizer; pessimistic on purpose.
    est_in = prompts_chars / 3 / 1e6
    est_out = 20 * len(rows) / 1e6
    est = est_in * PRICE_IN + est_out * PRICE_OUT
    print(f"{len(rows)} requests, ~{est_in:.1f}M input tokens, est. cost ${est:.2f}")
    if est > args.max_usd:
        sys.exit(f"Estimated cost above --max-usd {args.max_usd}; re-run with a higher limit to proceed.")

    client = anthropic.Anthropic()
    state = load_state()
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        try:
            batch = client.messages.batches.create(requests=[make_request(r) for r in chunk])
        except anthropic.RateLimitError:
            # Cap on requests "in processing" across batches: wait for some to end, then re-run.
            print(f"rate limited after {i} requests; re-run submit once running batches end")
            return
        (LABEL_DIR / f"{batch.id}.ids.json").write_text(json.dumps([r["thread_id"] for r in chunk]))
        provenance = {str(r['thread_id']): {
            'text_sha256': hashlib.sha256(r['text'].encode()).hexdigest(),
            'prompt_sha256': hashlib.sha256((SYSTEM + prompt_for(r)).encode()).hexdigest(),
        } for r in chunk}
        (LABEL_DIR / f'{batch.id}.provenance.json').write_text(json.dumps(provenance))
        state.append({"id": batch.id, "name": args.name, "n": len(chunk), "created": time.time(),
                      "model": MODEL, "op_max_chars": OP_MAX_CHARS,
                      "system_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest()})
        save_state(state)
        print(f"submitted {batch.id} ({len(chunk)} requests)")


def cmd_status(args):
    client = anthropic.Anthropic()
    for b in load_state():
        if args.name and b["name"] != args.name:
            continue
        r = client.messages.batches.retrieve(b["id"])
        c = r.request_counts
        print(f"{b['name']:10} {b['id']} {r.processing_status:12} ok={c.succeeded} err={c.errored} "
              f"proc={c.processing} exp={c.expired}")


def cmd_collect(args):
    client = anthropic.Anthropic()
    out_path = LABEL_DIR / f"{args.name}.jsonl"
    seen = set()
    if out_path.exists():
        with out_path.open() as source:
            seen = {json.loads(line)['thread_id'] for line in source}
    tok_in = tok_out = tok_cr = tok_cw = n_ok = n_bad = 0
    failed = []
    with open(out_path, "a") as f:
        for b in load_state():
            if b["name"] != args.name:
                continue
            if client.messages.batches.retrieve(b["id"]).processing_status != "ended":
                print(f"{b['id']} not ended yet, skipping")
                continue
            ids_file = LABEL_DIR / f"{b['id']}.ids.json"
            provenance_file = LABEL_DIR / f"{b['id']}.provenance.json"
            provenance = json.loads(provenance_file.read_text()) if provenance_file.exists() else {}
            expected = set(json.loads(ids_file.read_text())) if ids_file.exists() else None
            for res in client.messages.batches.results(b["id"]):
                tid = int(res.custom_id[1:])
                if expected is not None and tid not in expected:
                    raise ValueError(f"Unexpected result t{tid} in batch {b['id']}")
                if res.result.type != "succeeded":
                    failed.append(tid)
                    continue
                msg = res.result.message
                tok_in += msg.usage.input_tokens
                tok_out += msg.usage.output_tokens
                tok_cr += msg.usage.cache_read_input_tokens or 0
                tok_cw += msg.usage.cache_creation_input_tokens or 0
                if tid in seen:
                    continue
                if msg.stop_reason == 'refusal':
                    f.write(json.dumps(dict(thread_id=tid, decision='exclude', reason='classifier_refusal',
                                            **provenance.get(str(tid), {}), model=msg.model, batch_id=b['id']))+'\n')
                    seen.add(tid)
                    n_ok += 1
                    continue
                text = next((blk.text for blk in msg.content if blk.type == "text"), "")
                try:
                    lab = json.loads(text)
                except json.JSONDecodeError:
                    n_bad += 1
                    failed.append(tid)
                    continue
                if not isinstance(lab, dict) or lab.get('tipo') not in TIPOS or lab.get('calidad') not in CALIDAD:
                    n_bad += 1
                    failed.append(tid)
                    continue
                f.write(json.dumps({"thread_id": tid, 'tipo': lab['tipo'], 'calidad': lab['calidad'],
                                    **provenance.get(str(tid), {}), 'model': msg.model, 'batch_id': b['id']}) + "\n")
                seen.add(tid)
                n_ok += 1
    # 1h cache writes bill at 2x input, reads at 0.1x.
    cost = (tok_in + 2 * tok_cw + 0.1 * tok_cr) / 1e6 * PRICE_IN + tok_out / 1e6 * PRICE_OUT
    print(f"wrote {n_ok} review results to {out_path}; unparseable {n_bad}; failed/expired {len(failed)}")
    print(f"usage: {tok_in:,} in, {tok_cr:,} cache-read, {tok_cw:,} cache-write, {tok_out:,} out -> ${cost:.3f}")
    (LABEL_DIR / f"{args.name}.failed.json").write_text(json.dumps(sorted(set(failed)-seen)))


def main():
    global LABEL_DIR, STATE, OP_MAX_CHARS, SYSTEM
    ap = argparse.ArgumentParser()
    ap.add_argument('--label-dir', type=Path, default=LABEL_DIR)
    ap.add_argument('--op-max-chars', type=int, default=OP_MAX_CHARS)
    ap.add_argument('--audit-stories', action='store_true')
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--threads", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--sample", type=int)
    s.add_argument("--ids", help="JSON list of thread ids to (re)submit, ignoring what's already submitted")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--max-usd", type=float, default=1.0)
    st = sub.add_parser("status")
    st.add_argument("--name")
    c = sub.add_parser("collect")
    c.add_argument("--name", required=True)
    args = ap.parse_args()
    LABEL_DIR = args.label_dir
    STATE = LABEL_DIR / 'batches.json'
    OP_MAX_CHARS = args.op_max_chars
    if args.audit_stories:
        SYSTEM += '\nUna explicación histórica, guía de lugares o artículo pegado NO es historia personal aunque sea largo. Las citas y respuestas no prueban que el OP narre una experiencia propia. Evalúa la calidad del texto propio, no su longitud ni la de las citas.'
    {"submit": cmd_submit, "status": cmd_status, "collect": cmd_collect}[args.cmd](args)


if __name__ == "__main__":
    main()
