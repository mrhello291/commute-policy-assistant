"""Citations built from stored metadata, never from model text."""

from dataclasses import dataclass

from app.retrieval.models import ScoredChunk

EXCERPT_CHARS = 280


@dataclass(frozen=True)
class Citation:
    company_id: str
    document: str
    version: int
    section: str
    title: str
    excerpt: str


def _shorten(text: str, limit: int = EXCERPT_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"


def render_citations(labels: tuple[str, ...], by_label: dict[str, ScoredChunk]) -> list[Citation]:
    """One citation per cited section, in the order the model cited them."""
    citations: list[Citation] = []
    seen_sections: set[str] = set()
    for label in labels:
        chunk = by_label[label].chunk
        if chunk.section_id in seen_sections:
            continue
        seen_sections.add(chunk.section_id)
        citations.append(
            Citation(
                company_id=chunk.company_id,
                document=chunk.doc_title,
                version=chunk.version,
                section=chunk.section_id,
                title=chunk.section_title,
                excerpt=_shorten(chunk.text),
            )
        )
    return citations
