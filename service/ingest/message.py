"""Raw RFC 822 → Incoming: cleaned text, candidate links, usable images."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MIN_IMAGE_BYTES = 10 * 1024
MIN_IMAGE_SIDE = 200
MAX_IMAGES = 5
LINK_FIRST_MAX_CHARS = 400

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_TRAILING = ".,;:!?"
_JUNK_LINK_RE = re.compile(r"unsubscribe|/unsub|manage.?preferences|list-manage\.com/track|"
                           r"/open\?|/pixel|mailchi\.mp/.*/track", re.I)
_QUOTED_REPLY_RE = re.compile(r"\n\s*On .{5,120}wrote:\s*\n.*", re.S)


@dataclass
class Incoming:
    uid: str
    message_id: str | None
    sender: str
    text: str
    links: list[str] = field(default_factory=list)
    images: list[tuple[str, bytes]] = field(default_factory=list)
    auto_generated: bool = False


def _unwrap(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.lower()
    q = parse_qs(p.query)
    if host.endswith("google.com") and p.path == "/url" and q.get("q"):
        return q["q"][0]
    if host.endswith("safelinks.protection.outlook.com") and q.get("url"):
        return q["url"][0]
    if host == "l.facebook.com" and q.get("u"):
        return q["u"][0]
    return url


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = text.split("\n-- \n", 1)[0]          # signature delimiter
    text = _QUOTED_REPLY_RE.sub("", text)        # "On … wrote:" + everything after
    text = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith(">"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _links(text: str, html: str | None) -> list[str]:
    found = [u.rstrip(_TRAILING) for u in _URL_RE.findall(text)]
    if html:
        found += [a["href"] for a in BeautifulSoup(html, "html.parser").find_all("a", href=True)
                  if a["href"].startswith("http")]
    out = []
    for u in (_unwrap(u) for u in found):
        if not _JUNK_LINK_RE.search(u) and u not in out:
            out.append(u)
    return out


def _usable_image(data: bytes) -> bool:
    if not (MIN_IMAGE_BYTES <= len(data) <= MAX_IMAGE_BYTES):
        return False
    try:
        from PIL import Image
        with Image.open(BytesIO(data)) as im:
            return min(im.size) >= MIN_IMAGE_SIDE
    except Exception:
        return False


def parse(raw: bytes, uid: str) -> Incoming:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sender = parseaddr(msg.get("From", ""))[1].lower()
    plain = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    html = html_part.get_content() if html_part else None
    if plain is not None and plain.get_content().strip():
        body = plain.get_content()
    else:
        body = BeautifulSoup(html or "", "html.parser").get_text("\n")
    text = _clean_text(body)
    images = []
    for part in msg.walk():
        if part.get_content_maintype() == "image" and len(images) < MAX_IMAGES:
            data = part.get_payload(decode=True) or b""
            if _usable_image(data):
                images.append((part.get_content_type(), data))
    auto = (msg.get("Auto-Submitted", "no").strip().lower() != "no"
            or msg.get("Precedence", "").strip().lower() in {"bulk", "list", "junk"})
    return Incoming(uid=uid, message_id=msg.get("Message-ID"), sender=sender, text=text,
                    links=_links(text, html), images=images, auto_generated=auto)


def is_link_first(inc: Incoming) -> bool:
    return len(_URL_RE.sub("", inc.text).strip()) <= LINK_FIRST_MAX_CHARS
