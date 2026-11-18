"""Answers one employee question from their company's live policy.

    question
      -> live policy version for the caller's company (from the registry)
      -> names another client company? polite refusal, no LLM call
      -> retrieve up to 3 excerpts of that version          [time-boxed]
      -> evidence gate: nothing relevant? "not covered", no LLM call
      -> LLM call with fenced excerpts                      [call 1]
      -> validate the reply in code
      -> on a transient failure or a rejected reply, one retry [call 2]
      -> answer + citations built from metadata

Every outcome, including failures, comes back as an `AnswerResult` with a
plain message. Nothing in here raises to the caller for expected problems.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import StrEnum

from app import messages
from app.answering.citations import Citation, render_citations
from app.answering.prompt import Prompt, PromptBuilder
from app.answering.schema import answer_json_schema
from app.answering.validator import AnswerRejected, AnswerValidator, ValidatedAnswer
from app.auth import Principal
from app.config import Settings
from app.errors import TenantIsolationError
from app.llm.base import LLMClient, LLMError, LLMNotConfigured, LLMRateLimited, LLMTimeout
from app.llm.budget import CallBudget, Deadline
from app.policies.registry import PolicyRegistry, PolicyVersion
from app.retrieval.models import ScoredChunk
from app.retrieval.retriever import Retriever

log = logging.getLogger(__name__)


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    NOT_COVERED = "not_covered"
    NO_POLICY = "no_policy"
    OTHER_COMPANY = "other_company"
    UNANSWERABLE = "unanswerable"
    TRY_AGAIN = "try_again"


@dataclass
class Diagnostics:
    """What happened while answering. Logged, used by the evaluation, and
    returned to the client only as call counts and timings."""

    llm_calls: int = 0
    llm: str = ""
    retriever: str = ""
    retrieved: list[str] = field(default_factory=list)  # chunk ids, best first
    retrieved_sections: list[str] = field(default_factory=list)
    top_similarity: float | None = None
    retrieval_ms: float = 0.0
    total_ms: float = 0.0
    failure: str = ""


@dataclass
class AnswerResult:
    status: AnswerStatus
    answer: str
    citations: list[Citation] = field(default_factory=list)
    policy: PolicyVersion | None = None
    diagnostics: Diagnostics = field(default_factory=Diagnostics)


class QuestionAnsweringService:
    def __init__(
        self,
        registry: PolicyRegistry,
        retriever: Retriever,
        llm: LLMClient,
        settings: Settings,
        prompt_builder: PromptBuilder | None = None,
        validator: AnswerValidator | None = None,
    ):
        self._registry = registry
        self._retriever = retriever
        self._llm = llm
        self._settings = settings
        self._prompts = prompt_builder or PromptBuilder()
        self._validator = validator or AnswerValidator()
        self._retrieval_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="retrieval")
        self._llm_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm")

    def ask(self, principal: Principal, question: str) -> AnswerResult:
        started = time.perf_counter()
        diagnostics = Diagnostics(llm=self._llm.name, retriever=self._retriever.name)
        result = self._answer(principal.company_id, _normalise(question), diagnostics)
        diagnostics.total_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "question company=%s status=%s llm_calls=%d total_ms=%.0f retrieval_ms=%.0f top_sim=%s failure=%s",
            principal.company_id,
            result.status,
            diagnostics.llm_calls,
            diagnostics.total_ms,
            diagnostics.retrieval_ms,
            diagnostics.top_similarity,
            diagnostics.failure or "-",
        )
        return result

    def _answer(self, company_id: str, question: str, diagnostics: Diagnostics) -> AnswerResult:
        deadline = Deadline(self._settings.request_deadline_s)
        budget = CallBudget(self._settings.max_llm_calls)

        # Read the live version once, so the whole answer comes from one
        # consistent version even if an upload lands mid-request.
        policy = self._registry.active(company_id)
        if policy is None:
            return AnswerResult(AnswerStatus.NO_POLICY, messages.NO_POLICY, diagnostics=diagnostics)

        # A question about another client's policy is refused in code. Letting
        # the model handle it risks an answer like "Gomti's rule is ..."
        # built from the caller's own policy, which is wrong and confusing.
        others = self._other_company_names(company_id)
        if _mentions_any(question, others):
            message = messages.OTHER_COMPANY.format(company=policy.company_name)
            return AnswerResult(AnswerStatus.OTHER_COMPANY, message, policy=policy, diagnostics=diagnostics)

        try:
            excerpts = self._retrieve(policy, question, diagnostics)
        except FutureTimeout:
            diagnostics.failure = "retrieval_timeout"
            return self._try_again(policy, diagnostics)
        except TenantIsolationError:
            diagnostics.failure = "tenant_check"
            return self._try_again(policy, diagnostics)
        except Exception:
            log.exception("retrieval failed for %s", company_id)
            diagnostics.failure = "retrieval_error"
            return self._try_again(policy, diagnostics)

        if not excerpts or max(e.similarity for e in excerpts) < self._settings.evidence_min_similarity:
            diagnostics.failure = "below_evidence_threshold"
            return AnswerResult(AnswerStatus.NOT_COVERED, messages.NOT_COVERED, policy=policy, diagnostics=diagnostics)

        prompt = self._prompts.build(policy.company_name, question, excerpts)
        return self._generate(prompt, policy, others, budget, deadline, diagnostics)

    def _retrieve(self, policy: PolicyVersion, question: str, diagnostics: Diagnostics) -> list[ScoredChunk]:
        started = time.perf_counter()
        future = self._retrieval_pool.submit(
            self._retriever.retrieve, policy.company_id, policy.version, question, self._settings.max_cited_chunks
        )
        try:
            excerpts = future.result(timeout=self._settings.retrieval_timeout_s)
        finally:
            diagnostics.retrieval_ms = round((time.perf_counter() - started) * 1000, 1)
        # Hard cap, whatever the retriever returned: at most N excerpts can be cited.
        excerpts = excerpts[: self._settings.max_cited_chunks]
        diagnostics.retrieved = [e.chunk.chunk_id for e in excerpts]
        diagnostics.retrieved_sections = [e.chunk.section_id for e in excerpts]
        diagnostics.top_similarity = round(max(e.similarity for e in excerpts), 3) if excerpts else None
        return excerpts

    def _generate(
        self,
        prompt: Prompt,
        policy: PolicyVersion,
        other_companies: list[str],
        budget: CallBudget,
        deadline: Deadline,
        diagnostics: Diagnostics,
    ) -> AnswerResult:
        schema = answer_json_schema(list(prompt.labels))
        user_message = prompt.user
        last_problem: Exception | None = None

        while budget.remaining > 0 and deadline.remaining() >= self._settings.min_llm_window_s:
            budget.spend()
            diagnostics.llm_calls = budget.used
            timeout = min(self._settings.llm_timeout_s, deadline.remaining())
            try:
                raw = self._call_llm(prompt.system, user_message, schema, timeout)
                validated = self._validator.validate(raw, set(prompt.labels), prompt.token, other_companies)
                return self._success(validated, prompt, policy, diagnostics)
            except LLMNotConfigured:
                diagnostics.failure = "llm_not_configured"
                return AnswerResult(
                    AnswerStatus.TRY_AGAIN, messages.ASSISTANT_UNAVAILABLE, policy=policy, diagnostics=diagnostics
                )
            except LLMRateLimited as exc:
                # Retrying straight into a rate limit only wastes the budget.
                last_problem = exc
                break
            except LLMError as exc:
                last_problem = exc
                if not exc.transient:
                    break
                log.warning("LLM call %d failed: %s", budget.used, exc)
            except AnswerRejected as exc:
                last_problem = exc
                log.warning("LLM reply %d rejected: %s", budget.used, exc)
                user_message = (
                    f"{prompt.user}\n\nYour previous reply was rejected because {exc}. "
                    "Reply again, following the rules exactly."
                )

        diagnostics.failure = type(last_problem).__name__ if last_problem else "deadline"
        if isinstance(last_problem, AnswerRejected):
            return AnswerResult(
                AnswerStatus.UNANSWERABLE, messages.UNANSWERABLE, policy=policy, diagnostics=diagnostics
            )
        return self._try_again(policy, diagnostics)

    def _call_llm(self, system: str, user: str, schema: dict, timeout: float) -> str:
        """Call the model, but never wait longer than `timeout` seconds of wall-clock time.

        The SDKs' own timeouts are per network operation (connect, gap between
        reads), so a slow reply can run well past them; one benchmark request
        took 3.2 s against a 2.2 s setting. Waiting on a future enforces a real
        limit. A call that overruns is abandoned: its thread finishes in the
        background and the reply is discarded.
        """
        future = self._llm_pool.submit(self._llm.complete_json, system, user, schema, timeout)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout as exc:
            future.cancel()
            raise LLMTimeout(f"no reply within {timeout:.1f}s") from exc

    def _success(
        self, validated: ValidatedAnswer, prompt: Prompt, policy: PolicyVersion, diagnostics: Diagnostics
    ) -> AnswerResult:
        if validated.verdict == "not_covered":
            return AnswerResult(AnswerStatus.NOT_COVERED, messages.NOT_COVERED, policy=policy, diagnostics=diagnostics)
        return AnswerResult(
            AnswerStatus.ANSWERED,
            validated.answer,
            citations=render_citations(validated.citations, prompt.labels),
            policy=policy,
            diagnostics=diagnostics,
        )

    def _other_company_names(self, company_id: str) -> list[str]:
        names = []
        for other_id, other_name in self._registry.active_companies().items():
            if other_id != company_id:
                names.extend([other_name, other_id])
        return names

    @staticmethod
    def _try_again(policy: PolicyVersion | None, diagnostics: Diagnostics) -> AnswerResult:
        return AnswerResult(AnswerStatus.TRY_AGAIN, messages.TRY_AGAIN, policy=policy, diagnostics=diagnostics)


def _mentions_any(text: str, names: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE) for name in names)


def _normalise(question: str) -> str:
    """Collapse whitespace and drop control characters."""
    cleaned = "".join(ch for ch in question if ch.isprintable() or ch.isspace())
    return " ".join(cleaned.split())
