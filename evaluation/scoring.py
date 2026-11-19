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
- budget     at most 2 LLM calls and at most 3 citations

Retrieval quality (hit@1, hit@3, MRR against the gold sections) is reported
separately; it explains failures but is not itself a pass condition.
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


def score_case(case: dict, actual: dict, llm_available: bool = True, citations=None) -> CaseScore:
    """`citations` is a CitationReport from citation_check, when the source PDFs are at hand."""
    expected = case["expected"]
    company = case["company"]
    answer = actual.get("answer", "")
    checks: dict[str, bool] = {}
    notes: list[str] = []

    gold = expected["sections"]
    rank = first_gold_rank(gold, actual.get("retrieved_sections", [])) if gold else None

    checks["isolation"] = all(cid.startswith(f"{company}:") for cid in actual.get("retrieved_chunk_ids", []))
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

    ranked = [row["score"].first_gold_rank for row in rows if row["case"]["expected"]["sections"]]
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
        "retrieval": {
            "cases_with_gold": len(ranked),
            "hit_at_1": round(sum(1 for r in ranked if r == 1) / len(ranked), 3) if ranked else None,
            "hit_at_3": round(sum(1 for r in ranked if r is not None) / len(ranked), 3) if ranked else None,
            "mrr": round(sum(1 / r for r in ranked if r) / len(ranked), 3) if ranked else None,
        },
        "citations": citation_summary,
        "leaks": leaks,
        "llm_calls": {"max": max(calls, default=0), "mean": round(statistics.mean(calls), 2) if calls else 0},
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": max(latencies, default=None),
        },
    }
