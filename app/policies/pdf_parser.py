"""Reads a policy PDF into the same numbered sections as the Markdown parser.

A PDF has no headings or paragraphs, only text placed on pages in some font
at some size. Answers cite section numbers, so the one thing this parser must
not do is guess a section boundary wrong: that doesn't fail loudly, it shows
up later as a confident answer citing 4.2 for text that came from 4.3. So
every step uses the layout, and a document it can't read with confidence is
refused with the reason instead of being half-read.

What it expects (what HR policy PDFs normally look like):

- a document-control block on the first page, as a table or as
  `Label: value` lines: Company, Company ID, Document ID, Version,
  Effective from (and optionally Title);
- the title as the largest text on the first page;
- numbered headings (`4. Night-shift travel`, `4.2 Security escort`) set in
  bold or in a larger size than the body text. `Section 4:`, `Clause 4 -`,
  `4.0` and roman `IV.` are read as section 4.

The steps, and the PDF problem each one handles:

1. Refuse files that aren't PDFs, are password-protected, too large, or have
   pages that are only images (scanned, so there is no text to cite).
2. Read text line by line with each line's font size and boldness. Tables are
   read as tables, so a row isn't flattened into loose words.
3. Drop running headers and footers: lines near the top or bottom edge that
   repeat on most pages (digits ignored, so "Page 2 of 5" matches "Page 3 of
   5"), and bare page numbers. Otherwise they'd land mid-sentence in
   whichever section crosses the page break.
4. The body font size is the most common one. A line is a heading only if it
   starts with a section number *and* stands out from body text; a numbered
   list item in body text ("1. Bring your ID card") stays text. Table-of-
   contents lines (dot leaders and a page number) are skipped.
5. Rebuild paragraphs from line spacing, join a paragraph that a page break
   cut in two, keep bullet items as `- ` lines, and undo hyphenation.
   Bullets are found however the producing tool drew them: a text glyph, a
   Symbol/Wingdings private-use character (Word), a glyph placed as its own
   line (LibreOffice) or a small drawn dot (browsers' print to PDF).
6. Apply the same section rules as Markdown: unique numbers, each subsection
   inside its parent.

Not handled, and refused or documented rather than guessed: scanned pages
(needs OCR first), multi-column layouts, headings that are neither bold nor
larger than body text.
"""

import functools
import hashlib
import io
import logging
import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import pdfplumber

from app.errors import PolicyFormatError
from app.policies.models import PolicyDocument, Section
from app.policies.parser import MAX_DOCUMENT_BYTES, MarkdownPolicyParser, validate_metadata

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 10_000_000
MAX_PAGES = 150
MAX_SECTION_DEPTH = 4

