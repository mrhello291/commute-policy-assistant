"""Checks the citations in an answer against the policy PDF they point to.

The scorer's citation check only asks "did the answer cite one of the
expected sections?". This module asks whether each citation is *correct*:

1. **Verified against the PDF.** The cited document and version exist; the
   section's number and title are printed on the cited start page; the
   quoted excerpt and the section's whole text are on the cited pages; and
   the range is no wider than it needs to be. This reads the PDF's raw page text with pdfplumber,
   not the service's parser, so a parser bug that put text in the wrong
   section or on the wrong page would show up here instead of being trusted.
2. **On an expected section** (precision): how many citations point at one of
   the case's gold sections.
3. **Supports the answer.** Every required fact the answer states (the
   case's `must_include` patterns) appears in the text of a cited section. A
   right answer that cites the wrong section fails this.

Only (1) is a pass condition: a citation that doesn't match the document is
wrong whatever the answer says. (2) and (3) are reported as rates, because
gold lists and fact patterns were written for answers, not for every section
that could reasonably be cited.
"""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pdfplumber

from app.policies.models import PolicyDocument
from app.policies.pdf_parser import PdfPolicyParser

_CID = re.compile(r"\(cid:\d+\)")


def squash(text: str) -> str:
    """Letters and digits only, lower case: immune to line breaks, bullets and spacing."""
    return re.sub(r"[^a-z0-9]", "", _CID.sub("", text).lower())


@dataclass
class CitationReport:
    total: int = 0
    verified: int = 0
    on_gold: int = 0
    facts_checked: int = 0
    facts_supported: int = 0
    problems: list[str] = field(default_factory=list)  # citations that don't match the PDF
    unsupported: list[str] = field(default_factory=list)  # facts not found in any cited section


class PdfCitationChecker:
    def __init__(self, policy_dir: Path):
        self._policy_dir = policy_dir

    def check(self, case: dict, actual: dict) -> CitationReport:
        report = CitationReport()
        citations = actual.get("citations", [])
        gold = set(case["expected"]["sections"])
        cited_text = []

        for c in citations:
            report.total += 1
            report.on_gold += c["section"] in gold
            source = self._source(case["company"], c["version"])
            if source is None:
                report.problems.append(f"§{c['section']}: no PDF for {case['company']} v{c['version']}")
                continue
            pages, doc = source
            problem = self._verify(c, pages, doc)
            if problem:
                report.problems.append(f"§{c['section']}: {problem}")
            else:
                report.verified += 1
            section = next((s for s in doc.sections if s.section_id == c["section"]), None)
            if section is not None:
                cited_text.append(section.body)

        if actual.get("status") == "answered" and citations:
            cited = " ".join(cited_text)
            for pattern in case["expected"].get("must_include", []):
                if re.search(pattern, actual.get("answer", ""), re.IGNORECASE):
                    report.facts_checked += 1
                    if re.search(pattern, cited, re.IGNORECASE):
                        report.facts_supported += 1
                    else:
                        report.unsupported.append(pattern)
        return report

    def _verify(self, c: dict, pages: list[str], doc: PolicyDocument) -> str | None:
        if c["document"] != doc.title:
            return f"document {c['document']!r} is not this PDF's title {doc.title!r}"
        start, end = c.get("page_start"), c.get("page_end")
        if not start or not end or not 1 <= start <= end <= len(pages):
            return f"page range {start}-{end} is not within the PDF's {len(pages)} pages"
        heading = squash(f"{c['section']} {c['title']}")
        if heading not in pages[start - 1]:
            found = [i + 1 for i, text in enumerate(pages) if heading in text]
            return f"heading not on page {start} (found on {found or 'no page'})"
        cited_pages = "".join(pages[start - 1 : end])
        if squash(c["excerpt"].removesuffix("…")) not in cited_pages:
            return f"excerpt not found on pages {start}-{end}"
        section = next((s for s in doc.sections if s.section_id == c["section"]), None)
        if section is None:
            return "section not in the document"
        body = squash(section.body)
        if body not in cited_pages:
            return f"the section's text is not all on pages {start}-{end}"
        if end > start and body in "".join(pages[start - 1 : end - 1]):
            return f"section ends before page {end}"
        return None

    def _source(self, company: str, version: int) -> tuple[list[str], PolicyDocument] | None:
        path = self._policy_dir / company / f"v{version}.pdf"
        return _read(path) if path.is_file() else None


@cache
def _read(path: Path) -> tuple[list[str], PolicyDocument]:
    """Raw text of each page with running headers and footers removed, plus the parsed document."""
    with pdfplumber.open(path) as pdf:
        page_lines = [(page.extract_text() or "").splitlines() for page in pdf.pages]

    def shape(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip().lower())

    if len(page_lines) > 1:
        on_every_page = set.intersection(*({shape(line) for line in lines} for lines in page_lines))
        page_lines = [[line for line in lines if shape(line) not in on_every_page] for lines in page_lines]
    pages = [squash(" ".join(lines)) for lines in page_lines]
    return pages, PdfPolicyParser().parse(path.read_bytes())
