import pytest

from evaluation.scoring import first_gold_rank, percentile, score_case, stated, summarise

CASE = {
    "id": "x",
    "company": "yamuna",
    "category": "direct",
    "question": "Escort time?",
    "expected": {
        "status": "answered",
        "sections": ["4.2"],
        "must_include": [r"7:30\s*PM"],
        "must_not_include": [r"10:00\s*PM"],
    },
}


def actual(**overrides):
    base = {
        "status": "answered",
        "answer": "An escort travels on drops after 7:30 PM.",
        "citations": [{"section": "4.2", "company_id": "yamuna"}],
        "retrieved_sections": ["4.1", "4.2", "4.3"],
        "retrieved_chunk_ids": ["yamuna:a", "yamuna:b", "yamuna:c"],
        "llm_calls": 1,
        "latency_ms": 900.0,
    }
    base.update(overrides)
    return base


def test_correct_answer_passes():
    score = score_case(CASE, actual())
    assert score.passed and score.first_gold_rank == 2


def test_missing_fact_fails():
    score = score_case(CASE, actual(answer="An escort is provided at night."))
    assert not score.passed and not score.checks["facts"]


@pytest.mark.parametrize(
    ("answer", "pattern", "expected"),
    [
        ("Two no-shows lead to a written warning.", "warning", True),
        ("You will not get a written warning.", "warning", False),
        ("You do not need L2 approval.", "L2", False),
        ("No, you need approval from your L2 manager.", "L2", True),
        ("Yes, but without L2 approval it is not booked.", "L2", False),
        ("It is not free; each trip is capped at INR 600.", r"\b600\b", True),
        ("Company transport is not provided on Saturdays.", "not provided", True),
        ("You don't need anything; L2 approval is automatic.", "L2", True),
        ("You don't need HR approval, only L2 approval.", "L2", True),
    ],
)
def test_a_fact_counts_only_where_the_answer_states_it(answer, pattern, expected):
    assert stated(pattern, answer) is expected


def test_a_denied_fact_fails_and_says_so():
    score = score_case(CASE, actual(answer="The escort does not start at 7:30 PM."))
    assert not score.checks["facts"]
    assert any("only denied" in note for note in score.notes)


def test_other_companys_value_is_a_leak():
    score = score_case(CASE, actual(answer="After 7:30 PM, or 10:00 PM on weekends."))
    assert not score.passed and not score.checks["no_leak"]


def test_wrong_citation_fails():
    score = score_case(CASE, actual(citations=[{"section": "9", "company_id": "yamuna"}]))
    assert not score.checks["citation"]


def test_foreign_chunk_fails_isolation():
    score = score_case(CASE, actual(retrieved_chunk_ids=["yamuna:a", "gomti:b"]))
    assert not score.passed and not score.checks["isolation"]


def test_over_budget_fails():
    assert not score_case(CASE, actual(llm_calls=3)).checks["budget"]


def test_wrong_status_fails():
    assert not score_case(CASE, actual(status="not_covered", answer="", citations=[])).passed


def test_without_llm_only_retrieval_is_judged():
    score = score_case(CASE, actual(status="try_again", answer="", citations=[]), llm_available=False)
    assert score.passed and "status" not in score.checks
    assert "not scored" in " ".join(score.notes)


def test_rank_and_percentile_helpers():
    assert first_gold_rank(["2.1", "8"], ["5", "8", "2.1"]) == 2
    assert first_gold_rank(["2.1"], ["5"]) is None
    assert percentile([100, 200, 300, 400], 50) == 200
    assert percentile(list(range(1, 101)), 95) == 95


def test_summary_counts():
    rows = [
        {"case": CASE, "actual": actual(), "score": score_case(CASE, actual())},
        {"case": CASE, "actual": actual(llm_calls=2), "score": score_case(CASE, actual(answer="10:00 PM"))},
    ]
    summary = summarise(rows)
    assert summary["passed"] == 1 and summary["leaks"] == 1
    assert summary["retrieval"]["hit_at_3"] == 1.0 and summary["retrieval"]["mrr"] == 0.5
    assert summary["llm_calls"] == {"max": 2, "mean": 1.5}


