"""The evaluation set we submit, checked as committed.

The brief fixes its format so submissions can be compared side by side. The
runner is tested separately; this guards the file itself, so a hand edit or
an experiment written to the wrong place can't go unnoticed.
"""

import json

from evaluation.run import CASES_FILE, EVAL_SET_FILE

REQUIRED_FIELDS = ["id", "input", "expected", "actual", "pass", "notes"]

LINES = [json.loads(line) for line in EVAL_SET_FILE.read_text().splitlines() if line.strip()]
CASES = [json.loads(line) for line in CASES_FILE.read_text().splitlines() if line.strip()]


def test_every_line_has_exactly_the_required_fields():
    assert len(LINES) >= 10
    for line in LINES:
        assert list(line) == REQUIRED_FIELDS, line.get("id")
        assert isinstance(line["pass"], bool)
        assert isinstance(line["notes"], str)


def test_it_scores_every_case_with_the_same_question():
    assert [line["id"] for line in LINES] == [case["id"] for case in CASES]
    for line, case in zip(LINES, CASES, strict=True):
        assert line["input"] == {"company": case["company"], "question": case["question"]}


def test_summary_matches_the_lines():
    summary = json.loads(EVAL_SET_FILE.with_suffix(".summary.json").read_text())
    assert summary["cases"] == len(LINES)
    assert summary["passed"] == sum(line["pass"] for line in LINES)
    assert summary["leaks"] == 0


def test_the_properties_that_must_always_hold_held_in_every_case():
    """Own company only, newest version only, every answer cited, every citation matching the PDF."""
    requirements = json.loads(EVAL_SET_FILE.with_suffix(".summary.json").read_text())["requirements"]
    for name, counts in requirements.items():
        assert counts["cases"] > 0, name
        assert counts["held"] == counts["cases"], name
