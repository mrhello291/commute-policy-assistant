"""Retrievers: from a question to the most relevant chunks of one policy version."""

import logging
from typing import Protocol

from app.errors import TenantIsolationError
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
