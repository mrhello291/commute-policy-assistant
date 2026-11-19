"""Builds the sample policy PDFs from their Markdown sources.

    python scripts/make_policy_pdfs.py                  # all sample policies
    python scripts/make_policy_pdfs.py in.md out.pdf    # one document, e.g. a v3 to upload

Reads `data/archive/markdown-policies/<company>/v<N>.md` and writes
`data/policies/<company>/v<N>.pdf`. The PDFs are laid out the way HR policy
documents usually are, so the PDF parser is tested against the same problems
it would meet in a real upload:

- a document-control table on page 1 (company, document ID, version, date),
- a running header and a "Page X of Y" footer on every page,
- bold numbered headings, a size above body text,
- paragraphs and bullet lists that break across pages.

Output is byte-for-byte reproducible (reportlab's invariant mode), so
re-running the script on unchanged sources leaves git clean.
"""

import html
import io
import re
import sys
from datetime import date
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.policies.parser import MarkdownPolicyParser  # noqa: E402

SOURCE_DIR = ROOT / "data" / "archive" / "markdown-policies"
OUTPUT_DIR = ROOT / "data" / "policies"

rl_config.invariant = 1

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=13.5, spaceAfter=6, alignment=TA_LEFT)
TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=10)
H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=10, spaceAfter=5)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=6, spaceAfter=3)
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=9.5, leading=12)
CELL_LABEL = ParagraphStyle("cell_label", parent=CELL, fontName="Helvetica-Bold")


def _inline(text: str) -> str:
    """Markdown inline text to reportlab's mini-HTML (bold only)."""
    escaped = html.escape(" ".join(text.split()), quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def _long_date(value: date) -> str:
    return f"{value.day} {value:%B %Y}"


def _body_flowables(body: str) -> list:
    """A section body: paragraphs separated by blank lines, `- ` bullet lists."""
    flowables = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        if lines[0].lstrip().startswith("- "):
            items, current = [], []
            for line in lines:
                if line.lstrip().startswith("- "):
                    if current:
                        items.append(" ".join(current))
                    current = [line.lstrip()[2:]]
                else:
                    current.append(line.strip())
            items.append(" ".join(current))
            flowables.append(
                ListFlowable(
                    [ListItem(Paragraph(_inline(item), BODY), leftIndent=14) for item in items],
                    bulletType="bullet",
                    start="•",
                    leftIndent=14,
                    bulletFontSize=9,
                )
            )
        else:
            flowables.append(Paragraph(_inline(" ".join(lines)), BODY))
    return flowables


def render_policy_pdf(markdown: str) -> bytes:
    """Lay out one Markdown policy as a PDF and return the file's bytes."""
    doc = MarkdownPolicyParser().parse(markdown)

    control = [
        ("Company", doc.company_name),
        ("Company ID", doc.company_id),
        ("Document ID", doc.doc_id),
        ("Version", str(doc.version)),
        ("Effective from", _long_date(doc.effective_from)),
    ]

    def control_table() -> Table:
        table = Table(
            [[Paragraph(k, CELL_LABEL), Paragraph(html.escape(v), CELL)] for k, v in control],
            colWidths=[38 * mm, 90 * mm],
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eeeeee")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        return table

    def story() -> list:
        flowables = [Paragraph(_inline(doc.title), TITLE), control_table(), Spacer(1, 8 * mm)]
        current_top = None
        for section in doc.sections:
            top = section.path[0]
            if top != current_top:
                flowables.append(Paragraph(_inline(top), H1))
                current_top = top
            if "." in section.section_id:
                flowables.append(Paragraph(_inline(f"{section.section_id} {section.title}"), H2))
            flowables.extend(_body_flowables(section.body))
        return flowables

    footer_left = f"{doc.title}  |  Version {doc.version}  |  Effective {_long_date(doc.effective_from)}"

    def decorate(canvas, pdf):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        width, height = A4
        canvas.drawString(20 * mm, height - 12 * mm, doc.company_name)
        canvas.drawRightString(width - 20 * mm, height - 12 * mm, "Internal - for employees only")
        canvas.line(20 * mm, height - 14 * mm, width - 20 * mm, height - 14 * mm)
        canvas.drawString(20 * mm, 12 * mm, footer_left)
        canvas.drawRightString(width - 20 * mm, 12 * mm, f"Page {pdf.page} of {pdf.page_count}")
        canvas.restoreState()

    class CountingDoc(SimpleDocTemplate):
        """Two passes, so the footer can say "of N"."""

        page_count = 0

    def build(page_count: int) -> tuple[bytes, int]:
        buffer = io.BytesIO()
        pdf = CountingDoc(
            buffer,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=22 * mm,
            bottomMargin=22 * mm,
            title=doc.title,
            author=doc.company_name,
            subject=f"{doc.doc_id} version {doc.version}",
        )
        pdf.page_count = page_count
        pdf.build(story(), onFirstPage=decorate, onLaterPages=decorate)
        return buffer.getvalue(), pdf.page

    _, pages = build(0)
    data, _ = build(pages)
    return data


def main() -> None:
    if len(sys.argv) == 3:
        # One-off: python scripts/make_policy_pdfs.py my-v3.md my-v3.pdf
        source, target = Path(sys.argv[1]), Path(sys.argv[2])
        target.write_bytes(render_policy_pdf(source.read_text(encoding="utf-8")))
        print(f"{source} -> {target}")
        return
    sources = sorted(SOURCE_DIR.glob("*/v*.md"))
    if not sources:
        sys.exit(f"no Markdown policies under {SOURCE_DIR}")
    for source in sources:
        target = OUTPUT_DIR / source.parent.name / f"{source.stem}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(render_policy_pdf(source.read_text(encoding="utf-8")))
        print(f"{source.relative_to(ROOT)} -> {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
