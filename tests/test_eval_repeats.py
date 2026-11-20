"""Repeated evaluation runs and how they're combined."""

import argparse
import copy

import pytest

import evaluation.run as runner
from evaluation.repeats import aggregate
from evaluation.run import CASES_FILE, QuotaExhausted, repeat


def _args(**overrides):
    defaults = {
        "retriever": "dense",
        "llm": "none",
        "embedder": "hashing",
        "cases": str(CASES_FILE),
        "only": "yam-01,yam-16",
        "no_headers": False,
        "name": "test",
        "out": None,
        "pace": 0,
        "repeat": 2,
    }
    return argparse.Namespace(**{**defaults, **overrides})


def _run(passes: list[bool], held: int = 2, config: dict | None = None):
    lines = [
        {"id": f"c{i}", "pass": ok, "actual": {"status": "answered" if ok else "try_again"}}
        for i, ok in enumerate(passes)
    ]
    summary = {
        "passed": sum(passes),
        "by_category": {"direct": {"passed": sum(passes), "cases": len(passes)}},
        "requirements": {"own_company_only": {"held": held, "cases": 2}},
        "citations": {
            "total": 1,
            "verified_against_pdf": 1,
            "on_gold_section": 1,
            "facts_checked": 1,
            "facts_supported_by_citations": 1,
        },
        "leaks": 0,
        "retrieval": {"gold_first": 1, "gold_in_top_3": 2, "cases_with_gold": 2, "mrr": 0.75},
        "llm_calls": {"mean": 1.0},
        "latency_ms": {"p50": 800.0, "p95": 1200.0},
        "config": config or {"llm": "x", "retriever": "dense"},
    }
    return lines, summary


def test_pass_counts_become_mean_std_and_range():
    agg = aggregate([_run([True, True]), _run([True, False]), _run([True, True])])
    assert agg["runs"] == 3
    assert agg["passed"]["per_run"] == [2, 1, 2]
    assert agg["passed"]["mean"] == 1.67
    assert agg["passed"]["std"] == 0.58
    assert (agg["passed"]["min"], agg["passed"]["max"]) == (1, 2)
    assert agg["per_case_passed"] == {"c0": 3, "c1": 2}
    assert agg["flaky"] == ["c1 2/3"]
    assert agg["try_again"]["per_run"] == [0, 1, 0]


def test_wrong_answers_are_counted_apart_from_timeouts():
    timed_out = _run([True, False])
    wrong = _run([False, True])
    wrong[0][0]["actual"]["status"] = "not_covered"
    agg = aggregate([timed_out, wrong])
    assert agg["try_again"]["per_run"] == [1, 0]
    assert agg["wrong_answers"]["per_run"] == [0, 1]
    assert agg["wrong_answers_by_case"] == {"c0": 1}


def test_a_property_broken_in_one_run_is_reported_as_broken():
    agg = aggregate([_run([True, True]), _run([True, True], held=1)])
    assert agg["requirements"]["own_company_only"] == {"held": 3, "cases": 4, "held_in_every_run": False}


def test_retrieval_is_checked_to_be_the_same_in_every_run():
    first, second = _run([True, True]), _run([True, True])
    assert aggregate([first, second])["retrieval"]["same_in_every_run"]
    changed = copy.deepcopy(second)
    changed[1]["retrieval"]["gold_first"] = 2
    assert not aggregate([first, changed])["retrieval"]["same_in_every_run"]


def test_runs_with_different_settings_are_not_combined():
    with pytest.raises(ValueError, match="different settings"):
        aggregate([_run([True]), _run([True], config={"llm": "y", "retriever": "dense"})])


def test_repeat_writes_each_run_and_a_combined_summary(tmp_path):
    agg = repeat(_args(), repeats_dir=tmp_path)
    assert (tmp_path / "test" / "run-1.jsonl").exists()
    assert (tmp_path / "test" / "run-2.jsonl").exists()
    assert (tmp_path / "test.summary.json").exists()
    assert agg["runs"] == 2 and agg["retrieval"]["same_in_every_run"]


def test_repeat_keeps_finished_runs_and_only_adds_missing_ones(tmp_path):
    repeat(_args(), repeats_dir=tmp_path)
    first = (tmp_path / "test" / "run-1.jsonl").stat().st_mtime_ns
    agg = repeat(_args(repeat=3), repeats_dir=tmp_path)
    assert (tmp_path / "test" / "run-1.jsonl").stat().st_mtime_ns == first
    assert agg["runs"] == 3


def test_a_spent_quota_stops_without_scoring_the_run(tmp_path, monkeypatch):
    real_run = runner.run
    calls = []

    def run_then_run_out(args):
        calls.append(args.out)
        if len(calls) == 2:
            raise QuotaExhausted("still rate limited at case yam-01 (1/2)")
        return real_run(args)

    monkeypatch.setattr(runner, "run", run_then_run_out)
    assert repeat(_args(), repeats_dir=tmp_path) is None
    assert (tmp_path / "test" / "run-1.jsonl").exists()
    assert not (tmp_path / "test" / "run-2.jsonl").exists()
    assert not (tmp_path / "test.summary.json").exists()