def test_newest_version_only_passes_when_everything_is_from_the_live_version():
    live = actual(
        policy_version=2,
        retrieved_chunk_ids=["yamuna:doc:v2:4.2:0", "yamuna:doc:v2:4.1:0"],
        citations=[{"section": "4.2", "company_id": "yamuna", "version": 2}],
    )
    assert score_case(CASE, live, live_versions={"yamuna": 2}).checks["live_version"]


def test_a_retired_versions_chunk_or_citation_fails_newest_version_only():
    stale_chunk = actual(policy_version=2, retrieved_chunk_ids=["yamuna:doc:v2:4.2:0", "yamuna:doc:v1:4.2:0"])
    stale_cite = actual(policy_version=2, citations=[{"section": "4.2", "company_id": "yamuna", "version": 1}])
    stale_live = actual(policy_version=1)
    for case_actual in (stale_chunk, stale_cite, stale_live):
        score = score_case(CASE, case_actual, live_versions={"yamuna": 2})
        assert not score.checks["live_version"] and not score.passed
        assert any("newest" in note for note in score.notes)


def test_newest_version_is_not_judged_without_the_loaded_versions():
    assert "live_version" not in score_case(CASE, actual()).checks


def _row(case, act, **kwargs):
    return {"case": case, "actual": act, "score": score_case(case, act, **kwargs)}


def test_retrieval_summary_counts_hits_recall_precision_and_misses():
    two_gold = {**CASE, "id": "two", "expected": {**CASE["expected"], "sections": ["4.2", "9"]}}
    missed = {**CASE, "id": "missed", "expected": {**CASE["expected"], "sections": ["7"]}}
    rows = [
        _row(CASE, actual(retrieved_sections=["4.2", "4.1", "4.3"])),  # gold first
        _row(two_gold, actual(retrieved_sections=["4.1", "4.2", "4.3"])),  # one of two gold, rank 2
        _row(missed, actual(retrieved_sections=["4.1", "4.2", "4.3"])),  # no gold
    ]
    r = summarise(rows)["retrieval"]
    assert (r["cases_with_gold"], r["gold_first"], r["gold_in_top_3"]) == (3, 1, 2)
    assert r["missed"] == ["missed"]
    assert r["recall_at_3"] == round((1 + 0.5 + 0) / 3, 3)
    assert r["precision_at_3"] == round((1 / 3 + 1 / 3 + 0) / 3, 3)
    assert r["precision_at_3_best_possible"] == round((1 / 3 + 2 / 3 + 1 / 3) / 3, 3)


def test_evidence_gate_reports_answerable_questions_it_stopped():
    uncovered = {**CASE, "id": "pets", "expected": {**CASE["expected"], "status": "not_covered", "sections": []}}
    gated = {"status": "not_covered", "answer": "", "citations": [], "failure": "below_evidence_threshold"}
    rows = [_row(uncovered, actual(**gated)), _row(CASE, actual(**gated))]
    gate = summarise(rows)["retrieval"]["evidence_gate"]
    assert gate == {"answerable_stopped": ["x"], "not_covered_stopped": 1, "not_covered_cases": 1}


def test_requirements_count_the_cases_each_property_held_for():
    refused_case = {**CASE, "id": "other", "expected": {**CASE["expected"], "status": "other_company", "sections": []}}
    rows = [
        _row(CASE, actual(policy_version=2, retrieved_chunk_ids=["yamuna:d:v2:4.2:0"]), live_versions={"yamuna": 2}),
        _row(CASE, actual(citations=[], retrieved_chunk_ids=["yamuna:d:v2:4.1:0"]), live_versions={"yamuna": 2}),
        _row(refused_case, actual(status="other_company", answer="", citations=[], retrieved_chunk_ids=[])),
    ]
    req = summarise(rows)["requirements"]
    assert req["answers_with_citation"] == {"held": 1, "cases": 2}
    assert req["other_company_refused"] == {"held": 1, "cases": 1}
    assert req["own_company_only"] == {"held": 3, "cases": 3}
    assert req["newest_version_only"] == {"held": 2, "cases": 2}
