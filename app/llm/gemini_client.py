"""Google Gemini (AI Studio free tier) as an alternative provider.

Useful when the Groq quota runs out. Same interface, same no-retry rule.
"""

import httpx
from google import genai
from google.genai import errors, types

from app.llm.base import (
    LLMBadResponse,
    LLMNotConfigured,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
)


class GeminiLLMClient:
    def __init__(self, api_key: str, model: str, max_output_tokens: int = 500):
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )
        self._model = model
        self._max_output_tokens = max_output_tokens
        self.name = f"gemini:{model}"

    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
            max_output_tokens=self._max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),
        )
        try:
            response = self._client.models.generate_content(model=self._model, contents=user, config=config)
        except httpx.TimeoutException as exc:
            raise LLMTimeout("gemini request timed out") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable("could not reach gemini") from exc
        except errors.ClientError as exc:
            if exc.code == 429:
                raise LLMRateLimited("gemini quota reached") from exc
            if exc.code in (401, 403):
                raise LLMNotConfigured("gemini rejected the API key") from exc
            raise LLMBadResponse(f"gemini rejected the request: {exc.code}") from exc
        except errors.APIError as exc:
            raise LLMUnavailable(f"gemini is unavailable: {exc.code}") from exc

        candidates = response.candidates or []
        if candidates and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS:
            raise LLMBadResponse("gemini reply was cut off at the token limit")
        text = response.text
        if not text or not text.strip():
            raise LLMBadResponse("gemini returned an empty reply")
        return text
