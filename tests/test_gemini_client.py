"""Error mapping for the Gemini client, with the SDK call stubbed out."""

import httpx
import pytest
from google.genai import errors

from app.llm.base import LLMBadResponse, LLMNotConfigured, LLMRateLimited, LLMTimeout, LLMUnavailable
from app.llm.gemini_client import GeminiLLMClient


class FakeResponse:
    def __init__(self, text, candidates=()):
        self.text = text
        self.candidates = list(candidates)


def client_returning(outcome):
    client = GeminiLLMClient(api_key="test-key", model="test-model")

    def generate_content(**kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client._client.models.generate_content = generate_content
    return client


def call(client):
    return client.complete_json("system", "user", {"type": "object"}, timeout=1.0)


def test_returns_text():
    assert call(client_returning(FakeResponse('{"ok": true}'))) == '{"ok": true}'


@pytest.mark.parametrize(
    "exc,error",
    [
        (errors.ClientError(429, {"error": {"message": "quota"}}), LLMRateLimited),
        (errors.ClientError(403, {"error": {"message": "key"}}), LLMNotConfigured),
        (errors.ClientError(400, {"error": {"message": "bad"}}), LLMBadResponse),
        (errors.ServerError(503, {"error": {"message": "busy"}}), LLMUnavailable),
        (httpx.ReadTimeout("slow"), LLMTimeout),
        (httpx.ConnectError("refused"), LLMUnavailable),
    ],
)
def test_errors_map_to_llm_errors(exc, error):
    with pytest.raises(error):
        call(client_returning(exc))


def test_empty_reply_is_bad_response():
    with pytest.raises(LLMBadResponse):
        call(client_returning(FakeResponse("")))


def test_the_server_deadline_is_never_below_the_apis_minimum():
    """Gemini answers 400 to a deadline under 10 s; the client-side timeout stays short."""
    seen = {}
    client = GeminiLLMClient(api_key="test-key", model="test-model")

    def generate_content(**kwargs):
        seen["http"] = kwargs["config"].http_options
        return FakeResponse('{"ok": true}')

    client._client.models.generate_content = generate_content
    client.complete_json("system", "user", {"type": "object"}, timeout=2.2)
    assert seen["http"].headers["X-Server-Timeout"] == "10"
    assert seen["http"].timeout == 2200
