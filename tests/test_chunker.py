from datetime import date

from app.policies.chunker import SectionChunker
from app.policies.models import PolicyDocument, Section


def make_doc(*sections: Section) -> PolicyDocument:
    return PolicyDocument("acme", "Acme", "acme-policy", "Acme Policy", 2, date(2026, 1, 1), sections, "x")


def test_one_chunk_per_short_section():
    doc = make_doc(
        Section("1", "Scope", ("1. Scope",), "Everyone."),
        Section("2.1", "Escort", ("2. Night", "2.1 Escort"), "After 9 PM."),
    )
    chunks = SectionChunker().chunk(doc)
    assert [c.chunk_id for c in chunks] == ["acme:acme-policy:v2:1:0", "acme:acme-policy:v2:2.1:0"]
    assert all(c.company_id == "acme" and c.version == 2 for c in chunks)


def test_indexed_text_carries_heading_path():
    doc = make_doc(Section("2.1", "Escort", ("2. Night", "2.1 Escort"), "After 9 PM."))
    chunk = SectionChunker().chunk(doc)[0]
    assert chunk.indexed_text.startswith("Acme Policy > 2. Night > 2.1 Escort")
    assert chunk.text == "After 9 PM."


def test_long_section_splits_on_paragraphs_and_keeps_section_id():
    paragraphs = [f"Paragraph {i} " + "x" * 150 for i in range(5)]
    doc = make_doc(Section("3", "Long", ("3. Long",), "\n\n".join(paragraphs)))
    chunks = SectionChunker(max_chars=400).chunk(doc)
    assert len(chunks) > 1
    assert {c.section_id for c in chunks} == {"3"}
    assert [c.part for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= 400 for c in chunks)
    # No text is lost or duplicated.
    assert "\n\n".join(c.text for c in chunks) == "\n\n".join(paragraphs)
