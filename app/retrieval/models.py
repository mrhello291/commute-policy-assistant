"""Retrieval result types."""

from dataclasses import dataclass

from app.policies.models import Chunk


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: Chunk
    # Cosine similarity from the dense search, 0 when the chunk was found
    # only by keyword search. The evidence gate reads this value because,
    # unlike a fused rank score, it means the same thing for every query.
    similarity: float
    # Ordering score from whichever retriever produced the result.
    score: float
