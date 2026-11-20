"""Checks a model reply before anyone sees it.

The reply is treated as untrusted input. It must parse, match the schema,
cite only excerpts it was given, and must not show signs of having been
steered: naming another client company, echoing our fence markers, or
repeating the planted instruction patterns the scanner knows about.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from app.answering.companies import CompanyName, names_any
from app.answering.schema import AnswerDraft
from app.errors import AppError
from app.policies.scanner import InjectionScanner


class AnswerRejected(AppError):
    """The model's reply can't be shown to the employee. The message says why."""


@dataclass(frozen=True)
class ValidatedAnswer:
    verdict: str
    answer: str
    citations: tuple[str, ...]


class AnswerValidator:
    def __init__(self, scanner: InjectionScanner | None = None):
        self._scanner = scanner or InjectionScanner()

    def validate(
        self,
        raw: str,
        allowed_labels: set[str],
        fence_token: str,
        other_companies: Sequence[CompanyName],
    ) -> ValidatedAnswer:
        try:
            draft = AnswerDraft.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise AnswerRejected("reply was not valid JSON in the required shape") from exc

        if draft.verdict == "not_covered":
            return ValidatedAnswer("not_covered", "", ())

        answer = draft.answer.strip()
        if not answer:
            raise AnswerRejected("verdict was 'answered' but the answer is empty")

        citations = tuple(dict.fromkeys(draft.citations))  # de-duplicate, keep order
        if not citations:
            raise AnswerRejected("an answer must cite at least one excerpt label")
        unknown = [c for c in citations if c not in allowed_labels]
        if unknown:
            raise AnswerRejected(f"cited labels that were not provided: {', '.join(unknown)}")

        if fence_token in answer or "<<" in answer:
            raise AnswerRejected("answer repeats the excerpt markers")
        if names_any(answer, other_companies):
            raise AnswerRejected("answer mentions another company")
        if self._scanner.scan(answer):
            raise AnswerRejected("answer contains instruction-like text")

        return ValidatedAnswer("answered", answer, citations)
