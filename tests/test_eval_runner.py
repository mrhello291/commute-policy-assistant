"""The evaluation runner writes the JSONL format the brief fixes."""

import argparse
import json

import pytest

from evaluation.run import CASES_FILE, EVAL_SET_FILE, LATEST_FILE, RESULTS_DIR, NotSubmittable, output_path, run


def _args(**overrides):
    defaults = {"out": None, "name": None, "retriever": None, "embedder": None, "no_headers": False, "submit": False}
    return argparse.Namespace(**{**defaults, **overrides})


def test_only_submit_writes_the_submission_file():
    assert output_path(_args(submit=True), "dense", llm_available=True) == EVAL_SET_FILE
    assert output_path(_args(), "dense", llm_available=True) == LATEST_FILE


@pytest.mark.parametrize(
    ("overrides", "llm_available"),
    [
        ({}, False),
        ({"retriever": "hybrid"}, True),
        ({"no_headers": True}, True),
        ({"embedder": "hashing"}, True),
        ({"name": "try-this"}, True),
        ({"out": "x.jsonl"}, True),
    ],
)
def test_submit_refuses_anything_but_the_shipped_system(overrides, llm_available):
    with pytest.raises(NotSubmittable):
        output_path(_args(submit=True, **overrides), "dense", llm_available)


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
def test_runs_without_submit_cannot_overwrite_the_submission_file(overrides, llm_available, expected):
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


def test_rescore_gives_the_same_scores_for_the_same_answers(tmp_path):
    from evaluation.run import rescore

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
    first = run(args)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    lines[0]["notes"] = "rate limited, retried after 30s; " + lines[0]["notes"]
    out.write_text("".join(json.dumps(line) + "\n" for line in lines))

    again = rescore(out)

    rescored = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["pass"] for line in rescored] == [line["pass"] for line in lines]
    assert [line["actual"] for line in rescored] == [line["actual"] for line in lines]
    assert rescored[0]["notes"].startswith("rate limited, retried after 30s")
    assert again["config"] == first["config"]
    assert again["requirements"]["newest_version_only"] == {"held": 3, "cases": 3}
