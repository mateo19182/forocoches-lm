"""Turn one scraped ForoCoches post cell (vBulletin <td> HTML) into plain text.

Output conventions (the model sees these literally):
  [cita=<user>]...[/cita]   quote of another post (author resolved to a pseudonym in build.py)
  [cita]...[/cita]          quote with no author (usually pasted news)
  [spoiler]...[/spoiler]
  [img] [video youtube] [enlace <domain>] [email] [teléfono]
  :qmeparto:                FC smilie, by its image filename
"""

import re
from dataclasses import dataclass, field
from urllib.parse import unquote

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

QUOTE_MAX_CHARS = 200

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+34[ .-]?)?[6789]\d{2}[ .-]?\d{3}[ .-]?\d{3}(?!\d)")
_SMILIE_RE = re.compile(r"/smilies/([\w-]+)\.\w+")
_VIDEO_RE = re.compile(r"verVideo\('([^']+)'")
_QUOTED_POST_RE = re.compile(r"showthread\.php\?p=(\d+)")


@dataclass
class Post:
    text: str
    n_img: int = 0
    n_video: int = 0
    n_link: int = 0
    # (quoted username or None, quoted post_id or None), in order of appearance
    quotes: list = field(default_factory=list)


_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def _domain(url: str) -> str:
    # FC routes external links through link.php?url=<encoded target>.
    m = re.search(r"link\.php\?url=([^&\s]+)", url)
    if m:
        url = unquote(m.group(1))
    # Users paste junk like "https://Product title https://amazon.es/..."; take the last URL.
    urls = re.findall(r"(?:https?:)?//[^\s\"']+", url)
    if urls:
        url = urls[-1]
    d = re.sub(r"^(https?:)?//", "", url).split("/")[0].split("?")[0].lower()
    d = re.sub(r"^(www|m|mobile)\.", "", d)
    if not _HOST_RE.match(d):
        return "web"
    if d.endswith("forocoches.com"):
        return "forocoches"
    if d in ("youtu.be",):
        return "youtube"
    return d.rsplit(".", 1)[0] if d.count(".") == 1 else d


class _Renderer:
    def __init__(self):
        self.post = Post(text="")

    def render(self, node, in_quote=False) -> str:
        # User HTML can nest hundreds of formatting tags; keep traversal off the call stack.
        frames = [self._render(node, in_quote)]
        result = None
        while frames:
            try:
                child = frames[-1].send(result)
            except StopIteration as finished:
                frames.pop()
                result = finished.value
            else:
                frames.append(child)
                result = None
        return result

    def _render(self, node, in_quote):
        out = []
        for child in node.children:
            out.append((yield self._node(child, in_quote)))
        return "".join(out)

    def _node(self, n, in_quote) -> str:
        if isinstance(n, Comment):
            return ""
        if isinstance(n, NavigableString):
            # The scrape is pretty-printed: newlines in text nodes are layout, only <br> is real.
            return re.sub(r"\s+", " ", str(n))
        if not isinstance(n, Tag):
            return ""

        name = n.name
        cls = " ".join(n.get("class", []))
        style = n.get("style", "")

        if name == "br":
            return "\n"
        if name == "script":
            m = _VIDEO_RE.search(n.get_text())
            if m:
                self.post.n_video += 1
                return " [video youtube] "
            return ""
        if name == "img":
            src = n.get("src", "")
            m = _SMILIE_RE.search(src)
            if m:
                return f" :{m.group(1)}: "
            if "viewpost.gif" in src or "/buttons/" in src:
                return ""
            self.post.n_img += 1
            return " [img] "
        if name == "iframe":
            self.post.n_video += 1
            return f" [video {_domain(n.get('src', ''))}] "
        if name == "a":
            if "__cf_email__" in cls:
                return " [email] "
            href = n.get("href", "")
            inner = yield self._render(n, in_quote)
            stripped = inner.strip()
            if "[img]" in inner or "[video" in inner:
                return inner
            if not stripped or _URL_RE.fullmatch(stripped) or stripped.startswith(href[:20]) or "..." in stripped:
                self.post.n_link += 1
                return f" [enlace {_domain(href)}] "
            return inner
        if name in ("span",) and "__cf_email__" in cls:
            return " [email] "
        if name == "div" and "margin:20px" in style.replace(" ", "") and "margin-top:5px" in style.replace(" ", ""):
            return self._quote(n, in_quote)
        if name == "span" and "smallfont" in cls and "Spoiler" in n.get_text():
            return ""
        if name == "div" and "spoiler" in cls:
            inner = yield self._render(n, in_quote)
            return f" [spoiler]{inner.strip()}[/spoiler] "
        if name == "li":
            inner = yield self._render(n, in_quote)
            return "\n- " + inner.strip()
        if name in ("ul", "ol"):
            inner = yield self._render(n, in_quote)
            return inner + "\n"
        if name == "hr":
            return "\n"
        if name == "div" and "smallfont" in cls and n.find("strong") is not None and n.find_parent("td") is not None:
            # "icon and title" block at the top of an OP: duplicates the thread title.
            return ""
        if name == "pre":
            return "\n" + n.get_text() + "\n"
        return (yield self._render(n, in_quote))

    def _quote(self, n: Tag, in_quote: bool) -> str:
        cell = n.find("td", class_="alt2") or n
        user, post_id = None, None
        header = cell.find("div", recursive=False)
        body = cell.find("div", style=re.compile("italic"))
        if header is not None and "Cita de" in header.get_text():
            b = header.find("b")
            user = b.get_text(strip=True) if b else None
            a = header.find("a", href=_QUOTED_POST_RE)
            if a:
                post_id = int(_QUOTED_POST_RE.search(a["href"]).group(1))
        if body is None:
            body = cell
        if in_quote:
            # FC strips nested quotes anyway; don't recurse into them.
            return ""
        sub = _Renderer()
        inner = sub.render(body, in_quote=True)
        inner = _tidy(inner)
        if len(inner) > QUOTE_MAX_CHARS:
            inner = inner[:QUOTE_MAX_CHARS].rsplit(" ", 1)[0] + "…"
        self.post.quotes.append((user, post_id))
        # Author is a placeholder index into post.quotes; build.py swaps in the pseudonym.
        tag = f"[cita=\x00{len(self.post.quotes) - 1}\x00]" if user else "[cita]"
        return f"\n{tag}{inner}[/cita]\n"


def _tidy(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _scrub(s: str) -> str:
    s = _EMAIL_RE.sub("[email]", s)
    s = _PHONE_RE.sub("[teléfono]", s)
    s = _URL_RE.sub(lambda m: f"[enlace {_domain(m.group(0))}]", s)
    return s


def parse_post(html: str) -> Post:
    soup = BeautifulSoup(html, "lxml")
    # Crawled posts are the post_message div itself; its first <td> would be a quote box.
    root = soup.find("div", id=re.compile(r"^post_message_")) if html.lstrip().startswith("<div") else None
    root = root or soup.find("td") or soup
    r = _Renderer()
    text = r.render(root)
    r.post.text = _scrub(_tidy(text))
    return r.post


_TAG_RE = re.compile(r"\+\s?([A-Za-zÁÉÍÓÚÑáéíóúñ0-9]+)")


def title_tags(title: str) -> list[str]:
    """'Qué capricho os habéis dado +Hd +serio' -> ['+hd', '+serio']"""
    seen = []
    for t in _TAG_RE.findall(title or ""):
        t = "+" + t.lower()
        if t not in seen:
            seen.append(t)
    return seen
