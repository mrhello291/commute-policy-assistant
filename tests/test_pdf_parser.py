"""The PDF parser against the layouts that break naive text extraction.

Each test draws a small PDF with reportlab, placing text exactly where a real
document would put it, so a failure points at one layout problem.
"""

import io
from datetime import date

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table

from app.errors import PolicyFormatError
from app.policies.pdf_parser import PdfPolicyParser, _join_lines

CONTROL = [
    "Company: Kaveri Foods",
    "Company ID: kaveri",
    "Document ID: kaveri-transport",
    "Version: 1",
    "Effective from: 1 March 2026",
]


class Pdf:
    """Writes lines top to bottom, with an optional header and footer per page."""

    def __init__(self, header: str | None = None, footer: str | None = None, **canvas_args):
        self._buffer = io.BytesIO()
        self._canvas = canvas.Canvas(self._buffer, pagesize=A4, invariant=1, **canvas_args)
        self._header, self._footer = header, footer
        self._page = 1
        self._start_page()

    def _start_page(self) -> None:
        self.y = A4[1] - 80
        self._canvas.setFont("Helvetica", 8)
        if self._header:
            self._canvas.drawString(60, A4[1] - 30, self._header)
        if self._footer:
            self._canvas.drawString(60, 30, self._footer.format(page=self._page))

    def title(self, text: str) -> "Pdf":
        return self.line(text, font="Helvetica-Bold", size=18, after=10)

    def heading(self, text: str, size: float = 12) -> "Pdf":
        return self.line(text, font="Helvetica-Bold", size=size, before=6)

    def line(self, text: str, font="Helvetica", size=10.0, x=60, before=0.0, after=0.0) -> "Pdf":
        self.y -= before
        self._canvas.setFont(font, size)
        self._canvas.drawString(x, self.y, text)
        self.y -= size * 1.35 + after
        return self

    def para(self, *lines: str) -> "Pdf":
        for text in lines:
            self.line(text)
        self.y -= 8
        return self

    def front(self, control=CONTROL) -> "Pdf":
        self.title("Kaveri Foods Transport Policy")
        for text in control:
            self.line(text)
        self.y -= 12
        return self

    def table(self, rows: list[list[str]]) -> "Pdf":
        table = Table(rows, style=[("GRID", (0, 0), (-1, -1), 0.5, "grey"), ("FONTSIZE", (0, 0), (-1, -1), 10)])
        _, height = table.wrapOn(self._canvas, 400, 400)
        table.drawOn(self._canvas, 60, self.y - height + 10)
        self.y -= height + 8
        return self

    def image(self) -> "Pdf":
        self._canvas.drawImage(ImageReader(Image.new("RGB", (40, 40), "white")), 60, 200, 400, 500)
        return self

    def new_page(self) -> "Pdf":
        self._canvas.showPage()
        self._page += 1
        self._start_page()
        return self

    def bytes(self) -> bytes:
        self._canvas.save()
        return self._buffer.getvalue()


def parse(pdf: Pdf):
    return PdfPolicyParser().parse(pdf.bytes())


def bodies(doc) -> dict[str, str]:
    return {s.section_id: s.body for s in doc.sections}


def test_reads_document_control_lines_and_title():
    doc = parse(Pdf().front().heading("1. Scope").para("Covers everyone."))

    assert (doc.company_id, doc.company_name, doc.doc_id, doc.version) == (
        "kaveri",
        "Kaveri Foods",
        "kaveri-transport",
        1,
    )
    assert doc.title == "Kaveri Foods Transport Policy"
    assert doc.effective_from == date(2026, 3, 1)


def test_numbered_list_in_body_text_is_not_a_heading():
    doc = parse(
        Pdf()
        .front()
        .heading("1. Before your trip")
        .para("Please check three things:")
        .para("1. Your ID card is with you.")
        .para("2. The cab number matches the app.")
    )

    assert [s.section_id for s in doc.sections] == ["1"]
    assert "2. The cab number matches the app." in doc.sections[0].body


def test_running_header_footer_and_page_numbers_are_removed_and_paragraph_joined():
    pdf = Pdf(header="Kaveri Foods  |  Internal", footer="Transport Policy v1   Page {page} of 2").front()
    pdf.heading("1. Booking").line("Trips are booked in the app at least four hours")
    pdf.new_page()
    pdf.para("before the shift starts.").heading("2. Cancelling").para("Cancel an hour ahead.")

    doc = parse(pdf)

    assert bodies(doc) == {
        "1": "Trips are booked in the app at least four hours before the shift starts.",
        "2": "Cancel an hour ahead.",
    }


def test_a_heading_at_the_bottom_of_a_page_keeps_the_text_on_the_next_page():
    pdf = Pdf(footer="Page {page}").front().heading("1. Scope").para("All staff.").heading("1.1 Night drops", size=11)
    pdf.new_page().para("Drops after 10 PM have an escort.")

    assert bodies(parse(pdf))["1.1"] == "Drops after 10 PM have an escort."


def test_sections_record_the_pages_they_span():
    pdf = Pdf(footer="Page {page}").front().heading("1. Scope").para("All staff.").heading("1.1 Night drops", size=11)
    pdf.new_page().para("Drops after 10 PM have an escort.").heading("2. Booking").para("Book in the app.")

    assert [(s.section_id, s.pages) for s in parse(pdf).sections] == [("1", (1, 1)), ("1.1", (1, 2)), ("2", (2, 2))]


def test_a_finished_sentence_before_a_page_break_starts_a_new_paragraph():
    pdf = Pdf().front().heading("1. Scope").line("All staff are covered.").new_page().para("Contractors are not.")

    assert bodies(parse(pdf))["1"] == "All staff are covered.\n\nContractors are not."


