"""Per-question limits: how many LLM calls, and how much wall-clock time.

Both are created fresh for every question and passed down explicitly, so
there is one obvious place that enforces "at most N calls" and "answer
within T seconds", and tests can check them directly.
"""

import time
from collections.abc import Callable

from app.errors import AppError


class BudgetExhausted(AppError):
    """Another LLM call would exceed the per-question limit."""


class CallBudget:
    def __init__(self, max_calls: int):
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        self.max_calls = max_calls
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.max_calls - self.used

    def spend(self) -> None:
        """Record one LLM call. Must be called *before* making it."""
        if self.used >= self.max_calls:
            raise BudgetExhausted(f"already made {self.used} of {self.max_calls} LLM calls")
        self.used += 1


class Deadline:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._ends_at = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._ends_at - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0
