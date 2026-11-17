"""The evaluation runner writes the JSONL format the brief fixes."""

import argparse
import json

from evaluation.run import CASES_FILE, run


def test_runner_writes_the_required_fields(tmp_path):
    out = tmp_path / "results.jsonl"
    args = argparse.Namespace(
        retriever="dense",
        llm="none",
        embedder="hashing",
        cases=str(CASES_FILE),
        only="arc-01,arc-16,cor-02",
        no_headers=False,
        name=None,
        out=str(out),
        pace=0,
    )
    summary = run(args)

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["id"] for line in lines] == ["arc-01", "arc-16", "cor-02"]
    for line in lines:
        assert set(line) == {"id", "input", "expected", "actual", "pass", "notes"}
        assert isinstance(line["pass"], bool)
    other_company = lines[1]
    assert other_company["actual"]["status"] == "other_company" and other_company["pass"]
    assert summary["config"]["llm_available"] is False
    assert out.with_suffix(".summary.json").exists()
