"""The shipped PDFs read back as exactly what their Markdown sources say.

The PDFs have what makes extraction hard in real documents: a running header
and footer, a document-control table, a heading left alone at the bottom of a
page, bullets drawn in a font with no Unicode mapping. If any of that leaked
into the text or moved a section boundary, a section here would differ.
"""

import re

import pytest

from app.policies.parser import MarkdownPolicyParser
from app.policies.pdf_parser import PdfPolicyParser
from tests.conftest import MARKDOWN_DIR, POLICY_DIR

PDFS = sorted(POLICY_DIR.glob("*/v*.pdf"))


def as_plain_text(markdown_body: str) -> str:
    """A Markdown section body as the PDF shows it: no `**`, one line per paragraph or bullet."""
    blocks = []
    for block in re.split(r"\n\s*\n", markdown_body.replace("**", "").strip()):
        lines = [line.strip() for line in block.split("\n")]
        if lines[0].startswith("- "):
            items = " ".join(lines).split("- ")[1:]
            blocks.append("\n".join(f"- {' '.join(item.split())}" for item in items))
        else:
            blocks.append(" ".join(" ".join(lines).split()))
    return "\n\n".join(blocks)


def test_every_markdown_source_has_its_pdf():
    sources = {p.relative_to(MARKDOWN_DIR).with_suffix("") for p in MARKDOWN_DIR.glob("*/v*.md")}
    shipped = {p.relative_to(POLICY_DIR).with_suffix("") for p in PDFS}
    assert sources == shipped and len(shipped) == 4


@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_pdf_reads_back_as_its_markdown_source(pdf):
    source = MarkdownPolicyParser().parse((MARKDOWN_DIR / pdf.parent.name / f"{pdf.stem}.md").read_text())
    parsed = PdfPolicyParser().parse(pdf.read_bytes())

    for field in ("company_id", "company_name", "doc_id", "title", "version", "effective_from"):
        assert getattr(parsed, field) == getattr(source, field), field
    assert [(s.section_id, s.title, s.path) for s in parsed.sections] == [
        (s.section_id, s.title, s.path) for s in source.sections
    ]
    for got, expected in zip(parsed.sections, source.sections, strict=True):
        assert got.body == as_plain_text(expected.body), got.section_id


def test_pdfs_span_several_pages_so_page_breaks_are_exercised():
    import pdfplumber

    for pdf in PDFS:
        with pdfplumber.open(pdf) as doc:
            assert len(doc.pages) >= 2, pdf
