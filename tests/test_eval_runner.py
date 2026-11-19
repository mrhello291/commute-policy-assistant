"""The evaluation runner writes the JSONL format the brief fixes."""

import argparse
import json

import pytest

from evaluation.run import CASES_FILE, EVAL_SET_FILE, RESULTS_DIR, output_path, run


def _args(**overrides):
    defaults = {"out": None, "name": None, "retriever": None, "embedder": None, "no_headers": False}
    return argparse.Namespace(**{**defaults, **overrides})


def test_plain_run_with_an_llm_writes_the_submission_file():
    assert output_path(_args(), "dense", llm_available=True) == EVAL_SET_FILE


@pytest.mark.parametrize(
    ("overrides", "llm_available", "expected"),
    [
        ({}, False, "retrieval-only.jsonl"),
        ({"retriever": "hybrid"}, True, "hybrid.jsonl"),
        ({"no_headers": True}, True, "dense.jsonl"),
        ({"embedder": "hashing"}, True, "dense.jsonl"),
        ({"name": "before-plain-chunks"}, True, "before-plain-chunks.jsonl"),
    ],
)
def test_other_runs_cannot_overwrite_the_submission_file(overrides, llm_available, expected):
    path = output_path(_args(**overrides), "hybrid" if "retriever" in overrides else "dense", llm_available)
    assert path == RESULTS_DIR / expected
    assert path != EVAL_SET_FILE


def test_runner_writes_the_required_fields(tmp_path):
    out = tmp_path / "results.jsonl"
    args = argparse.Namespace(
        retriever="dense",
        llm="none",
        embedder="hashing",
        cases=str(CASES_FILE),
        only="yam-01,yam-16,med-02",
        no_headers=False,
        name=None,
        out=str(out),
        pace=0,
    )
    summary = run(args)

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["id"] for line in lines] == ["yam-01", "yam-16", "med-02"]
    for line in lines:
        assert set(line) == {"id", "input", "expected", "actual", "pass", "notes"}
        assert isinstance(line["pass"], bool)
    other_company = lines[1]
    assert other_company["actual"]["status"] == "other_company" and other_company["pass"]
    assert summary["config"]["llm_available"] is False
    assert out.with_suffix(".summary.json").exists()
