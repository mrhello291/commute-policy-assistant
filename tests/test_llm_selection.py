import pytest
from pydantic import SecretStr

from app.config import LLMProvider
from app.container import build_llm
from app.llm.base import LLMNotConfigured
from app.llm.fake import ScriptedLLM


def test_no_keys_means_unconfigured_llm(settings):
    llm = build_llm(settings.model_copy(update={"groq_api_key": None, "gemini_api_key": None}))
    with pytest.raises(LLMNotConfigured):
        llm.complete_json("s", "u", {}, 1.0)


def test_auto_prefers_groq_then_gemini(settings):
    settings = settings.model_copy(update={"llm_provider": LLMProvider.AUTO})
    both = settings.model_copy(update={"groq_api_key": SecretStr("g-key"), "gemini_api_key": SecretStr("m-key")})
    assert build_llm(both).name.startswith("groq:")
    only_gemini = settings.model_copy(update={"groq_api_key": None, "gemini_api_key": SecretStr("m-key")})
    assert build_llm(only_gemini).name.startswith("gemini:")


def test_explicit_provider_without_key_falls_back_to_unconfigured(settings):
    chosen = settings.model_copy(update={"llm_provider": LLMProvider.GEMINI, "gemini_api_key": None})
    assert build_llm(chosen).name == "none"


def test_scripted_llm_replays_and_records():
    llm = ScriptedLLM(['{"a": 1}', TimeoutError("x")])
    assert llm.complete_json("sys", "q", {}, 1.0) == '{"a": 1}'
    with pytest.raises(TimeoutError):
        llm.complete_json("sys", "q", {}, 1.0)
    with pytest.raises(AssertionError):
        llm.complete_json("sys", "q", {}, 1.0)
    assert len(llm.calls) == 3 and llm.calls[0].user == "q"
