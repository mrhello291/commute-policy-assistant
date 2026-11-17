"""A scripted LLM for tests.

Each call returns (or raises) the next item from the script, and every
prompt is recorded so tests can check what the model was shown and how
many calls were made.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

Step = str | Exception | Callable[[str, str], str]


@dataclass
class RecordedCall:
    system: str
    user: str
    schema: dict
    timeout: float


@dataclass
class ScriptedLLM:
    script: list[Step]
    name: str = "scripted"
    calls: list[RecordedCall] = field(default_factory=list)

    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> str:
        self.calls.append(RecordedCall(system, user, schema, timeout))
        if len(self.calls) > len(self.script):
            raise AssertionError(f"LLM called {len(self.calls)} times but only {len(self.script)} replies scripted")
        step = self.script[len(self.calls) - 1]
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(system, user)
        return step
