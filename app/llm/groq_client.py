"""Groq chat completions with strict JSON-schema output.

Groq's free tier is fast enough to keep a full answer under the 3 second
target, and its strict structured-output mode constrains decoding to the
schema, which removes most malformed-reply cases at the source.
"""

import groq

from app.llm.base import (
    LLMBadResponse,
    LLMNotConfigured,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
)


class GroqLLMClient:
    def __init__(self, api_key: str, model: str, max_output_tokens: int = 500, reasoning_effort: str | None = "low"):
        # max_retries=0: the SDK would otherwise retry silently and spend
        # calls the per-question budget never sees.
        self._client = groq.Groq(api_key=api_key, max_retries=0)
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self.name = f"groq:{model}"

    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> str:
        extra = {"reasoning_effort": self._reasoning_effort} if self._reasoning_effort else {}
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "policy_answer", "strict": True, "schema": schema},
                },
                temperature=0,
                max_completion_tokens=self._max_output_tokens,
                timeout=timeout,
                **extra,
            )
        except groq.APITimeoutError as exc:
            raise LLMTimeout("groq request timed out") from exc
        except groq.RateLimitError as exc:
            raise LLMRateLimited("groq rate limit reached") from exc
        except (groq.AuthenticationError, groq.PermissionDeniedError) as exc:
            raise LLMNotConfigured("groq rejected the API key") from exc
        except groq.BadRequestError as exc:
            # Includes output that failed the provider's own schema check.
            raise LLMBadResponse(f"groq rejected the request: {exc.status_code}") from exc
        except (groq.APIConnectionError, groq.APIStatusError) as exc:
            raise LLMUnavailable("groq is unavailable") from exc

        if not response.choices:
            raise LLMBadResponse("groq returned no choices")
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMBadResponse("groq reply was cut off at the token limit")
        content = choice.message.content
        if not content or not content.strip():
            raise LLMBadResponse("groq returned an empty reply")
        return content
