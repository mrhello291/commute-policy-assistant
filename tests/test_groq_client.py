"""Error mapping for the Groq client, using a stubbed HTTP transport."""

import json

import httpx
import pytest

from app.llm.base import LLMBadResponse, LLMNotConfigured, LLMRateLimited, LLMTimeout, LLMUnavailable
from app.llm.groq_client import GroqLLMClient


def make_client(handler):
    client = GroqLLMClient(api_key="test-key", model="test-model")
    client._client = client._client.with_options(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return client


def completion(content, finish_reason="stop"):
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}],
    }


def call(client):
    return client.complete_json("system", "user", {"type": "object"}, timeout=1.0)


def test_returns_content_and_sends_strict_schema():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion('{"ok": true}'))

    assert call(make_client(handler)) == '{"ok": true}'
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["temperature"] == 0


@pytest.mark.parametrize(
    "status,error",
    [(429, LLMRateLimited), (401, LLMNotConfigured), (400, LLMBadResponse), (500, LLMUnavailable), (503, LLMUnavailable)],
)
def test_http_errors_map_to_llm_errors(status, error):
    client = make_client(lambda request: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(error):
        call(client)


def test_timeout_maps_to_llm_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMTimeout):
        call(make_client(handler))


def test_connection_error_maps_to_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMUnavailable):
        call(make_client(handler))


@pytest.mark.parametrize("content,finish", [("", "stop"), ("   ", "stop"), ('{"answer": "cut', "length")])
def test_empty_or_truncated_reply_is_bad_response(content, finish):
    client = make_client(lambda request: httpx.Response(200, json=completion(content, finish)))
    with pytest.raises(LLMBadResponse):
        call(client)


def test_sdk_never_retries_on_its_own():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"error": {"message": "busy"}})

    with pytest.raises(LLMUnavailable):
        call(make_client(handler))
    assert len(calls) == 1
