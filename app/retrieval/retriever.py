"""Retrievers: from a question to the most relevant chunks of one policy version."""

import logging
from typing import Protocol

from app.errors import TenantIsolationError
from app.retrieval.bm25 import BM25Cache
from app.retrieval.embeddings import Embedder
from app.retrieval.models import ScoredChunk
from app.retrieval.vector_store import ChromaPolicyStore

log = logging.getLogger(__name__)


class Retriever(Protocol):
    name: str

    def retrieve(self, company_id: str, version: int, question: str, k: int) -> list[ScoredChunk]:
        """Top `k` chunks of `company_id`'s policy version `version`, best first."""
        ...


def ensure_same_tenant(company_id: str, version: int, results: list[ScoredChunk]) -> list[ScoredChunk]:
    """Last check before chunks can reach a prompt: all must be the caller's company and live version.

    The store already guarantees this through per-company collections and a
    version filter. Checking again costs nothing and turns a future bug into
    a refused request instead of a leak.
    """
    for result in results:
        chunk = result.chunk
        if chunk.company_id != company_id or chunk.version != version:
            log.critical(
                "tenant check failed: wanted %s v%s, got chunk %s", company_id, version, chunk.chunk_id
            )
            raise TenantIsolationError(chunk.chunk_id)
    return results


class DenseRetriever:
    name = "dense"

    def __init__(self, store: ChromaPolicyStore, embedder: Embedder):
        self._store = store
        self._embedder = embedder

    def retrieve(self, company_id: str, version: int, question: str, k: int) -> list[ScoredChunk]:
        [embedding] = self._embedder.embed([question])
        results = self._store.query(company_id, version, embedding, k)
        return ensure_same_tenant(company_id, version, results)


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Combine several rankings of ids: score(id) = sum of 1 / (k + rank).

    RRF only looks at positions, so it needs no tuning to make cosine
    similarities and BM25 scores comparable. k=60 is the value from the
    original paper (Cormack et al., 2009) and is the usual default.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


class HybridRetriever:
    """Dense and BM25 candidates merged with reciprocal rank fusion."""

    name = "hybrid"

    def __init__(self, store: ChromaPolicyStore, embedder: Embedder, bm25: BM25Cache, candidates: int = 8, rrf_k: int = 60):
        self._store = store
        self._embedder = embedder
        self._bm25 = bm25
        self._candidates = candidates
        self._rrf_k = rrf_k

    def retrieve(self, company_id: str, version: int, question: str, k: int) -> list[ScoredChunk]:
        [embedding] = self._embedder.embed([question])
        dense = self._store.query(company_id, version, embedding, self._candidates)
        keyword = self._bm25.get(company_id, version).search(question, self._candidates)

        by_id = {r.chunk.chunk_id: r.chunk for r in dense}
        by_id.update({chunk.chunk_id: chunk for chunk, _ in keyword})
        similarity = {r.chunk.chunk_id: r.similarity for r in dense}

        fused = reciprocal_rank_fusion(
            [[r.chunk.chunk_id for r in dense], [chunk.chunk_id for chunk, _ in keyword]], self._rrf_k
        )
        top = sorted(fused, key=lambda cid: fused[cid], reverse=True)[:k]
        results = [ScoredChunk(by_id[cid], similarity.get(cid, 0.0), fused[cid]) for cid in top]
        return ensure_same_tenant(company_id, version, results)
