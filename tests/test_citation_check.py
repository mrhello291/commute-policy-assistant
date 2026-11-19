"""The evaluation's citation check catches each way a citation can be wrong."""

import pytest

from evaluation.citation_check import PdfCitationChecker
from tests.conftest import POLICY_DIR
from tests.test_pdf_parser import Pdf

CASE = {
    "company": "yamuna",
    "expected": {"sections": ["8"], "must_include": ["7\\s*days"], "must_not_include": []},
}
GOOD = {
    "document": "Yamuna Software Services Employee Transport Policy",
    "version": 2,
    "section": "8",
    "title": "No-show policy",
    "page_start": 3,
    "page_end": 3,
    "excerpt": "A no-show is recorded when the employee does not board a booked cab and did not cancel in time.",
}


def check(answer="Your transport is suspended for 7 days.", **changes):
    citation = {**GOOD, **changes}
    return PdfCitationChecker(POLICY_DIR).check(CASE, {"status": "answered", "answer": answer, "citations": [citation]})


def test_a_correct_citation_is_verified_and_supports_the_answer():
    report = check()
    assert (report.verified, report.on_gold, report.problems) == (1, 1, [])
    assert (report.facts_checked, report.facts_supported) == (1, 1)


@pytest.mark.parametrize(
    "changes, problem",
    [
        ({"page_start": 2, "page_end": 2}, "heading not on page 2 (found on [3])"),
        ({"page_end": 9}, "not within the PDF's 3 pages"),
        ({"title": "Guest travel"}, "heading not on page 3"),
        ({"excerpt": "Guests may travel in a company cab."}, "excerpt not found"),
        ({"version": 7}, "no PDF for yamuna v7"),
        ({"document": "Gomti Logistics Commute Policy"}, "is not this PDF's title"),
    ],
)
def test_each_kind_of_wrong_citation_is_reported(changes, problem):
    report = check(**changes)
    assert report.verified == 0
    assert problem in report.problems[0]


def test_a_section_crossing_a_page_break_must_cite_both_pages():
    # Yamuna v2's 3.1 heading sits at the foot of page 1 and its text is on page 2;
    # 6.2 starts on page 2 and finishes on page 3.
    cut_off = {"section": "3.1", "title": "Booking cut-off", "excerpt": "Pickup and drop requests"}
    assert check(**cut_off, page_start=1, page_end=2).problems == []
    assert "excerpt not found on pages 1-1" in check(**cut_off, page_start=1, page_end=1).problems[0]


def test_text_running_onto_the_next_page_must_cite_that_page_too(tmp_path):
    pdf = Pdf(footer="Page {page} of 3").front().heading("1. Booking").line("Trips are booked at least four hours")
    pdf.new_page().para("before the shift starts.").heading("2. Cancelling").para("Cancel an hour ahead.")
    pdf.new_page().heading("3. Safety").para("Call the helpline.")
    (tmp_path / "kaveri").mkdir()
    (tmp_path / "kaveri" / "v1.pdf").write_bytes(pdf.bytes())
    case = {"company": "kaveri", "expected": {"sections": ["1"], "must_include": []}}
    citation = {"document": "Kaveri Foods Transport Policy", "version": 1, "section": "1", "title": "Booking"}
    # Only the start of the section, so the whole-section check is what decides.
    citation["excerpt"] = "Trips are booked at least four hours"

    def problems(start, end, **changes):
        cited = {**citation, "page_start": start, "page_end": end, **changes}
        return (
            PdfCitationChecker(tmp_path)
            .check(case, {"status": "answered", "answer": "", "citations": [cited]})
            .problems
        )

    assert problems(1, 2) == []
    assert "not all on pages 1-1" in problems(1, 1)[0]
    cancelling = {"section": "2", "title": "Cancelling", "excerpt": "Cancel an hour ahead."}
    assert problems(2, 2, **cancelling) == []
    assert "ends before page 3" in problems(2, 3, **cancelling)[0]


def test_a_right_answer_citing_the_wrong_section_is_unsupported():
    wrong = {"section": "9", "title": "Guest travel", "excerpt": "Guests", "page_start": 3, "page_end": 3}
    report = check(**wrong)
    assert report.problems == [] and report.on_gold == 0
    assert report.unsupported == ["7\\s*days"]
