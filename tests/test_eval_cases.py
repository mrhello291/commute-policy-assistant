"""Sanity checks on the evaluation set itself, so a typo can't fake a result."""

import json
import re
from pathlib import Path

import pytest

from app.policies.pdf_parser import PdfPolicyParser
from tests.conftest import POLICY_DIR

CASES_FILE = Path(__file__).resolve().parent.parent / "evaluation" / "cases.jsonl"
CASES = [json.loads(line) for line in CASES_FILE.read_text().splitlines() if line.strip()]


def live_sections(company: str) -> dict[str, str]:
    latest = max((POLICY_DIR / company).glob("v*.pdf"), key=lambda p: int(p.stem[1:]))
    doc = PdfPolicyParser().parse(latest.read_bytes())
    return {s.section_id: s.body for s in doc.sections}


def test_ids_are_unique_and_set_is_big_enough():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) >= 10
    categories = {c["category"] for c in CASES}
    assert {"paraphrase", "cross_company", "not_covered"} <= categories


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_is_well_formed(case):
    expected = case["expected"]
    assert expected["status"] in {"answered", "not_covered", "other_company"}
    for pattern in expected["must_include"] + expected["must_not_include"]:
        re.compile(pattern)
    if expected["status"] == "answered":
        assert expected["sections"], "answered cases need gold sections"
        assert expected["must_include"], "answered cases need at least one fact to check"


@pytest.mark.parametrize("case", [c for c in CASES if c["expected"]["status"] == "answered"], ids=lambda c: c["id"])
def test_expected_facts_really_are_in_the_gold_sections(case):
    """Verifies the gold answers against the documents, not from memory."""
    sections = live_sections(case["company"])
    gold = case["expected"]["sections"]
    assert set(gold) <= set(sections), f"unknown sections {set(gold) - set(sections)}"
    gold_text = " ".join(sections[s] for s in gold)
    for pattern in case["expected"]["must_include"]:
        assert re.search(pattern, gold_text, re.IGNORECASE) or pattern.startswith(r"\b(no|"), (
            f"{pattern!r} is not supported by sections {gold}"
        )
