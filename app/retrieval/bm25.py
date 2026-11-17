"""Keyword search over one policy version with BM25.

Policy questions often hinge on exact words: "no-show", "airport",
"Saturday", "salary". A small sentence-embedding model can blur those, while
BM25 matches them directly. The corpus per company is tiny (a few dozen
chunks), so the index is built in memory on first use and cached until the
company's live version changes.

Chroma's own sparse/hybrid search is a Chroma Cloud feature, so this is done
in-process with `rank-bm25`.
"""

import re
import threading

from rank_bm25 import BM25Okapi

from app.policies.models import Chunk
from app.retrieval.vector_store import ChromaPolicyStore

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or "
    "our so than that the their them then there this to was we what when where which who will "
    "with you your".split()
)


def stem(token: str) -> str:
    """A deliberately tiny suffix stripper: "Saturdays", "booked" and
    "approves" should match "Saturday", "book" and "approve". It only has to
    be consistent between queries and documents, not linguistically right."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > 4 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    if len(token) > 4 and token.endswith("e"):
        token = token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        corpus = [tokenize(c.indexed_text) for c in chunks]
        self._bm25 = BM25Okapi(corpus) if chunks else None

    def search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        terms = tokenize(query)
        if self._bm25 is None or not terms:
            return []
        scores = self._bm25.get_scores(terms)
        ranked = sorted(zip(self.chunks, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        return [(chunk, float(score)) for chunk, score in ranked[:k] if score > 0]


class BM25Cache:
    """One index per (company, version), built lazily."""

    def __init__(self, store: ChromaPolicyStore):
        self._store = store
        self._indexes: dict[tuple[str, int], BM25Index] = {}
        self._lock = threading.Lock()

    def get(self, company_id: str, version: int) -> BM25Index:
        key = (company_id, version)
        with self._lock:
            index = self._indexes.get(key)
            if index is None:
                index = BM25Index(self._store.chunks(company_id, version))
                self._indexes[key] = index
            return index

    def invalidate(self, company_id: str) -> None:
        with self._lock:
            for key in [k for k in self._indexes if k[0] == company_id]:
                del self._indexes[key]
