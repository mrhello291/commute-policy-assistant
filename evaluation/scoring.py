"""Deterministic scoring for evaluation cases.

No LLM judge: every check is a plain comparison or regex, so the same answer
always gets the same score and a run costs nothing extra.

A case passes when every check that applies to it passes:

- status     the outcome (answered / not_covered / other_company) is the expected one
- facts      every `must_include` pattern appears in the answer
- no_leak    no `must_not_include` pattern appears (other companies' values,
             retired-version values, injected text)
- citation   an answered case cites at least one gold section, and every
             citation comes from the caller's own company's document
- citation_valid  every citation matches the policy PDF: section, title,
             excerpt and page numbers (see citation_check.py; checked when the
             runner has the source PDFs)
- isolation  every retrieved chunk belongs to the caller's company
- live_version  every retrieved chunk and every citation is from the
             company's newest policy version (the highest version among the
             policy files loaded), and that is the version the service
             reports as live
- budget     at most 2 LLM calls and at most 3 citations

Retrieval quality is reported separately; it explains failures but is not
itself a pass condition. Against each case's gold sections: hit@1 and hit@3
(was a gold section ranked first / in the top 3), MRR, recall@3 (share of
the gold sections in the top 3) and precision@3 (share of the top 3 that are
gold). It also reports what the evidence gate did: answerable questions it
wrongly stopped, and uncovered ones it stopped before any LLM call.
"""

import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

MAX_LLM_CALLS = 2
MAX_CITATIONS = 3


@dataclass
class CaseScore:
    passed: bool
    checks: dict[str, bool]
    notes: list[str] = field(default_factory=list)
    first_gold_rank: int | None = None  # 1-based rank of the first gold section retrieved


def _matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def first_gold_rank(gold: list[str], retrieved_sections: list[str]) -> int | None:
    for rank, section in enumerate(retrieved_sections, start=1):
        if section in gold:
            return rank
    return None


def score_case(
    case: dict,
    actual: dict,
    llm_available: bool = True,
    citations=None,
    live_versions: dict[str, int] | None = None,
) -> CaseScore:
    """Score one case.

    `citations` is a CitationReport from citation_check, when the source PDFs
    are at hand. `live_versions` maps each company to its newest loaded
    version; with it, the live_version check runs.
    """
    expected = case["expected"]
    company = case["company"]
    answer = actual.get("answer", "")
    checks: dict[str, bool] = {}
    notes: list[str] = []

    gold = expected["sections"]
    rank = first_gold_rank(gold, actual.get("retrieved_sections", [])) if gold else None

    checks["isolation"] = all(cid.startswith(f"{company}:") for cid in actual.get("retrieved_chunk_ids", []))
    if live_versions and company in live_versions:
        stale = _stale_versions(actual, live_versions[company])
        checks["live_version"] = not stale
        notes.extend(stale)

    checks["budget"] = actual.get("llm_calls", 0) <= MAX_LLM_CALLS and len(actual.get("citations", [])) <= MAX_CITATIONS
    checks["no_leak"] = True
    for pattern in expected["must_not_include"]:
        if _matches(pattern, answer):
            checks["no_leak"] = False
            notes.append(f"forbidden text matched /{pattern}/")

    needs_llm = expected["status"] in ("answered", "not_covered")
    if not llm_available and needs_llm and actual.get("status") == "try_again":
        # Without a model only retrieval can be judged. Say so instead of
        # counting the case as a pass or a fail it never had a chance at.
        if gold:
            checks["retrieval"] = rank is not None
            if rank is None:
                notes.append("no gold section in the top 3")
        notes.append("answer not scored: no LLM configured")
        return CaseScore(all(checks.values()), checks, notes, rank)

    checks["status"] = actual.get("status") == expected["status"]
    if not checks["status"]:
        notes.append(f"status {actual.get('status')} != expected {expected['status']}")

    if expected["status"] == "answered" and checks["status"]:
        missing = [p for p in expected["must_include"] if not _matches(p, answer)]
        checks["facts"] = not missing
        notes.extend(f"missing fact /{p}/" for p in missing)

        cited = [c["section"] for c in actual.get("citations", [])]
        own_docs = all(c.get("company_id", company) == company for c in actual.get("citations", []))
        checks["citation"] = own_docs and any(s in gold for s in cited)
        if not checks["citation"]:
            notes.append(f"cited {cited or 'nothing'}, gold is {gold}")

    if citations is not None and actual.get("citations"):
        checks["citation_valid"] = not citations.problems
        notes.extend(f"citation wrong: {p}" for p in citations.problems)
        notes.extend(f"fact /{p}/ is not in any cited section" for p in citations.unsupported)

    if gold and rank is None and actual.get("retrieved_sections"):
        notes.append("no gold section in the top 3")
    return CaseScore(all(checks.values()), checks, notes, rank)


def _chunk_version(chunk_id: str) -> int | None:
    """Chunk ids are `company:doc:v<N>:section:part`."""
    parts = chunk_id.split(":")
    if len(parts) > 2 and parts[2].startswith("v") and parts[2][1:].isdigit():
        return int(parts[2][1:])
    return None


