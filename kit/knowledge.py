"""The college documents (handbook, brochure, lesson notes, circulars) as a searchable index.

Documents are downloaded from the college system, split into chunks and ranked with BM25.
BM25 is a classic keyword ranking. For a few dozen pages it is fast, needs no model download,
and works on any host. The index refreshes itself when the document list changes.
"""
import asyncio
import hashlib
import io
import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import quote

from .college import college

log = logging.getLogger("kit.knowledge")
STOP = set("a an the is are was were be to of in on at for and or but if it its this that these those "
           "do does did can could should would will i me my we you your they what which who how when "
           "where why please tell about from with as by".split())
PUBLIC_HINT = re.compile(r"brochure|admission|prospectus|course|fee", re.I)


def tokenize(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if t in STOP:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


@dataclass
class Chunk:
    doc_id: str
    title: str
    text: str
    public: bool


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind(". ", start + size // 2, end)
            if cut != -1:
                end = cut + 1
        piece = text[start:end].strip()
        if len(piece) > 40:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


class BM25Index:
    K1, B = 1.5, 0.75

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.tf = [Counter(tokenize(c.title + " " + c.text)) for c in chunks]
        self.len = [sum(d.values()) for d in self.tf]
        n = len(chunks)
        self.avg = (sum(self.len) / n) if n else 0
        df: Counter = Counter()
        for d in self.tf:
            df.update(d.keys())
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 4, public_only: bool = False) -> list[Chunk]:
        terms = set(tokenize(query))
        scored = []
        for i, d in enumerate(self.tf):
            if public_only and not self.chunks[i].public:
                continue
            s = 0.0
            for t in terms:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [self.chunks[i] for _, i in scored[:k]]


def extract_text(raw: bytes) -> str:
    if raw[:5] == b"%PDF-":
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            log.exception("PDF extraction failed")
            return ""
    return raw.decode("utf-8", errors="ignore")


def _doc_items(payload) -> list[dict]:
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _first(d: dict, keys: tuple[str, ...]):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


class Knowledge:
    def __init__(self) -> None:
        self.index = BM25Index([])
        self.signature: str | None = None
        self.titles: list[str] = []

    async def sync(self, force: bool = False) -> bool:
        res = await college.request("GET", "/v1/documents")
        if res["status"] != 200:
            log.warning("document list unavailable (status %s)", res["status"])
            return False
        items = _doc_items(res["data"])
        sig = hashlib.sha1(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()
        if sig == self.signature and not force:
            return False

        metas = []
        for d in items:
            did = _first(d, ("id", "document_id", "doc_id", "uuid", "slug"))
            if did is None:
                continue
            title = str(_first(d, ("title", "name", "filename", "file_name")) or did)
            hint = f"{title} {_first(d, ('category', 'type', 'kind')) or ''}"
            metas.append((str(did), title, bool(PUBLIC_HINT.search(hint))))
        any_public = any(m[2] for m in metas)

        chunks, titles = [], []
        for did, title, is_public in metas:
            raw = await college.download(f"/v1/documents/{quote(did, safe='')}/file")
            text = await asyncio.to_thread(extract_text, raw) if raw else ""
            pieces = chunk_text(text)
            if not pieces:
                continue
            titles.append(title)
            for p in pieces:
                chunks.append(Chunk(did, title, p, is_public or not any_public))
        if not chunks:
            log.warning("no readable documents; keeping the previous index")
            return False
        self.index, self.signature, self.titles = BM25Index(chunks), sig, titles
        log.info("indexed %d chunks from %d documents", len(chunks), len(titles))
        return True

    def search(self, query: str, k: int = 4, public_only: bool = False) -> list[Chunk]:
        return self.index.search(query, k, public_only)


knowledge = Knowledge()
