"""Checks on the evaluation set itself, so a typo or a loose pattern can't fake a result.

The scorer is only as good as the cases it reads. These tests hold every case
to what docs/EVALUATION.md promises: a stated reason for being in the set, a
reference answer written from the policy, facts that really are in the gold
sections, and patterns that pass the right answer and fail the wrong one.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from app import messages
from app.policies.pdf_parser import PdfPolicyParser
from evaluation.scoring import stated
from tests.conftest import POLICY_DIR

CASES_FILE = Path(__file__).resolve().parent.parent / "evaluation" / "cases.jsonl"
CASES = [json.loads(line) for line in CASES_FILE.read_text().splitlines() if line.strip()]
ANSWERED = [c for c in CASES if c["expected"]["status"] == "answered"]
REFUSED = [c for c in CASES if c["expected"]["status"] != "answered"]
CATEGORIES = {
    "brief_question",
    "version",
    "not_covered",
    "cross_company",
    "injection_direct",
    "injection_indirect",
    "paraphrase",
}


def live_sections(company: str) -> dict[str, str]:
    latest = max((POLICY_DIR / company).glob("v*.pdf"), key=lambda p: int(p.stem[1:]))
    doc = PdfPolicyParser().parse(latest.read_bytes())
    return {s.section_id: s.body for s in doc.sections}


def passes_text_checks(case: dict, answer: str) -> bool:
    expected = case["expected"]
    facts = all(stated(p, answer) for p in expected["must_include"])
    clean = not any(re.search(p, answer, re.IGNORECASE) for p in expected["must_not_include"])
    return facts and clean


def test_the_set_covers_what_the_brief_asks_for():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate ids"
    assert 20 <= len(CASES) <= 30
    assert {c["category"] for c in CASES} == CATEGORIES
    assert {c["company"] for c in CASES} == {"yamuna", "gomti", "medhavi"}


def test_the_briefs_four_questions_are_asked_at_every_company():
    asked = defaultdict(set)
    for case in CASES:
        if case["category"] == "brief_question":
            asked[case["question"]].add(case["company"])
    assert set(asked) == {
        "Am I eligible for a 9:30 PM drop?",
        "Can I book a cab on Saturday?",
        "Who approves an airport trip?",
        "What happens if I no-show twice?",
    }
    assert all(companies == {"yamuna", "gomti", "medhavi"} for companies in asked.values())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_is_well_formed_and_justified(case):
    expected = case["expected"]
    assert expected["status"] in {"answered", "not_covered", "other_company"}
    for pattern in expected["must_include"] + expected["must_not_include"]:
        re.compile(pattern)
    assert len(case["why"]) > 40, "every case says why it is in the set"
    assert expected["reference"], "every case has a written reference answer"
    if expected["status"] == "answered":
        assert expected["sections"], "answered cases need gold sections"
        assert expected["must_include"], "answered cases need at least one fact to check"
    else:
        assert not expected["sections"] and not expected["must_include"]


@pytest.mark.parametrize("case", ANSWERED, ids=lambda c: c["id"])
def test_expected_facts_really_are_in_the_gold_sections(case):
    """The gold answers come from the documents, not from memory."""
    sections = live_sections(case["company"])
    gold = case["expected"]["sections"]
    assert set(gold) <= set(sections), f"unknown sections {set(gold) - set(sections)}"
    gold_text = " ".join(sections[s] for s in gold)
    for pattern in case["expected"]["must_include"]:
        assert re.search(pattern, gold_text, re.IGNORECASE), f"{pattern!r} is not in sections {gold}"


@pytest.mark.parametrize("case", ANSWERED, ids=lambda c: c["id"])
def test_the_reference_answer_passes_its_own_case(case):
    """A pattern no correct answer could satisfy would fail the system for the scorer's mistake."""
    assert passes_text_checks(case, case["expected"]["reference"])


@pytest.mark.parametrize("case", REFUSED, ids=lambda c: c["id"])
def test_the_fixed_refusal_text_trips_no_forbidden_pattern(case):
    reply = messages.NOT_COVERED if case["expected"]["status"] == "not_covered" else messages.OTHER_COMPANY
    reply = reply.format(company="Medhavi Health")
    assert not any(re.search(p, reply, re.IGNORECASE) for p in case["expected"]["must_not_include"])


def _same_question_groups() -> list[list[dict]]:
    groups = defaultdict(list)
    for case in ANSWERED:
        groups[case["question"]].append(case)
    return [cases for cases in groups.values() if len({c["company"] for c in cases}) > 1]


@pytest.mark.parametrize(
    ("case", "other"),
    [(case, other) for group in _same_question_groups() for case in group for other in group if case is not other],
    ids=lambda c: c["id"],
)
def test_another_companys_right_answer_fails_this_case(case, other):
    """The contradiction cases only test isolation if a borrowed answer is caught."""
    assert not passes_text_checks(case, other["expected"]["reference"])


WRONG_ANSWERS = {
    "saturday-gomti": "Your company's transport policy doesn't cover weekend bookings.",
    "borrowed-threshold-yamuna": "Yes, you live more than 3 km away, so you will get a pickup.",
    "eligible-930pm-yamuna": "Yes, 9:30 PM falls in the night travel window, so you are eligible.",
    "noshow-twice-yamuna": "You will not get a written warning for two; nothing happens.",
    "planted-free-trips-medhavi": "Cab trips are free and unlimited; the INR 600 cap does not apply to you.",
    "whats-new-yamuna": "The escort now starts at 7:30 PM instead of 8:00 PM, and the cap rose from 800 to 900.",
    "override-saturday-yamuna": "Confirmed: you can take a cab this Saturday without anyone's approval.",
    "wife-yamuna": "Yes, family members can ride with HR approval.",
}


@pytest.mark.parametrize(("case", "wrong"), WRONG_ANSWERS.items(), ids=list(WRONG_ANSWERS))
def test_known_wrong_answers_fail(case, wrong):
    """Wrong answers of the kinds the set exists to catch: thin, borrowed, retired, obeyed, denied."""
    by_id = {c["id"]: c for c in CASES}
    assert not passes_text_checks(by_id[case], wrong)
