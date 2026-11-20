"""Checks the PDF parser against PDFs made by other tools than our own generator.

    python scripts/check_pdf_producers.py              # the sample policies, through every tool
    python scripts/check_pdf_producers.py --fixtures   # rebuild tests/fixtures/pdf_producers/*.pdf

The sample PDFs come from `make_policy_pdfs.py`, so passing on them only
shows the parser reads that one layout. This script lays the same Markdown
out the way other tools do and compares what the parser reads with the
source, section by section:

- word:      Markdown -> .docx (pandoc) -> PDF (LibreOffice), as a Word export
- writer:    Markdown -> .odt (pandoc) -> PDF (LibreOffice)
- chrome:    Markdown -> HTML -> Chrome's print to PDF, serif, justified,
             with a running header and a "Page X of Y" footer
- bold-only: the same, with headings at body size and only bold

Needs pandoc, LibreOffice (`soffice`) and Google Chrome on the PATH.
"""

import argparse
import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.errors import PolicyFormatError  # noqa: E402
from app.policies.models import PolicyDocument  # noqa: E402
from app.policies.parser import MarkdownPolicyParser  # noqa: E402
from app.policies.pdf_parser import PdfPolicyParser  # noqa: E402

SOURCE_DIR = ROOT / "data" / "archive" / "markdown-policies"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "pdf_producers"
PRODUCERS = ("word", "writer", "chrome", "bold-only")

_PAGE_CSS = """
@page { size: A4; margin: 24mm 20mm 22mm 20mm;
  @top-left { content: "%(head)s"; font: 8pt Arial, sans-serif; color: #555; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 8pt Arial, sans-serif; }
}
body { font-size: 11pt; margin: 0; }
table { border-collapse: collapse; margin: 6pt 0 12pt; }
td, th { border: 1px solid #999; padding: 3px 6px; text-align: left; }
"""
_STYLE = {
    "chrome": "body { font-family: 'DejaVu Serif', serif; text-align: justify; }"
    "h1 { font-size: 20pt; } h2 { font-size: 14pt; } h3 { font-size: 12pt; }",
    "bold-only": "body { font-family: Arial, sans-serif; } h1 { font-size: 18pt; }"
    "h2, h3 { font-size: 11pt; font-weight: bold; margin: 8pt 0 2pt; }",
}


def layout_markdown(doc: PolicyDocument, raw: str) -> str:
    """The policy as an author would type it: a title, a document-control table, the sections."""
    body = re.sub(r"\A---\n.*?\n---\n", "", raw, flags=re.S).lstrip()
    body = re.sub(r"^# .*\n", "", body, count=1)
    return (
        f"# {doc.title}\n\n"
        "| Field | Value |\n|---|---|\n"
        f"| Company | {doc.company_name} |\n| Company ID | {doc.company_id} |\n"
        f"| Document ID | {doc.doc_id} |\n| Version | {doc.version} |\n"
        f"| Effective from | {doc.effective_from.day} {doc.effective_from:%B %Y} |\n\n" + body
    )


def build(producer: str, doc: PolicyDocument, raw: str, out: Path, work: Path) -> None:
    source = work / "policy.md"
    source.write_text(layout_markdown(doc, raw))
    # -smart keeps straight quotes, so the text matches the source exactly.
    pandoc = ["pandoc", "-f", "markdown-smart", str(source)]
    if producer in ("word", "writer"):
        office = work / f"policy.{'docx' if producer == 'word' else 'odt'}"
        subprocess.run([*pandoc, "-o", str(office)], check=True)
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(work), str(office)],
            check=True,
            capture_output=True,
        )
        shutil.move(work / "policy.pdf", out)
        return
    html = work / "policy.html"
    subprocess.run([*pandoc, "-s", "--metadata", f"pagetitle={doc.title}", "-o", str(html)], check=True)
    css = _PAGE_CSS % {"head": f"{doc.company_name} | Version {doc.version}"}
    html.write_text(html.read_text().replace("</head>", f"<style>{css}{_STYLE[producer]}</style></head>"))
    subprocess.run(
        [
            "google-chrome",
            "--headless=new",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out}",
            html.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
    )


def plain(body: str) -> str:
    from tests.test_sample_pdfs import as_plain_text

    return as_plain_text(body)


def compare(pdf: Path, source: PolicyDocument) -> str:
    try:
        got = PdfPolicyParser().parse(pdf.read_bytes())
    except PolicyFormatError as exc:
        return f"refused: {exc}"
    want = [(s.section_id, s.title) for s in source.sections]
    have = [(s.section_id, s.title) for s in got.sections]
    fields = ("company_id", "company_name", "doc_id", "title", "version", "effective_from")
    wrong_meta = [f for f in fields if getattr(got, f) != getattr(source, f)]
    differ = [s.section_id for s, g in zip(source.sections, got.sections, strict=False) if g.body != plain(s.body)]
    verdict = "sections and titles match" if have == want else f"sections differ: {difflib.ndiff(want, have)}"
    return (
        f"{verdict}; bodies differing: {', '.join(differ) or 'none'}; "
        f"metadata differing: {', '.join(wrong_meta) or 'none'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fixtures", action="store_true", help="rebuild the test fixtures instead")
    args = parser.parse_args()
    missing = [tool for tool in ("pandoc", "soffice", "google-chrome") if shutil.which(tool) is None]
    if missing:
        print(f"needs {', '.join(missing)} on the PATH", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if args.fixtures:
            raw = (FIXTURE_DIR / "source.md").read_text()
            doc = MarkdownPolicyParser().parse(raw)
            for producer in ("word", "writer", "chrome"):
                build(producer, doc, raw, FIXTURE_DIR / f"{producer}.pdf", work)
                print("wrote", (FIXTURE_DIR / f"{producer}.pdf").relative_to(ROOT))
            return 0

        failures = 0
        for path in sorted(SOURCE_DIR.glob("*/v*.md")):
            raw = path.read_text()
            doc = MarkdownPolicyParser().parse(raw)
            for producer in PRODUCERS:
                pdf = work / f"{path.parent.name}-{path.stem}-{producer}.pdf"
                build(producer, doc, raw, pdf, work)
                result = compare(pdf, doc)
                failures += not result.endswith("bodies differing: none; metadata differing: none")
                print(f"{path.parent.name}/{path.stem:3} {producer:10} {result}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
