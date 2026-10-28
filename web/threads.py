"""The thread text format shared by the dataset, the model and the web.

    tipo: historia | calidad: alta | etiquetas: +hd | páginas: 2    <- optional conditioning header

    TÍTULO: ...

    #1 OP: ...

    #2 forero_a: [cita=OP]...[/cita]
    ...
"""

import re
import string

TIPOS = ["historia", "pregunta", "debate", "hipotetico", "noticia", "humor", "compraventa", "otro"]
CALIDADES = ["alta", "media", "baja"]
PAGINAS = ["1", "2", "3-5", "6-10", "11-30", "31+"]
MAX_PROMPT_CHARS = 12_000  # ~3.5k tokens; older replies are dropped from the prompt, the OP is kept

POST_SPLIT = re.compile(r"\n\n(?=#\d+ (?:OP|forero_[a-z]+): )")
POST_RE = re.compile(r"^#(\d+) (OP|forero_[a-z]+): ?(.*)$", re.S)
AUTHOR_RE = re.compile(r"^\s*(OP|forero_[a-z]+):\s?")


def header(tipo="", calidad="", anio="", etiquetas="", paginas=""):
    fields = [("tipo", tipo), ("calidad", calidad), ("año", anio), ("etiquetas", etiquetas.strip()), ("páginas", paginas)]
    kept = [f"{k}: {v}" for k, v in fields if v]
    return " | ".join(kept) + "\n\n" if kept else ""


def parse_thread(text):
    """'TÍTULO: ...\\n\\n#1 OP: ...' -> (title, [(author, body), ...])."""
    parts = POST_SPLIT.split(text)
    title = parts[0][len("TÍTULO: "):] if parts[0].startswith("TÍTULO: ") else parts[0]
    posts = []
    for p in parts[1:]:
        m = POST_RE.match(p)
        if m:
            posts.append((m.group(2), m.group(3)))
    return title.strip(), posts


def thread_text(title, posts, trim=True):
    lines = [f"#{i} {a}: {t}" for i, (a, t) in enumerate(posts, 1)]
    if trim:
        while len(lines) > 1 and sum(len(l) + 2 for l in lines) + len(title) > MAX_PROMPT_CHARS:
            del lines[1]
    return "\n\n".join([f"TÍTULO: {title}"] + lines)


def next_forero(posts):
    used = {a for a, _ in posts}
    for c in string.ascii_lowercase:
        if f"forero_{c}" not in used:
            return f"forero_{c}"
    return "forero_z"
