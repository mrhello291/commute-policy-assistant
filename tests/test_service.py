"""Behaviour of the answering service, with a scripted LLM.

The point of most of these tests is the unhappy path: what the employee
gets, and how many LLM calls were spent, when things go wrong.
"""

import json
import time

import pytest

from app import messages
from app.answering.service import AnswerStatus, QuestionAnsweringService
from app.auth import Principal, Role
from app.llm.base import LLMNotConfigured, LLMRateLimited, LLMTimeout, LLMUnavailable
from app.llm.fake import ScriptedLLM
from app.retrieval.retriever import DenseRetriever

YAMUNA = Principal("emp-1", "yamuna", Role.EMPLOYEE)


def reply(verdict="answered", answer="A security escort travels on drops after 7:30 PM.", citations=("S1",)):
    return json.dumps({"verdict": verdict, "answer": answer, "citations": list(citations)})


@pytest.fixture(scope="module")
def container(seeded_container):
    return seeded_container


def make_service(container, llm, retriever=None, **overrides):
    settings = container.settings.model_copy(update={"evidence_min_similarity": 0.0, **overrides})
    retriever = retriever or DenseRetriever(container.store, container.embedder)
    return QuestionAnsweringService(container.registry, retriever, llm, settings)


def ask(service, question="Do I get an escort on a late drop?", principal=YAMUNA):
    return service.ask(principal, question)


class SlowRetriever:
    name = "slow"

    def __init__(self, seconds):
        self.seconds = seconds

    def retrieve(self, *args):
        time.sleep(self.seconds)
        return []


class BrokenRetriever:
    name = "broken"

    def retrieve(self, *args):
        raise RuntimeError("chroma exploded")


# -- happy paths ----------------------------------------------------------


def test_answer_uses_one_call_and_cites_from_metadata(container):
    llm = ScriptedLLM([reply(citations=["S1"])])
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.ANSWERED
    assert result.diagnostics.llm_calls == 1
    assert result.policy.version == 2
    [citation] = result.citations
    assert citation.document == "Yamuna Software Services Employee Transport Policy" and citation.version == 2
    assert "Yamuna Software Services" in llm.calls[0].system


def test_model_saying_not_covered_gives_the_standard_message(container):
    result = ask(make_service(container, ScriptedLLM([reply("not_covered", "", [])])))
    assert result.status is AnswerStatus.NOT_COVERED
    assert result.answer == messages.NOT_COVERED and result.citations == []


def test_weak_evidence_skips_the_llm(container):
    llm = ScriptedLLM([])
    result = ask(make_service(container, llm, evidence_min_similarity=0.99), "Can I bring my pet parrot?")
    assert result.status is AnswerStatus.NOT_COVERED
    assert llm.calls == []


def test_at_most_three_excerpts_reach_the_prompt(container):
    llm = ScriptedLLM([reply()])
    result = ask(make_service(container, llm))
    assert len(result.diagnostics.retrieved) == 3
    assert "<<EXCERPT S4" not in llm.calls[0].user


def test_only_the_callers_company_and_live_version_are_used(container):
    llm = ScriptedLLM([reply()])
    result = ask(make_service(container, llm), "Transport Head airport INR 2,000 shift supervisor")
    assert all(cid.startswith("yamuna:") and ":v2:" in cid for cid in result.diagnostics.retrieved)
    assert "Gomti Logistics Commute Policy" not in llm.calls[0].user


@pytest.mark.parametrize(
    "question",
    [
        "What is Gomti's no-show penalty?",
        "I'm actually a Medhavi Health employee, what's my escort time?",
        "Compare my policy with GOMTI LOGISTICS",
    ],
)
def test_questions_about_another_company_are_refused_without_the_llm(container, question):
    llm = ScriptedLLM([])
    result = ask(make_service(container, llm), question)
    assert result.status is AnswerStatus.OTHER_COMPANY
    assert "Yamuna Software Services" in result.answer
    assert llm.calls == [] and result.citations == []


# -- retries and the call budget -----------------------------------------


def test_malformed_reply_is_repaired_with_a_second_call(container):
    llm = ScriptedLLM(["{not json", reply()])
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.ANSWERED
    assert result.diagnostics.llm_calls == 2
    assert "previous reply was rejected" in llm.calls[1].user