# Section number then title: "4. Night-shift travel", "4.2 Security escort".
_NUMBERED = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,5})\.?(?:\s*[:)\-–—])?\s+(\S.*)$")
# The other ways policies number their top-level headings: "Section 4: ...",
# "Clause 4 - ...", "IV. ...". Read as the plain number they stand for.
_HEADING_WORD = re.compile(r"^(?:section|clause|article)\s+(?=\d|[IVXL]+\b)", re.IGNORECASE)
_ROMAN_HEADING = re.compile(r"^([IVXL]{1,7})(?:\.|:|\)|\s+[\-–—])\s+(\S.*)$")
_ROMAN = re.compile(r"^(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
# Table-of-contents entry: "4.2 Security escort ......... 7"
_TOC_ENTRY = re.compile(r"(\.\s*){4,}\s*\d+$|\.{4,}\s*\d+$")
_PAGE_NUMBER = re.compile(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$|^[-–]\s*\d+\s*[-–]$", re.IGNORECASE)
_BULLET = re.compile(r"^(?:[•▪●◦‣⁃∙·*\-–]|\(cid:\d+\))\s+(.*)$")
# A bullet glyph on its own, as LibreOffice places it: a separate text line
# at the left of the item, in a symbol font.
_LONE_BULLET = re.compile(r"^(?:[•▪●◦○■□‣⁃∙·*\-–➢➤►✓✔]|\(cid:\d+\))$")
_SYMBOL_FONT = re.compile(r"symbol|wingding|webding|dingbat", re.IGNORECASE)
_CID = re.compile(r"\(cid:\d+\)")
_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?:]['\")”’]?$")

# Document-control labels, longest first so "company id" wins over "company".
_LABELS = {
    "company id": "company_id",
    "company code": "company_id",
    "company name": "company_name",
    "company": "company_name",
    "organisation": "company_name",
    "organization": "company_name",
    "document id": "doc_id",
    "document number": "doc_id",
    "document no": "doc_id",
    "doc id": "doc_id",
    "policy title": "title",
    "title": "title",
    "version": "version",
    "version no": "version",
    "effective from": "effective_from",
    "effective date": "effective_from",
}
_LABEL_LINE = re.compile(
    r"^(" + "|".join(sorted(map(re.escape, _LABELS), key=len, reverse=True)) + r")\.?\s*[:\-–]?\s+(.+)$",
    re.IGNORECASE,
)
_FIELD_NAMES = {
    "company_id": "Company ID",
    "company_name": "Company",
    "doc_id": "Document ID",
    "title": "Title",
    "version": "Version",
    "effective_from": "Effective from",
}
_DATE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y", "%d-%B-%Y")


@dataclass
class _Line:
    page: int
    text: str
    x0: float
    top: float
    bottom: float
    size: float
    bold: float  # share of the line's characters set in a bold font
    rows: list[list[str]] | None = None  # set when this "line" is a whole table
    x1: float = 0.0

    @property
    def is_table(self) -> bool:
        return self.rows is not None


@dataclass
class _Page:
    number: int
    height: float
    lines: list[_Line] = field(default_factory=list)
    has_text: bool = False
    has_images: bool = False


class PdfPolicyParser:
    def parse(self, data: bytes) -> PolicyDocument:
        return _parse_cached(bytes(data))

    def _parse(self, data: bytes) -> PolicyDocument:
        if not data:
            raise PolicyFormatError("document is empty")
        if len(data) > MAX_PDF_BYTES:
            raise PolicyFormatError(f"PDF is larger than {MAX_PDF_BYTES // 1_000_000} MB")
        if not data.lstrip()[:5] == b"%PDF-":
            raise PolicyFormatError("file is not a PDF")

        pages, pdf_title = self._read(data)
        self._refuse_unreadable(pages)
        lines = self._drop_running_text(pages)
        if not lines:
            raise PolicyFormatError("the PDF has no text to read")
        if sum(len(line.text) for line in lines) > MAX_DOCUMENT_BYTES:
            raise PolicyFormatError(f"document text is longer than {MAX_DOCUMENT_BYTES} characters")

        body_size = self._body_size(lines)
        first_heading = next((i for i, line in enumerate(lines) if self._heading(line, body_size)), None)
        if first_heading is None:
            raise PolicyFormatError(
                "no numbered section headings found; headings must start with a section number "
                "('4.2 Security escort') and be bold or larger than the body text"
            )
        meta = self._document_control(lines[:first_heading], body_size, pdf_title)
        sections = self._sections(lines[first_heading:], body_size)

        return PolicyDocument(
            company_id=meta["company_id"],
            company_name=meta["company_name"],
            doc_id=meta["doc_id"],
            title=meta["title"],
            version=meta["version"],
            effective_from=meta["effective_from"],
            sections=tuple(sections),
            checksum=hashlib.sha256(data).hexdigest(),
        )

    # -- 1-2. reading the file ---------------------------------------------

    def _read(self, data: bytes) -> tuple[list[_Page], str]:
        try:
            pdf = pdfplumber.open(io.BytesIO(data))
        except Exception as exc:
            if _is_password_error(exc):
                raise PolicyFormatError("the PDF is password-protected; upload an unprotected copy") from exc
            raise PolicyFormatError("the file could not be read as a PDF (damaged or incomplete?)") from exc

        with pdf:
            try:
                if len(pdf.pages) > MAX_PAGES:
                    raise PolicyFormatError(f"PDF has more than {MAX_PAGES} pages")
                pages = [self._read_page(page, number) for number, page in enumerate(pdf.pages, start=1)]
                title = str((pdf.metadata or {}).get("Title") or "").strip()
            except PolicyFormatError:
                raise
            except Exception as exc:
                if _is_password_error(exc):
                    raise PolicyFormatError("the PDF is password-protected; upload an unprotected copy") from exc
                raise PolicyFormatError("the PDF's content could not be read (damaged file?)") from exc
        return pages, title

    def _read_page(self, page, number: int) -> _Page:
        result = _Page(number=number, height=float(page.height))
        result.has_images = bool(page.images)
        result.has_text = any(c["text"].strip() for c in page.chars)

        tables = [t for t in page.find_tables() if _is_real_table(t)]
        boxes = [t.bbox for t in tables]

        def outside_tables(obj) -> bool:
            if obj.get("object_type") != "char":
                return True
            cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
            return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

        text_page = page.filter(outside_tables) if boxes else page
        lines = []
        for raw in text_page.extract_text_lines(layout=False, strip=True, return_chars=True):
            line = self._make_line(raw, number)
            if line is not None:
                lines.append(line)
        result.lines.extend(_attach_bullets(lines, _drawn_bullets(page, boxes)))

        for table in tables:
            rows = [[_clean(cell or "") for cell in row] for row in table.extract()]
            rows = [row for row in rows if any(row)]
            if rows:
                x0, top, _, bottom = table.bbox
                text = " ".join(" ".join(row) for row in rows)
                result.lines.append(_Line(number, text, x0, top, bottom, size=0.0, bold=0.0, rows=rows))

        result.lines.sort(key=lambda line: (round(line.top, 1), line.x0))
        return result

    def _make_line(self, raw: dict, page: int) -> _Line | None:
        chars = [c for c in raw["chars"] if c["text"].strip()]
        if not chars:
            return None
        text = raw["text"]
        if _symbol_bullet(chars):
            # Word draws bullets in Symbol or Wingdings, as private-use
            # characters (U+F0B7) that cleaning would delete, or as an "o" in
            # Courier New. Either way the item would lose its bullet.
            text = "• " + text[len(chars[0]["text"]) :].lstrip()
        text = _clean(text)
        if not text:
            return None
        sizes = Counter(round(c["size"] * 2) / 2 for c in chars)
        size = sizes.most_common(1)[0][0]
        bold = sum(1 for c in chars if _BOLD_FONT.search(c.get("fontname", ""))) / len(chars)
        return _Line(
            page, text, float(raw["x0"]), float(raw["top"]), float(raw["bottom"]), size, bold, x1=float(raw["x1"])
        )

    def _refuse_unreadable(self, pages: list[_Page]) -> None:
        scanned = [p.number for p in pages if p.has_images and not p.has_text]
        if scanned:
            listed = ", ".join(map(str, scanned[:10])) + ("..." if len(scanned) > 10 else "")
            raise PolicyFormatError(
                f"page {listed} {'is an image' if len(scanned) == 1 else 'are images'} with no text "
                "(a scan?); run OCR on the PDF or upload the original document"
            )
        text = " ".join(line.text for p in pages for line in p.lines)
        unmapped = len(_CID.findall(text)) - sum(1 for p in pages for line in p.lines if _BULLET.match(line.text))
        if text and unmapped > max(5, len(text) // 200):
            raise PolicyFormatError("the PDF's fonts don't say which letters they draw, so its text can't be read")

    # -- 3. running headers and footers ------------------------------------

    def _drop_running_text(self, pages: list[_Page]) -> list[_Line]:
        def in_margin(line: _Line, page: _Page) -> bool:
            band = page.height * 0.1
            return not line.is_table and (line.top < band or line.bottom > page.height - band)

        def shape(text: str) -> str:
            return re.sub(r"\d+", "#", text.lower())

        seen_on = Counter()
        for page in pages:
            for s in {shape(line.text) for line in page.lines if in_margin(line, page)}:
                seen_on[s] += 1
        repeat_threshold = max(2, (len(pages) + 1) // 2)

        kept = []
        for page in pages:
            for line in page.lines:
                if in_margin(line, page) and (
                    _PAGE_NUMBER.match(line.text) or (len(pages) > 1 and seen_on[shape(line.text)] >= repeat_threshold)
                ):
                    continue
                kept.append(line)
        return kept

    # -- 4. headings --------------------------------------------------------

    def _body_size(self, lines: list[_Line]) -> float:
        weights = Counter()
        for line in lines:
            if not line.is_table:
                weights[line.size] += len(line.text)
        return weights.most_common(1)[0][0]

    def _heading(self, line: _Line, body_size: float) -> tuple[str, str] | None:
        if line.is_table:
            return None
        numbered = _section_number(line.text)
        if numbered is None or _TOC_ENTRY.search(line.text):
            return None
        stands_out = line.size >= body_size + 0.75 or (line.bold >= 0.8 and line.size >= body_size - 0.5)
        if not stands_out:
            return None
        return numbered

    def _continues_heading(self, heading: _Line, line: _Line, body_size: float) -> bool:
        """A long heading wrapped onto a second line in the same style."""
        return (
            not line.is_table
            and line.page == heading.page
            and abs(line.size - heading.size) < 0.3
            and line.bold >= 0.8 * heading.bold
            and (line.size >= body_size + 0.75 or line.bold >= 0.8)
            and line.top - heading.bottom < 0.5 * heading.size
            and _section_number(line.text) is None
            and not _BULLET.match(line.text)
        )

    # -- document control --------------------------------------------------

    def _document_control(self, front: list[_Line], body_size: float, pdf_title: str) -> dict:
        found: dict[str, str] = {}

        def take(label: str, value: str) -> None:
            key = _LABELS.get(re.sub(r"[\s:.]+$", "", " ".join(label.lower().split())))
            if key and value and key not in found:
                found[key] = value.strip()

        for line in front:
            if line.is_table:
                for row in line.rows:
                    cells = [c for c in row if c]
                    if len(cells) >= 2:
                        take(cells[0], " ".join(cells[1:]))
            else:
                match = _LABEL_LINE.match(line.text)
                if match:
                    take(match.group(1), match.group(2))

        if "title" not in found:
            text_lines = [line for line in front if not line.is_table and line.page == 1]
            if text_lines:
                largest = max(line.size for line in text_lines)
                if largest >= body_size + 2:
                    first = next(i for i, line in enumerate(text_lines) if line.size == largest)
                    parts = [text_lines[first].text]
                    for line in text_lines[first + 1 :]:
                        if line.size != largest:
                            break
                        parts.append(line.text)
                    found["title"] = " ".join(parts)
            if "title" not in found and pdf_title:
                found["title"] = pdf_title

        missing = [_FIELD_NAMES[f] for f in _FIELD_NAMES if not found.get(f)]
        if missing:
            raise PolicyFormatError(
                "the document-control block before the first section is missing: " + ", ".join(missing)
            )

        meta: dict = dict(found)
        if not re.fullmatch(r"\d+", found["version"]):
            raise PolicyFormatError(f"version must be a positive whole number like 3, found {found['version']!r}")
        meta["version"] = int(found["version"])
        meta["effective_from"] = _parse_date(found["effective_from"])
        return validate_metadata(meta)

    # -- 5-6. sections and paragraphs --------------------------------------

    def _sections(self, lines: list[_Line], body_size: float) -> list[Section]:
        sections: list[Section] = []
        seen: set[str] = set()
        open_headings: dict[int, tuple[str, str]] = {}
        para_gap = self._paragraph_gap(lines, body_size)

        current: tuple[str, str, tuple[str, ...]] | None = None
        heading_page = 0
        content: list[_Line] = []

        def flush() -> None:
            if current is not None:
                body = _body_text(content, para_gap)
                if body:
                    # The heading's page to the last page holding its text: a
                    # heading left at the foot of page 2 gives (2, 3).
                    pages = (heading_page, max(line.page for line in content))
                    sections.append(Section(current[0], current[1], current[2], body, pages))
            content.clear()

        i = 0
        while i < len(lines):
            line = lines[i]
            heading = self._heading(line, body_size)
            if heading is None:
                if _TOC_ENTRY.search(line.text) and current is None:
                    i += 1
                    continue
                content.append(line)
                i += 1
                continue

            section_id, title = heading
            while i + 1 < len(lines) and self._continues_heading(line, lines[i + 1], body_size):
                i += 1
                title = f"{title} {lines[i].text}"
            i += 1

            depth = section_id.count(".") + 1
            if depth > MAX_SECTION_DEPTH:
                raise PolicyFormatError(f"section {section_id} is nested deeper than {MAX_SECTION_DEPTH} levels")
            if section_id in seen:
                raise PolicyFormatError(f"section {section_id} appears more than once")
            if depth > 1:
                parent = open_headings.get(depth - 1)
                if parent is None or not section_id.startswith(parent[0] + "."):
                    expected = section_id.rsplit(".", 1)[0]
                    raise PolicyFormatError(
                        f"section {section_id} is not inside its parent section: no heading numbered {expected} "
                        "comes before it (headings are read when numbered like '4. Title' or '4.2 Title'; "
                        "'Section 4: Title', '4.0 Title' and 'IV. Title' are read as 4)"
                    )
            seen.add(section_id)

            flush()
            open_headings[depth] = (section_id, title)
            for deeper in [d for d in open_headings if d > depth]:
                del open_headings[deeper]
            path = tuple(f"{sid} {t}" if "." in sid else f"{sid}. {t}" for _, (sid, t) in sorted(open_headings.items()))
            current = (section_id, title, path)
            heading_page = line.page

        flush()
        if not sections:
            raise PolicyFormatError("document has no numbered sections with text")
        return sections

    def _paragraph_gap(self, lines: list[_Line], body_size: float) -> float:
        """Vertical gap above which the next line starts a new paragraph.

        Lines inside a paragraph sit a fixed distance apart (the leading); the
        smallest common gap between consecutive body lines is that distance.
        """
        gaps = [
            b.top - a.bottom
            for a, b in zip(lines, lines[1:], strict=False)
            if a.page == b.page
            and not a.is_table
            and not b.is_table
            and abs(a.size - body_size) < 0.3
            and abs(b.size - body_size) < 0.3
            and b.top > a.bottom - 1
        ]
        line_gap = statistics.quantiles(gaps, n=5)[0] if len(gaps) >= 5 else (min(gaps) if gaps else 0.0)
        return max(line_gap, 0.0) + max(2.0, 0.3 * body_size)


@functools.lru_cache(maxsize=16)
def _parse_cached(data: bytes) -> PolicyDocument:
    """Layout analysis costs about 0.1 s a page and every start re-reads the
    seed files, so a document already read is not read again. The result is
    frozen, so sharing it is safe; refusals are raised again each time."""
    return PdfPolicyParser()._parse(data)


def _body_text(lines: list[_Line], para_gap: float) -> str:
    """Rebuild a section's paragraphs and bullet items from its lines."""
    blocks: list[tuple[str, list[str]]] = []  # (kind, fragments); kind is "p", "li" or "table"
    prev: _Line | None = None
    bullet_x = 0.0

    for line in lines:
        if line.is_table:
            blocks.append(("table", [_table_text(line.rows)]))
            prev = None
            continue
        bullet = _BULLET.match(line.text)
        if bullet:
            blocks.append(("li", [bullet.group(1)]))
            bullet_x = line.x0
        elif prev is None or not blocks or blocks[-1][0] == "table":
            blocks.append(("p", [line.text]))
        else:
            kind = blocks[-1][0]
            if line.page == prev.page:
                same_block = line.top - prev.bottom <= para_gap
                if kind == "li":
                    same_block = same_block and line.x0 > bullet_x + 1
            else:
                # A page break: the paragraph carries on unless it had ended.
                same_block = not _SENTENCE_END.search(prev.text)
            if same_block:
                blocks[-1][1].append(line.text)
            else:
                blocks.append(("p", [line.text]))
        prev = line

    out: list[str] = []
    for i, (kind, fragments) in enumerate(blocks):
        text = _join_lines(fragments)
        if kind == "li":
            text = f"- {text}"
            separator = "\n" if i > 0 and blocks[i - 1][0] == "li" else "\n\n"
        else:
            separator = "\n\n"
        out.append(separator + text if out else text)
    return "".join(out).strip()


def _join_lines(fragments: list[str]) -> str:
    text = fragments[0]
    for nxt in fragments[1:]:
        if text.endswith("­"):
            text = text[:-1] + nxt
        elif len(text) > 1 and text.endswith("-") and text[-2].isalpha() and nxt[:1].islower():
            # "night-" + "shift": keep the hyphen, drop the line break.
            text += nxt
        elif len(text) > 1 and text.endswith("-") and text[-2].isdigit() and nxt[:1].isdigit():
            # "1800-000-" + "222", "5-" + "10 km": a number broken at its hyphen.
            text += nxt
        else:
            text = f"{text} {nxt}"
    return _CID.sub("", text).replace("­", "").strip()


def _table_text(rows: list[list[str]]) -> str:
    """A table as text an embedder and a reader can follow, one row per line."""
    widths = {len(row) for row in rows}
    if len(rows) >= 2 and widths == {2}:
        return "\n".join(f"{a}: {b}" if a and b else (a or b) for a, b in rows)
    if len(rows) >= 2 and len(widths) == 1 and widths.pop() > 2:
        header = rows[0]
        return "\n".join(
            "; ".join(f"{h}: {c}" if h else c for h, c in zip(header, row, strict=True) if c) for row in rows[1:]
        )
    return "\n".join(" ".join(c for c in row if c) for row in rows)


def _section_number(text: str) -> tuple[str, str] | None:
    """The section number and title a heading line starts with, if any.

    "4.2 Escort" and "4. Night travel" are the usual forms. "Section 4:",
    "Clause 4 -", "4.0" and "IV." are read as 4, so the citation is the plain
    number with the heading's title.
    """
    word = _HEADING_WORD.match(text)
    if word:
        text = text[word.end() :]
    roman = _ROMAN_HEADING.match(text) or (re.match(r"^([IVXL]{1,7})\s+(\S.*)$", text) if word else None)
    if roman and _ROMAN.match(roman.group(1)):
        return str(_roman_value(roman.group(1))), roman.group(2).strip()
    match = _NUMBERED.match(text)
    if match is None:
        return None
    section_id = match.group(1)
    if re.fullmatch(r"\d+\.0", section_id):
        section_id = section_id[:-2]
    return section_id, match.group(2).strip()


def _roman_value(numeral: str) -> int:
    values = {"I": 1, "V": 5, "X": 10, "L": 50}
    total = 0
    for i, ch in enumerate(numeral):
        value = values[ch]
        total += -value if i + 1 < len(numeral) and values[numeral[i + 1]] > value else value
    return total


def _symbol_bullet(chars: list[dict]) -> bool:
    """Whether a line starts with a bullet drawn as a symbol-font character."""
    if len(chars) < 2 or len(chars[0]["text"]) != 1 or chars[1]["x0"] - chars[0]["x1"] < 1.5:
        return False
    glyph, font, next_font = chars[0]["text"], chars[0].get("fontname", ""), chars[1].get("fontname", "")
    if unicodedata.category(glyph) == "Co":
        return True
    if _SYMBOL_FONT.search(font) and glyph in "§Øüvq":  # Wingdings bullets mapped to Latin letters
        return True
    return glyph == "o" and "courier" in font.lower() and "courier" not in next_font.lower()


def _drawn_bullets(page, table_boxes: list[tuple]) -> list[tuple[float, float, float, float]]:
    """Small dots and squares drawn as shapes, as browsers draw list bullets."""
    found = []
    for obj in page.curves + page.rects:
        width, height = obj["x1"] - obj["x0"], obj["bottom"] - obj["top"]
        if not (1.5 <= width <= 8 and 1.5 <= height <= 8 and 0.6 <= width / height <= 1.6):
            continue
        cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
        if any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in table_boxes):
            continue
        found.append((float(obj["x0"]), float(obj["x1"]), float(obj["top"]), float(obj["bottom"])))
    return found


def _attach_bullets(lines: list[_Line], shapes: list[tuple[float, float, float, float]]) -> list[_Line]:
    """Put a bullet that sits apart from its text back at the start of the line.

    The marker (a lone glyph line or a drawn shape) must sit just left of the
    line and at its height. Lone glyphs that mark nothing are kept as text.
    """
    glyphs = [line for line in lines if _LONE_BULLET.match(line.text)]
    markers = [(g.x0, g.x1 or g.x0 + g.size * 0.6, g.top, g.bottom, g) for g in glyphs]
    markers += [(*shape, None) for shape in shapes]
    used: set[int] = set()
    out = []
    for line in lines:
        if any(line is g for g in glyphs) or _BULLET.match(line.text):
            out.append(line)
            continue
        for i, (x0, x1, top, bottom, _) in enumerate(markers):
            centre = (top + bottom) / 2
            if (
                i not in used
                and x1 <= line.x0 + 0.5
                and line.x0 - x1 <= 3 * max(line.size, 6)
                and line.top - 1 <= centre <= line.bottom + 1
            ):
                used.add(i)
                # The line now starts at its bullet, as when the glyph is inline,
                # so a wrapped second line still reads as indented under it.
                line.text = f"• {line.text}"
                line.x0 = x0
                break
        out.append(line)
    taken = {id(markers[i][4]) for i in used if markers[i][4] is not None}
    return [line for line in out if id(line) not in taken]


def _is_real_table(table) -> bool:
    """Ruling lines alone (a rule under a header) aren't a table."""
    rows = table.extract()
    return len(rows) >= 1 and max((len(r) for r in rows), default=0) >= 2


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace(" ", " ")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C" or ch == "­")
    return " ".join(text.split())


def _parse_date(value: str) -> date:
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", " ".join(value.split()))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise PolicyFormatError(f"effective from must be a date like 2026-01-31 or 31 January 2026, found {value!r}")


def _is_password_error(exc: BaseException) -> bool:
    seen = exc
    while seen is not None:
        if "password" in type(seen).__name__.lower() or "encrypt" in str(seen).lower():
            return True
        seen = seen.__cause__ or seen.__context__
    return False


class PolicyParser:
    """Picks the parser from the file's content, not its name.

    PDF is the format HR sends. Markdown with front matter is still read, for
    documents written by hand and for tests that need an exact document.
    """

    def __init__(self):
        self._pdf = PdfPolicyParser()
        self._markdown = MarkdownPolicyParser()

    def parse(self, data: bytes | str) -> PolicyDocument:
        if isinstance(data, str):
            return self._markdown.parse(data)
        if data.lstrip()[:5] == b"%PDF-":
            return self._pdf.parse(data)
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise PolicyFormatError("the file is not a PDF") from exc
        if not text.lstrip().startswith("---"):
            raise PolicyFormatError("the file is not a PDF (or a Markdown policy with front matter)")
        return self._markdown.parse(text)
