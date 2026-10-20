"""Fill the intro's Trending with whole threads written by a model version, through the web's own API.

    .venv-train/bin/python scripts/seed_generated.py --model v1 --n 6 --posts 8

Runs against the local server (Tailscale mode, server GPU), then shares each thread like a visitor would.
"""
import argparse
import json
import random
import sys
import urllib.request

API = "http://127.0.0.1:7862"
TIPOS = ["historia", "historia", "humor", "hipotetico", "debate", "pregunta"]


def sse(body):
    req = urllib.request.Request(f"{API}/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
    last = None
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            line = line.decode().strip()
            if line.startswith("data: "):
                last = json.loads(line[6:])
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="v1")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--posts", type=int, default=8)
    a = ap.parse_args()
    sampling = {"temperature": 0.8, "top_p": 0.95, "repetition_penalty": 1.1, "max_new_tokens": 300}
    for i in range(a.n):
        tipo = TIPOS[i % len(TIPOS)]
        header = {"tipo": tipo, "calidad": "alta" if tipo == "historia" else "", "etiquetas": "", "paginas": ""}
        title = sse({"model": a.model, "kind": "title", "header": header, "sampling": sampling})["title"]
        posts = []
        while len(posts) < a.posts:
            ev = sse({"model": a.model, "kind": "post", "who": "auto", "header": header, "title": title, "posts": posts, "sampling": sampling})
            if not ev or not ev.get("body"):
                break
            posts.append([ev["author"], ev["body"]])
        print(f"[{i + 1}/{a.n}] {tipo}: {title!r} ({len(posts)} posts)", flush=True)
        if len(posts) < 2:
            continue
        req = urllib.request.Request(f"{API}/api/generated", json.dumps({"model": a.model, "tipo": tipo, "title": title, "posts": posts}).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()


if __name__ == "__main__":
    main()
