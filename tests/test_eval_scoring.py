from evaluation.scoring import first_gold_rank, percentile, score_case, summarise

CASE = {
    "id": "x",
    "company": "arcadia",
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
        "citations": [{"section": "4.2", "company_id": "arcadia"}],
        "retrieved_sections": ["4.1", "4.2", "4.3"],
        "retrieved_chunk_ids": ["arcadia:a", "arcadia:b", "arcadia:c"],
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


def test_other_companys_value_is_a_leak():
    score = score_case(CASE, actual(answer="After 7:30 PM, or 10:00 PM on weekends."))
    assert not score.passed and not score.checks["no_leak"]


def test_wrong_citation_fails():
    score = score_case(CASE, actual(citations=[{"section": "9", "company_id": "arcadia"}]))
    assert not score.checks["citation"]


def test_foreign_chunk_fails_isolation():
    score = score_case(CASE, actual(retrieved_chunk_ids=["arcadia:a", "bluepeak:b"]))
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
