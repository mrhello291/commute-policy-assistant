"""The contract every LLM client follows.

The rest of the application only ever sees `LLMClient` and the error types
below, so switching provider is a configuration change, and tests can use a
scripted fake without any network access.

Clients make exactly one HTTP request per call and never retry on their own.
Retrying is a decision for the caller, which has to stay inside the per
question call budget and deadline.
"""

from typing import Protocol

from app.errors import AppError


class LLMError(AppError):
    """Base class for anything that went wrong talking to the model."""

    #: Whether trying again shortly has a reasonable chance of working.
    transient: bool = False


class LLMTimeout(LLMError):
    transient = True


class LLMRateLimited(LLMError):
    transient = True


class LLMUnavailable(LLMError):
    """Network failure or a server-side error at the provider."""

    transient = True


class LLMBadResponse(LLMError):
    """The provider answered but the output is unusable (empty, cut off, rejected)."""

    transient = True


class LLMNotConfigured(LLMError):
    """No API key, or the key was refused. Retrying won't help."""


class LLMClient(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> str:
        """Return the model's reply as a JSON string that should match `schema`.

        The reply is still untrusted: callers must parse and validate it.
        Raises an `LLMError` subclass on any failure.
        """
        ...


class UnconfiguredLLM:
    """Stands in when no provider key is set, so the service still starts."""

    name = "none"

    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> str:
        raise LLMNotConfigured("no LLM provider is configured")