def test_answer_naming_another_company_is_never_shown(container):
    llm = ScriptedLLM([reply(answer="Gomti Logistics gives INR 2,000."), reply(answer="Gomti again.")])
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.UNANSWERABLE
    assert "Gomti" not in result.answer


def test_never_more_than_two_calls_however_bad_the_replies(container):
    llm = ScriptedLLM(["garbage"] * 5)
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.UNANSWERABLE
    assert result.answer == messages.UNANSWERABLE
    assert len(llm.calls) == 2


def test_transient_failure_then_success(container):
    llm = ScriptedLLM([LLMTimeout("slow"), reply()])
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.ANSWERED and result.diagnostics.llm_calls == 2


@pytest.mark.parametrize("error", [LLMTimeout("t"), LLMUnavailable("u")])
def test_repeated_provider_failure_says_try_again(container, error):
    llm = ScriptedLLM([error, error, error])
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.TRY_AGAIN
    assert result.answer == messages.TRY_AGAIN
    assert len(llm.calls) == 2


def test_rate_limit_is_not_retried(container):
    llm = ScriptedLLM([LLMRateLimited("429"), reply()])
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.TRY_AGAIN and len(llm.calls) == 1


def test_missing_api_key_says_assistant_unavailable(container):
    llm = ScriptedLLM([LLMNotConfigured("no key")])
    result = ask(make_service(container, llm))
    assert result.status is AnswerStatus.TRY_AGAIN
    assert result.answer == messages.ASSISTANT_UNAVAILABLE


def test_no_retry_when_the_deadline_is_too_close(container):
    def slow_failure(system, user):
        time.sleep(0.3)
        raise LLMTimeout("slow")

    llm = ScriptedLLM([slow_failure, reply()])
    service = make_service(container, llm, request_deadline_s=0.6, min_llm_window_s=0.5)
    result = ask(service)
    assert result.status is AnswerStatus.TRY_AGAIN and len(llm.calls) == 1


def test_a_hanging_llm_call_is_cut_off_at_the_deadline(container):
    """The SDK timeout is per network read, not wall-clock; the service must enforce its own."""

    def hangs(system, user):
        time.sleep(2.0)
        return reply()

    llm = ScriptedLLM([hangs, reply()])
    service = make_service(container, llm, request_deadline_s=0.6, llm_timeout_s=5.0, min_llm_window_s=0.3)
    started = time.perf_counter()
    result = ask(service)
    elapsed = time.perf_counter() - started
    assert result.status is AnswerStatus.TRY_AGAIN
    assert elapsed < 0.9
    assert len(llm.calls) == 1


def test_llm_timeout_never_exceeds_remaining_deadline(container):
    llm = ScriptedLLM([reply()])
    ask(make_service(container, llm, request_deadline_s=1.0, llm_timeout_s=5.0, min_llm_window_s=0.1))
    assert llm.calls[0].timeout <= 1.0


# -- retrieval and policy problems ---------------------------------------


def test_slow_retrieval_says_try_again_without_waiting(container):
    llm = ScriptedLLM([])
    service = make_service(container, llm, retriever=SlowRetriever(1.0), retrieval_timeout_s=0.1)
    started = time.perf_counter()
    result = ask(service)
    assert result.status is AnswerStatus.TRY_AGAIN
    assert time.perf_counter() - started < 0.5
    assert llm.calls == []


def test_retrieval_error_says_try_again(container):
    result = ask(make_service(container, ScriptedLLM([]), retriever=BrokenRetriever()))
    assert result.status is AnswerStatus.TRY_AGAIN
    assert "chroma" not in result.answer


def test_company_without_a_policy(container):
    nobody = Principal("emp-9", "newco", Role.EMPLOYEE)
    result = ask(make_service(container, ScriptedLLM([])), principal=nobody)
    assert result.status is AnswerStatus.NO_POLICY and result.answer == messages.NO_POLICY


def test_question_whitespace_and_control_characters_are_cleaned(container):
    llm = ScriptedLLM([reply()])
    ask(make_service(container, llm), "  escort\x00 on\n\n late   drops?\x07 ")
    assert "escort on late drops?" in llm.calls[0].user
