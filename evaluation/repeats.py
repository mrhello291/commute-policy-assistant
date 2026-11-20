"""Combines several runs of the same evaluation into one summary.

Answers vary from run to run (the model isn't fully deterministic even at
temperature 0, and provider latency decides which questions hit the
deadline), so one run's score is a sample. This reports what varies as a
mean, standard deviation and range, and what must not vary as a count over
every case of every run:

- **Pass count**: mean ± sample standard deviation, min and max.
- **Why cases failed**: a "try again" means the provider didn't reply before
  the deadline; any other failure is a wrong answer (wrong status, a missing
  or forbidden fact, a bad citation). Counted separately, because the first
  measures the provider's speed and the second the answers.
- **Per case**: how many runs it passed in. A case that passed in some runs
  and failed in others is listed as flaky.
- **Must-hold properties** (own company only, newest version, cited,
  citations matching the PDF, nothing forbidden): held/cases summed over all
  runs, plus whether they held in every run.
- **Retrieval**: doesn't depend on the model, so it's reported once, with a
  check that every run retrieved the same thing.
- **Latency**: each run's p50 and p95, as a median and range across runs.
"""

import json
import statistics
from pathlib import Path


def spread(values: list[float]) -> dict:
    return {
        "mean": round(statistics.mean(values), 2),
        "std": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "per_run": values,
    }


def middle(values: list[float]) -> dict:
    return {"median": round(statistics.median(values), 1), "min": min(values), "max": max(values)}


def load_run(path: Path) -> tuple[list[dict], dict]:
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    summary = json.loads(path.with_suffix(".summary.json").read_text())
    return lines, summary


def aggregate(runs: list[tuple[list[dict], dict]]) -> dict:
    if not runs:
        raise ValueError("no runs to combine")
    summaries = [summary for _, summary in runs]
    configs = {json.dumps(s.get("config", {}), sort_keys=True) for s in summaries}
    if len(configs) > 1:
        raise ValueError(f"runs used different settings: {sorted(configs)}")
    ids = [line["id"] for line in runs[0][0]]
    if any([line["id"] for line in lines] != ids for lines, _ in runs):
        raise ValueError("runs didn't ask the same cases in the same order")

    n = len(runs)
    per_case = {case_id: 0 for case_id in ids}
    for lines, _ in runs:
        for line in lines:
            per_case[line["id"]] += line["pass"]

    categories = summaries[0]["by_category"]
    requirements = {}
    for name in summaries[0]["requirements"]:
        held = [s["requirements"][name]["held"] for s in summaries]
        cases = [s["requirements"][name]["cases"] for s in summaries]
        requirements[name] = {
            "held": sum(held),
            "cases": sum(cases),
            "held_in_every_run": all(h == c for h, c in zip(held, cases, strict=True)),
        }

    citation_keys = [
        "total",
        "verified_against_pdf",
        "on_gold_section",
        "facts_checked",
        "facts_supported_by_citations",
    ]
    retrieval = summaries[0]["retrieval"]

    return {
        "runs": n,
        "cases": len(ids),
        "config": summaries[0].get("config", {}),
        "passed": spread([s["passed"] for s in summaries]),
        "by_category": {
            name: {"cases": c["cases"], **spread([s["by_category"][name]["passed"] for s in summaries])}
            for name, c in categories.items()
        },
        "per_case_passed": per_case,
        "flaky": [f"{case_id} {k}/{n}" for case_id, k in per_case.items() if 0 < k < n],
        "failed_every_run": [case_id for case_id, k in per_case.items() if k == 0],
        "try_again": spread([sum(line["actual"]["status"] == "try_again" for line in lines) for lines, _ in runs]),
        "wrong_answers": spread([len(_wrong(lines)) for lines, _ in runs]),
        "wrong_answers_by_case": _count(case_id for lines, _ in runs for case_id in _wrong(lines)),
        "requirements": requirements,
        "citations": {key: sum(s["citations"][key] for s in summaries) for key in citation_keys},
        "leaks": sum(s["leaks"] for s in summaries),
        "retrieval": {**retrieval, "same_in_every_run": all(s["retrieval"] == retrieval for s in summaries)},
        "llm_calls_mean": round(statistics.mean(s["llm_calls"]["mean"] for s in summaries), 2),
        "latency_ms": {
            "p50": middle([s["latency_ms"]["p50"] for s in summaries]),
            "p95": middle([s["latency_ms"]["p95"] for s in summaries]),
        },
    }


def _wrong(lines: list[dict]) -> list[str]:
    """Failed cases that weren't a "try again": the answer itself was wrong."""
    return [line["id"] for line in lines if not line["pass"] and line["actual"]["status"] != "try_again"]


def _count(ids) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case_id in ids:
        counts[case_id] = counts.get(case_id, 0) + 1
    return counts


def print_aggregate(agg: dict, out: Path) -> None:
    p = agg["passed"]
    print(f"\n{agg['runs']} runs | llm={agg['config'].get('llm')} retriever={agg['config'].get('retriever')}")
    print(
        f"passed     {p['mean']} ± {p['std']} of {agg['cases']}  (min {p['min']}, max {p['max']}; "
        f"runs: {', '.join(map(str, p['per_run']))})"
    )
    for name, c in agg["by_category"].items():
        print(f"  {name:20} {c['mean']} ± {c['std']} of {c['cases']}  ({c['min']}-{c['max']})")
    print(f"flaky      {', '.join(agg['flaky']) or 'none'}")
    print(f"failed every run: {', '.join(agg['failed_every_run']) or 'none'}")
    print(f"try again  per run: {', '.join(map(str, agg['try_again']['per_run']))}")
    wrong = agg["wrong_answers"]
    by_case = ", ".join(f"{case_id} x{k}" for case_id, k in agg["wrong_answers_by_case"].items())
    print(f"wrong      per run: {', '.join(map(str, wrong['per_run']))}  ({by_case or 'none'})")
    print("requirements (over every case of every run)")
    for name, req in agg["requirements"].items():
        if req["cases"]:
            print(f"  {name:24} {req['held']}/{req['cases']}{'' if req['held_in_every_run'] else '  BROKEN'}")
    r = agg["retrieval"]
    print(
        f"retrieval  gold first {r['gold_first']}/{r['cases_with_gold']}  "
        f"in top 3 {r['gold_in_top_3']}/{r['cases_with_gold']}  mrr={r['mrr']}  "
        f"same in every run: {r['same_in_every_run']}"
    )
    c = agg["citations"]
    print(f"citations  {c['verified_against_pdf']}/{c['total']} match the PDF")
    lat = agg["latency_ms"]
    print(
        f"latency    p50 {lat['p50']['median']} ms ({lat['p50']['min']}-{lat['p50']['max']})  "
        f"p95 {lat['p95']['median']} ms ({lat['p95']['min']}-{lat['p95']['max']})"
    )
    print(f"leaks      {agg['leaks']}")
    print(f"summary -> {out}")
