"""The /v1/ask endpoint end to end, with a scripted LLM."""

import json

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.llm.base import LLMTimeout
from app.llm.fake import ScriptedLLM
from app.main import create_app
from tests.helpers import auth

GOOD = json.dumps({"verdict": "answered", "answer": "Yes, after 7:30 PM.", "citations": ["S1"]})


@pytest.fixture
def make_client(settings):
    clients = []

    def factory(script):
        settings_ = settings.model_copy(update={"evidence_min_similarity": 0.0})
        llm = ScriptedLLM(script)
        client = TestClient(create_app(settings_, Container(settings_, llm=llm)))
        client.__enter__()
        clients.append(client)
        return client, llm

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def test_answer_with_citation(make_client):
    client, llm = make_client([GOOD])
    response = client.post("/v1/ask", json={"question": "Do I get an escort?"}, headers=auth(client))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["answer"] == "Yes, after 7:30 PM."
    assert body["citations"][0]["document"] == "Yamuna Software Services Employee Transport Policy"
    assert body["policy"] == {"document": "Yamuna Software Services Employee Transport Policy", "version": 2}
    assert body["meta"]["llm_calls"] == 1
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_company_comes_from_the_token_only(make_client):
    client, llm = make_client([GOOD])
    client.post("/v1/ask", json={"question": "Do I get an escort?"}, headers=auth(client, "medhavi"))
    assert "Medhavi Health" in llm.calls[0].system
    assert "Yamuna" not in llm.calls[0].user


@pytest.mark.parametrize(
    "extra",
    [{"company_id": "gomti"}, {"company": "gomti"}, {"version": 1}, {"filter": {"company_id": "gomti"}}],
)
def test_body_cannot_override_company_or_version(make_client, extra):
    client, llm = make_client([GOOD])
    response = client.post("/v1/ask", json={"question": "Escort?", **extra}, headers=auth(client))
    assert response.status_code == 422
    assert llm.calls == []


def test_query_string_is_ignored(make_client):
    client, llm = make_client([GOOD])
    client.post("/v1/ask?company_id=gomti", json={"question": "Do I get an escort?"}, headers=auth(client))
    assert "Yamuna Software Services" in llm.calls[0].system


@pytest.mark.parametrize("question", ["", "  ", "hi", "x" * 501])
def test_question_length_is_checked(make_client, question):
    client, llm = make_client([GOOD])
    response = client.post("/v1/ask", json={"question": question}, headers=auth(client))
    assert response.status_code == 422 and llm.calls == []


def test_requires_a_token(make_client):
    client, _ = make_client([GOOD])
    assert client.post("/v1/ask", json={"question": "Escort?"}).status_code == 401


def test_provider_outage_is_a_503_with_a_plain_message(make_client):
    client, _ = make_client([LLMTimeout("t"), LLMTimeout("t")])
    response = client.post("/v1/ask", json={"question": "Do I get an escort?"}, headers=auth(client))
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    body = response.json()
    assert body["status"] == "try_again" and "try again" in body["answer"].lower()
    assert "LLMTimeout" not in response.text
