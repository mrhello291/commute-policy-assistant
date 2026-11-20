"""Runs the evaluation set and writes one JSONL line per case.

    python -m evaluation.run                       # settings from env / .env -> results/latest.jsonl
    python -m evaluation.run --submit              # the same, written to results/eval_set.jsonl
    python -m evaluation.run --retriever dense     # baseline for comparison
    python -m evaluation.run --llm none            # retrieval only, no API key needed
    python -m evaluation.run --rescore evaluation/results/eval_set.jsonl
                                                   # re-score saved answers, no LLM calls
    python -m evaluation.run --llm gemini --retriever hybrid --repeat 5
                                                   # five runs, combined into mean ± std

Each run builds a fresh, private instance of the service in a temporary
directory and loads the bundled policies in version order (so Yamuna goes
v1 -> v2 during setup, exactly like a live update). Results never depend on
whatever state a running server happens to be in.

Output line format (fixed by the brief): id, input, expected, actual, pass, notes.

`cases.jsonl` holds the questions and what a correct answer must contain. A
plain run writes `results/latest.jsonl`. Only `--submit` writes the scored set
the brief asks for, `results/eval_set.jsonl`, and only with a real LLM and the
shipped settings, so trying something out can never replace the submission.
Retrieval-only runs and runs with a changed setting go to their own file.

`--rescore` re-applies the current scoring to the answers already saved in a
results file. Answers vary from run to run; this shows the same answers
scored with today's checks, and costs nothing.

`--repeat N` runs the set N times into `results/repeats/<name>/run-<i>.jsonl`
and combines them into `results/repeats/<name>.summary.json` (see
`evaluation/repeats.py`). Runs already on disk are kept, so the same command
picks up where a spent daily quota stopped it. A run in which the provider
was still rate limiting after the harness's retries is thrown away rather
than scored, because those failures say nothing about the answers.
"""

import argparse
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

from app.answering.service import AnswerResult
from app.auth import Principal, Role
from app.config import EmbedderKind, LLMProvider, RetrieverKind, Settings
from app.container import Container
from app.policies.pdf_parser import PolicyParser
from app.policies.seed import policy_files
from evaluation.citation_check import PdfCitationChecker
from evaluation.repeats import aggregate, load_run, print_aggregate
from evaluation.scoring import score_case, summarise

ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = ROOT / "evaluation" / "cases.jsonl"
RESULTS_DIR = ROOT / "evaluation" / "results"
EVAL_SET_FILE = RESULTS_DIR / "eval_set.jsonl"
LATEST_FILE = RESULTS_DIR / "latest.jsonl"
REPEATS_DIR = RESULTS_DIR / "repeats"
POLICY_DIR = ROOT / "data" / "policies"
RATE_LIMIT_WAIT_S = 30
RATE_LIMIT_ATTEMPTS = 3


class QuotaExhausted(RuntimeError):
    """The provider kept rate limiting after every harness retry."""


