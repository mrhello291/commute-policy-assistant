"""PDFs made by other tools read back as exactly what their source says.

The sample policies all come from our own generator, so they only prove the
parser reads one layout. These fixtures are one short policy laid out by a
Word export, LibreOffice Writer and Chrome's print to PDF. Each draws bullets
differently (a private-use Symbol character, a glyph placed as its own line,
a drawn dot), and Chrome's has a running header and a "Page X of Y" footer.
Rebuild them with `python scripts/check_pdf_producers.py --fixtures`.
"""

from pathlib import Path

import pytest

from app.policies.parser import MarkdownPolicyParser
from app.policies.pdf_parser import PdfPolicyParser
from tests.test_sample_pdfs import as_plain_text

FIXTURES = Path(__file__).parent / "fixtures" / "pdf_producers"
SOURCE = MarkdownPolicyParser().parse((FIXTURES / "source.md").read_text())


@pytest.mark.parametrize("producer", ["word", "writer", "chrome"])
def test_pdf_from_another_tool_reads_back_as_its_source(producer):
    parsed = PdfPolicyParser().parse((FIXTURES / f"{producer}.pdf").read_bytes())

    for field in ("company_id", "company_name", "doc_id", "title", "version", "effective_from"):
        assert getattr(parsed, field) == getattr(SOURCE, field), field
    assert [(s.section_id, s.title, s.path) for s in parsed.sections] == [
        (s.section_id, s.title, s.path) for s in SOURCE.sections
    ]
    for got, expected in zip(parsed.sections, SOURCE.sections, strict=True):
        assert got.body == as_plain_text(expected.body), got.section_id
