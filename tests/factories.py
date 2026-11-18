"""Builders for test objects."""

from app.policies.models import Chunk
from app.retrieval.models import ScoredChunk


def chunk(
    section_id="4.2",
    text="A security escort travels on every drop after 7:30 PM with a woman employee.",
    company="yamuna",
    version=2,
    flags=(),
    title="Security escort",
) -> Chunk:
    return Chunk(
        chunk_id=f"{company}:doc:v{version}:{section_id}:0",
        company_id=company,
        company_name=company.title(),
        doc_id="doc",
        doc_title=f"{company.title()} Transport Policy",
        version=version,
        section_id=section_id,
        section_title=title,
        section_path=("4. Night-shift travel", f"{section_id} {title}"),
        part=0,
        text=text,
        flags=flags,
    )


def scored(similarity=0.8, **kwargs) -> ScoredChunk:
    return ScoredChunk(chunk(**kwargs), similarity, similarity)