def load_cases(path: Path, only: set[str] | None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [c for c in cases if not only or c["id"] in only]


def newest_versions(policy_dir: Path) -> dict[str, int]:
    """Each company's highest policy version among the files the service loads."""
    newest: dict[str, int] = {}
    for path in policy_files(policy_dir):
        doc = PolicyParser().parse(path.read_bytes())
        newest[doc.company_id] = max(newest.get(doc.company_id, 0), doc.version)
    return newest


def to_actual(result: AnswerResult) -> dict:
    d = result.diagnostics
    return {
        "status": result.status.value,
        "answer": result.answer,
        "citations": [
            {
                "company_id": c.company_id,
                "document": c.document,
                "version": c.version,
                "section": c.section,
                "title": c.title,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "excerpt": c.excerpt,
            }
            for c in result.citations
        ],
        "policy_version": result.policy.version if result.policy else None,
        "retrieved_sections": d.retrieved_sections,
        "retrieved_chunk_ids": d.retrieved,
        "top_similarity": d.top_similarity,
        "llm_calls": d.llm_calls,
        "latency_ms": d.total_ms,
        "retrieval_ms": d.retrieval_ms,
        "failure": d.failure,
    }


def build_settings(args: argparse.Namespace, data_dir: Path) -> Settings:
    overrides = {
        "data_dir": data_dir,
        "policy_seed_dir": POLICY_DIR,
        "employee_directory": ROOT / "data" / "employees.json",
    }
    if args.llm:
        overrides["llm_provider"] = LLMProvider(args.llm)
    if args.embedder:
        overrides["embedder"] = EmbedderKind(args.embedder)
    if args.retriever:
        overrides["retriever"] = args.retriever
    if args.no_headers:
        overrides["contextual_headers"] = False
    return Settings(**overrides)


class NotSubmittable(ValueError):
    """--submit was asked for a run that isn't the shipped system."""


def output_path(args: argparse.Namespace, retriever: str, llm_available: bool) -> Path:
    changed = args.retriever or args.embedder or args.no_headers
    if getattr(args, "submit", False):
        if not llm_available or changed or args.out or args.name:
            raise NotSubmittable("--submit needs a real LLM and the shipped settings, with no --out or --name")
        return EVAL_SET_FILE
    if args.out:
        return Path(args.out)
    if args.name:
        return RESULTS_DIR / f"{args.name}.jsonl"
    if not llm_available:
        return RESULTS_DIR / "retrieval-only.jsonl"
    if changed:
        return RESULTS_DIR / f"{retriever}.jsonl"
    return LATEST_FILE


def ask_with_backoff(container: Container, case: dict) -> tuple[AnswerResult, list[str]]:
    """Ask once; if the provider rate-limited us, wait and ask again.

    This retry belongs to the evaluation harness, not the service: it stops a
    free-tier quota from being scored as a wrong answer. Every retry is
    written into the case's notes.
    """
    principal = Principal(f"eval-{case['id']}", case["company"], Role.EMPLOYEE)
    notes = []
    for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
        result = container.answering.ask(principal, case["question"])
        if result.diagnostics.failure != "LLMRateLimited" or attempt == RATE_LIMIT_ATTEMPTS:
            return result, notes
        notes.append(f"rate limited, retried after {RATE_LIMIT_WAIT_S}s")
        time.sleep(RATE_LIMIT_WAIT_S)
    return result, notes


def run(args: argparse.Namespace) -> dict:
    cases = load_cases(Path(args.cases), set(args.only.split(",")) if args.only else None)
    with tempfile.TemporaryDirectory(prefix="policy-eval-") as tmp:
        settings = build_settings(args, Path(tmp))
        with Container(settings) as container:
            return _run_cases(args, cases, settings, container)


def _run_cases(args: argparse.Namespace, cases: list[dict], settings: Settings, container: Container) -> dict:
    """Seed the container's policies, ask every case, score it and write the results."""
    checker = PdfCitationChecker(settings.policy_seed_dir)
    live_versions = newest_versions(settings.policy_seed_dir)
    container.startup()
    llm_available = container.llm.name != "none"
    out = output_path(args, settings.retriever, llm_available)  # refuses a bad --submit before any LLM call
    print(
        f"evaluating {len(cases)} cases | retriever={container.retriever.name} "
        f"llm={container.llm.name} embedder={container.embedder.name}",
        file=sys.stderr,
    )

    rows = []
    for i, case in enumerate(cases, start=1):
        result, harness_notes = ask_with_backoff(container, case)
        # A repeat or a submitted run that the quota stops is thrown away, never scored.
        stop = getattr(args, "stop_on_rate_limit", False) or getattr(args, "submit", False)
        if stop and result.diagnostics.failure == "LLMRateLimited":
            raise QuotaExhausted(f"still rate limited at case {case['id']} ({i}/{len(cases)})")
        actual = to_actual(result)
        citations = checker.check(case, actual)
        score = score_case(case, actual, llm_available, citations, live_versions)
        rows.append(
            {
                "case": case,
                "actual": actual,
                "score": score,
                "citations": citations,
                "notes": harness_notes + score.notes,
            }
        )
        mark = "PASS" if score.passed else "FAIL"
        print(
            f"[{i:2}/{len(cases)}] {mark} {case['id']:8} {actual['status']:13} "
            f"{actual['latency_ms']:7.0f} ms  {'; '.join(rows[-1]['notes'])}",
            file=sys.stderr,
        )
        if llm_available and actual["llm_calls"] and args.pace:
            time.sleep(args.pace)

    config = {
        "retriever": container.retriever.name,
        "llm": container.llm.name,
        "embedder": container.embedder.name,
        "contextual_headers": settings.contextual_headers,
        "llm_available": llm_available,
    }
    return write_results(rows, out, config)


def repeat(args: argparse.Namespace, repeats_dir: Path = REPEATS_DIR) -> dict | None:
    """Run the set until `args.repeat` runs exist, then combine them."""
    name = args.name or f"{args.llm or 'auto'}-{args.retriever or Settings().retriever}"
    run_dir = repeats_dir / name
    paths = [run_dir / f"run-{i}.jsonl" for i in range(1, args.repeat + 1)]
    for i, path in enumerate(paths, start=1):
        if path.exists() and path.with_suffix(".summary.json").exists():
            print(f"run {i}/{args.repeat}: already done, kept ({path.name})", file=sys.stderr)
            continue
        print(f"run {i}/{args.repeat} -> {path}", file=sys.stderr)
        try:
            run(argparse.Namespace(**{**vars(args), "out": str(path), "stop_on_rate_limit": True}))
        except QuotaExhausted as exc:
            print(f"\nstopped: {exc}. Run the same command again later to continue.", file=sys.stderr)
            return None
    agg = aggregate([load_run(path) for path in paths])
    out = repeats_dir / f"{name}.summary.json"
    out.write_text(json.dumps(agg, indent=2) + "\n")
    print_aggregate(agg, out)
    return agg


def write_results(rows: list[dict], out: Path, config: dict) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            case = row["case"]
            line = {
                "id": case["id"],
                "input": {"company": case["company"], "question": case["question"]},
                # The reference answer and the reason the case exists travel with the result,
                # so each line can be judged on its own.
                "expected": {**case["expected"], "category": case["category"], "why": case.get("why", "")},
                "actual": row["actual"],
                "pass": row["score"].passed,
                "notes": "; ".join(row["notes"]) or "ok",
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    summary = summarise(rows)
    summary["config"] = config
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print_summary(summary, out)
    return summary


def rescore(path: Path, policy_dir: Path = POLICY_DIR) -> dict:
    """Score the answers saved in `path` again with the current checks, and rewrite it and its summary.

    Only for runs on the current policies: the citation check reads today's
    PDFs, so an old run on different documents would be judged against the
    wrong text.
    """
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    summary_file = path.with_suffix(".summary.json")
    config = json.loads(summary_file.read_text()).get("config", {}) if summary_file.exists() else {}
    llm_available = config.get("llm_available", True)
    checker = PdfCitationChecker(policy_dir)
    live_versions = newest_versions(policy_dir)

    rows = []
    for line in lines:
        expected = {k: v for k, v in line["expected"].items() if k not in ("category", "why")}
        case = {
            "id": line["id"],
            **line["input"],
            "category": line["expected"]["category"],
            "why": line["expected"].get("why", ""),
            "expected": expected,
        }
        actual = line["actual"]
        citations = checker.check(case, actual)
        score = score_case(case, actual, llm_available, citations, live_versions)
        # Notes the harness added while running (rate-limit retries) aren't scores; keep them.
        harness = [n for n in line["notes"].split("; ") if n.startswith("rate limited")]
        rows.append(
            {"case": case, "actual": actual, "score": score, "citations": citations, "notes": harness + score.notes}
        )
    summary = write_results(rows, path, config)
    print(f"re-scored {len(rows)} saved answers in {path}", file=sys.stderr)
    return summary


def print_summary(summary: dict, out: Path) -> None:
    r = summary["retrieval"]
    print(f"\npassed {summary['passed']}/{summary['cases']} ({summary['pass_rate']:.0%})")
    for name, c in summary["by_category"].items():
        print(f"  {name:20} {c['passed']}/{c['cases']}")
    print(
        f"retrieval  gold first {r['gold_first']}/{r['cases_with_gold']}  "
        f"gold in top 3 {r['gold_in_top_3']}/{r['cases_with_gold']}  "
        f"hit@1={r['hit_at_1']}  hit@3={r['hit_at_3']}  mrr={r['mrr']}  "
        f"recall@3={r['recall_at_3']}  precision@3={r['precision_at_3']} "
        f"(best possible {r['precision_at_3_best_possible']})"
    )
    if r["missed"]:
        print(f"           no gold section in the top 3: {', '.join(r['missed'])}")
    gate = r["evidence_gate"]
    print(
        f"gate       stopped {gate['not_covered_stopped']}/{gate['not_covered_cases']} uncovered questions "
        f"before the LLM; answerable questions stopped: {', '.join(gate['answerable_stopped']) or 'none'}"
    )
    print("requirements (cases where it held)")
    for name, req in summary["requirements"].items():
        if req["cases"]:
            print(f"  {name:24} {req['held']}/{req['cases']}")
    c = summary["citations"]
    if c["total"]:
        print(
            f"citations  {c['verified_against_pdf']}/{c['total']} match the PDF  "
            f"{c['on_gold_section']}/{c['total']} on an expected section  "
            f"facts in cited text {c['facts_supported_by_citations']}/{c['facts_checked']}"
        )
    print(f"leaks      {summary['leaks']}")
    print(f"llm calls  max={summary['llm_calls']['max']}  mean={summary['llm_calls']['mean']}")
    lat = summary["latency_ms"]
    print(f"latency    p50={lat['p50']} ms  p95={lat['p95']} ms  max={lat['max']} ms")
    print(f"results -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retriever", choices=[k.value for k in RetrieverKind], help="default: from settings")
    parser.add_argument("--llm", choices=[p.value for p in LLMProvider], help="default: from settings")
    parser.add_argument("--embedder", choices=[e.value for e in EmbedderKind], help="default: minilm")
    parser.add_argument("--no-headers", action="store_true", help="index plain section text (the 'before' chunking)")
    parser.add_argument("--rescore", metavar="RESULTS", help="re-score the answers saved in a results file")
    parser.add_argument("--cases", default=str(CASES_FILE))
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--name", help="results file name (default: the retriever name)")
    parser.add_argument("--out", help="explicit output path")
    parser.add_argument("--repeat", type=int, metavar="N", help="run N times and combine (resumes if stopped)")
    parser.add_argument(
        "--submit", action="store_true", help="write the submitted results/eval_set.jsonl (shipped settings only)"
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=8.0,
        help="seconds to wait after each LLM case; Groq's free tier allows about 8K tokens a minute",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.submit and (args.repeat or args.rescore):
        parser.error("--submit is a single plain run; it can't be combined with --repeat or --rescore")
    if args.rescore:
        rescore(Path(args.rescore))
    elif args.repeat:
        if repeat(args) is None:
            sys.exit(1)
    else:
        try:
            run(args)
        except QuotaExhausted as exc:
            print(f"\nstopped: {exc}. Nothing was written; run the same command again later.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
