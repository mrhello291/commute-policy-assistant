"""Turns a parsed policy into chunks ready for indexing."""

from app.policies.models import Chunk, PolicyDocument, Section

DEFAULT_MAX_CHARS = 1200


class SectionChunker:
    """One chunk per section, splitting only sections that are too long.

    Policy sections are short and self-contained, so cutting at section
    boundaries keeps each chunk meaningful and makes citations exact. Fixed
    size windows would mix the end of one rule with the start of another.
    """

    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS):
        if max_chars < 200:
            raise ValueError("max_chars is too small to hold a paragraph")
        self._max_chars = max_chars

    def chunk(self, doc: PolicyDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in doc.sections:
            for part, text in enumerate(self._split(section)):
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc.company_id}:{doc.doc_id}:v{doc.version}:{section.section_id}:{part}",
                        company_id=doc.company_id,
                        company_name=doc.company_name,
                        doc_id=doc.doc_id,
                        doc_title=doc.title,
                        version=doc.version,
                        section_id=section.section_id,
                        section_title=section.title,
                        section_path=section.path,
                        part=part,
                        text=text,
                    )
                )
        return chunks

    def _split(self, section: Section) -> list[str]:
        if len(section.body) <= self._max_chars:
            return [section.body]

        pieces: list[str] = []
        current = ""
        for paragraph in section.body.split("\n\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= self._max_chars or not current:
                current = candidate
            else:
                pieces.append(current)
                current = paragraph
        if current:
            pieces.append(current)
        return pieces