def test_heading_wrapped_over_two_lines_is_one_title():
    pdf = Pdf().front().heading("1. Travel for employees working night shifts and on")
    pdf.line("public holidays", font="Helvetica-Bold", size=12).para("Rules for night shifts.")

    doc = parse(pdf)

    assert doc.sections[0].title == "Travel for employees working night shifts and on public holidays"
    assert doc.sections[0].body == "Rules for night shifts."


def test_paragraphs_bullets_and_hyphenation():
    pdf = Pdf().front().heading("1. Terms")
    pdf.line("A night-").line("shift trip is any trip in the night window.")
    pdf.y -= 10
    pdf.line("• Trip: one pickup or drop,", x=60).line("counted per person.", x=72)
    pdf.line("• No-show: a missed trip.", x=60)

    assert bodies(parse(pdf))["1"] == (
        "A night-shift trip is any trip in the night window.\n\n"
        "- Trip: one pickup or drop, counted per person.\n- No-show: a missed trip."
    )


@pytest.mark.parametrize(
    "fragments, joined",
    [
        (["the escort is ar\u00ad", "ranged by HR."], "the escort is arranged by HR."),
        (["a night-", "shift trip"], "a night-shift trip"),
        (["grades L1 to L5 -", "Bengaluru only"], "grades L1 to L5 - Bengaluru only"),
        (["see section", "4.1"], "see section 4.1"),
    ],
)
def test_line_joining(fragments, joined):
    assert _join_lines(fragments) == joined


def test_a_table_is_read_row_by_row():
    pdf = Pdf().front().heading("7. Cost caps").para("Caps per trip:")
    pdf.table([["Trip type", "Cap (INR)", "Approver"], ["Regular", "900", "None"], ["Airport", "1,500", "Manager"]])
    pdf.para("Anything above the cap needs approval.")

    assert bodies(parse(pdf))["7"] == (
        "Caps per trip:\n\n"
        "Trip type: Regular; Cap (INR): 900; Approver: None\n"
        "Trip type: Airport; Cap (INR): 1,500; Approver: Manager\n\n"
        "Anything above the cap needs approval."
    )


def test_table_of_contents_is_skipped():
    pdf = Pdf().front().line("Contents", font="Helvetica-Bold", size=12)
    pdf.line("1. Scope ................................ 2", font="Helvetica-Bold")
    pdf.line("2. Booking .............................. 2", font="Helvetica-Bold")
    pdf.new_page().heading("1. Scope").para("All staff.").heading("2. Booking").para("Book in the app.")

    assert bodies(parse(pdf)) == {"1": "All staff.", "2": "Book in the app."}


def test_subsections_get_their_heading_path():
    pdf = Pdf().front().heading("4. Night travel", size=13).heading("4.2 Security escort", size=11).para("Escort.")

    section = parse(pdf).sections[0]

    assert (section.section_id, section.path) == ("4.2", ("4. Night travel", "4.2 Security escort"))


def test_date_written_with_an_ordinal_is_accepted():
    control = [*CONTROL[:4], "Effective from: 1st March 2026"]
    doc = parse(Pdf().front(control).heading("1. Scope").para("All staff."))

    assert doc.effective_from == date(2026, 3, 1)


@pytest.mark.parametrize(
    "data, reason",
    [
        (b"", "empty"),
        (b"just some text", "not a PDF"),
        (b"%PDF-1.4\n1 0 obj << /Type /Catalog", "could not be read"),
    ],
)
def test_files_that_are_not_readable_pdfs_are_refused(data, reason):
    with pytest.raises(PolicyFormatError, match=reason):
        PdfPolicyParser().parse(data)


def test_password_protected_pdf_is_refused():
    pdf = Pdf(encrypt="secret").front().heading("1. Scope").para("All staff.")

    with pytest.raises(PolicyFormatError, match="password-protected"):
        parse(pdf)


def test_scanned_page_is_refused_naming_the_page():
    pdf = Pdf().front().heading("1. Scope").para("All staff.").new_page().image()

    with pytest.raises(PolicyFormatError, match="page 2 is an image"):
        parse(pdf)


def test_headings_in_body_style_are_refused_rather_than_guessed():
    pdf = Pdf().front().line("1. Scope").para("All staff.")

    with pytest.raises(PolicyFormatError, match="no numbered section headings"):
        parse(pdf)


@pytest.mark.parametrize(
    "control, reason",
    [
        (CONTROL[:3] + CONTROL[4:], "missing: Version"),
        ([*CONTROL[:3], "Version: v3", CONTROL[4]], "found 'v3'"),
        ([*CONTROL[:3], "Version: 2.5", CONTROL[4]], "found '2.5'"),
        ([*CONTROL[:4], "Effective from: 03/04/2026"], "found '03/04/2026'"),
        (["Company: Kaveri Foods", "Company ID: Kaveri Foods", *CONTROL[2:]], "company_id must be"),
    ],
)
def test_document_control_problems_are_refused(control, reason):
    with pytest.raises(PolicyFormatError, match=reason):
        parse(Pdf().front(control).heading("1. Scope").para("All staff."))


def test_duplicate_section_number_is_refused():
    pdf = Pdf().front().heading("1. Scope").para("A.").heading("1. Booking").para("B.")

    with pytest.raises(PolicyFormatError, match="appears more than once"):
        parse(pdf)


def test_subsection_outside_its_parent_is_refused():
    pdf = Pdf().front().heading("1. Scope").para("A.").heading("2.1 Booking", size=11).para("B.")

    with pytest.raises(PolicyFormatError, match="not inside its parent"):
        parse(pdf)