def _stale_versions(actual: dict, live: int) -> list[str]:
    problems = []
    reported = actual.get("policy_version")
    if reported is not None and reported != live:
        problems.append(f"service answered from v{reported}, newest loaded is v{live}")
    old_chunks = sorted({v for cid in actual.get("retrieved_chunk_ids", []) if (v := _chunk_version(cid)) != live})
    if old_chunks:
        problems.append(f"retrieved chunks from {['v' + str(v) for v in old_chunks]}, newest is v{live}")
    old_cites = sorted({c["version"] for c in actual.get("citations", []) if c.get("version", live) != live})
    if old_cites:
        problems.append(f"cited {['v' + str(v) for v in old_cites]}, newest is v{live}")
    return problems


def _retrieval_summary(rows: list[dict]) -> dict:
    """How well retrieval found the gold sections, over the cases that have any."""
    with_gold = [row for row in rows if row["case"]["expected"]["sections"]]
    n = len(with_gold)
    ranks = [row["score"].first_gold_rank for row in with_gold]
    recalls, precisions, ceilings = [], [], []
    for row in with_gold:
        gold = set(row["case"]["expected"]["sections"])
        retrieved = row["actual"].get("retrieved_sections", [])
        recalls.append(len(gold & set(retrieved)) / len(gold))
        precisions.append(sum(s in gold for s in retrieved) / len(retrieved) if retrieved else 0.0)
        # With one gold section and three retrieved, a perfect retriever still scores 1/3.
        ceilings.append(min(len(gold), len(retrieved)) / len(retrieved) if retrieved else 0.0)

    def rate(values) -> float | None:
        return round(sum(values) / n, 3) if n else None

    gated = [row for row in rows if row["actual"].get("failure") == "below_evidence_threshold"]
    uncovered = [row for row in rows if row["case"]["expected"]["status"] == "not_covered"]
    return {
        "cases_with_gold": n,
        "gold_first": sum(1 for r in ranks if r == 1),
        "gold_in_top_3": sum(1 for r in ranks if r is not None),
        "hit_at_1": rate([r == 1 for r in ranks]),
        "hit_at_3": rate([r is not None for r in ranks]),
        "mrr": rate([1 / r if r else 0 for r in ranks]),
        "recall_at_3": rate(recalls),
        "precision_at_3": rate(precisions),
        "precision_at_3_best_possible": rate(ceilings),
        "missed": [row["case"]["id"] for row, r in zip(with_gold, ranks, strict=True) if r is None],
        "evidence_gate": {
            "answerable_stopped": [
                row["case"]["id"] for row in gated if row["case"]["expected"]["status"] == "answered"
            ],
            "not_covered_stopped": sum(
                1 for row in uncovered if row["actual"].get("failure") == "below_evidence_threshold"
            ),
            "not_covered_cases": len(uncovered),
        },
    }


def _requirements(rows: list[dict]) -> dict:
    """The properties every run must keep, counted case by case."""

    def held(check: str) -> dict:
        judged = [row for row in rows if check in row["score"].checks]
        return {"held": sum(row["score"].checks[check] for row in judged), "cases": len(judged)}

    answered = [row for row in rows if row["actual"].get("status") == "answered"]
    refused = [row for row in rows if row["case"]["expected"]["status"] == "other_company"]
    return {
        "own_company_only": held("isolation"),
        "other_company_refused": {
            "held": sum(row["actual"].get("status") == "other_company" for row in refused),
            "cases": len(refused),
        },
        "answers_with_citation": {
            "held": sum(bool(row["actual"].get("citations")) for row in answered),
            "cases": len(answered),
        },
        "citations_match_pdf": held("citation_valid"),
        "newest_version_only": held("live_version"),
        "no_forbidden_text": held("no_leak"),
    }


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile: the smallest value with at least pct% of values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[index]


def summarise(rows: list[dict]) -> dict:
    """Aggregate metrics over scored rows (each has case, actual and score)."""
    by_category: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        by_category[row["case"]["category"]].append(row["score"].passed)

    calls = [row["actual"]["llm_calls"] for row in rows]
    latencies = [row["actual"]["latency_ms"] for row in rows]
    leaks = sum(
        1 for row in rows if not (row["score"].checks.get("no_leak", True) and row["score"].checks["isolation"])
    )

    reports = [row["citations"] for row in rows if row.get("citations") is not None]
    total = sum(r.total for r in reports)
    facts = sum(r.facts_checked for r in reports)
    citation_summary = {
        "total": total,
        "verified_against_pdf": sum(r.verified for r in reports),
        "on_gold_section": sum(r.on_gold for r in reports),
        "precision": round(sum(r.on_gold for r in reports) / total, 3) if total else None,
        "facts_checked": facts,
        "facts_supported_by_citations": sum(r.facts_supported for r in reports),
    }

    return {
        "cases": len(rows),
        "passed": sum(row["score"].passed for row in rows),
        "pass_rate": round(sum(row["score"].passed for row in rows) / len(rows), 3) if rows else 0.0,
        "by_category": {
            name: {"passed": sum(results), "cases": len(results)} for name, results in sorted(by_category.items())
        },
        "requirements": _requirements(rows),
        "retrieval": _retrieval_summary(rows),
        "citations": citation_summary,
        "leaks": leaks,
        "llm_calls": {"max": max(calls, default=0), "mean": round(statistics.mean(calls), 2) if calls else 0},
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": max(latencies, default=None),
        },
    }
