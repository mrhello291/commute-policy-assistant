"""Slows down guessing at the login endpoint.

Sign-in takes only a company and an employee ID, so the thing worth guessing
is IDs. Each client gets a fixed number of failed sign-ins in a sliding
window; after that it must wait until the oldest failure falls out of it.
Successful sign-ins are not counted and don't clear earlier failures, so a
known-good ID can't be used to reset the counter between guesses.

The counts live in memory. A restart clears them, which an attacker can't
cause from outside, and several server processes would each keep their own
(a shared store such as Redis would fix both).
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from app.errors import TooManyAttempts

# Clients idle for this long are forgotten, so the table can't grow forever.
_MAX_TRACKED_CLIENTS = 10_000


class LoginRateLimiter:
    def __init__(self, max_failures: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self._max_failures = max_failures
        self._window_s = window_s
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> None:
        """Raise `TooManyAttempts` if `client` has used up its failures."""
        with self._lock:
            failures = self._recent(client)
            if len(failures) >= self._max_failures:
                retry_after = failures[0] + self._window_s - self._clock()
                raise TooManyAttempts(max(1, math.ceil(retry_after)))

    def record_failure(self, client: str) -> None:
        with self._lock:
            if client not in self._failures and len(self._failures) >= _MAX_TRACKED_CLIENTS:
                self._forget_idle()
            self._failures.setdefault(client, deque()).append(self._clock())

    def _recent(self, client: str) -> deque[float]:
        failures = self._failures.get(client, deque())
        cutoff = self._clock() - self._window_s
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(client, None)
        return failures

    def _forget_idle(self) -> None:
        for client in list(self._failures):
            self._recent(client)
        if len(self._failures) >= _MAX_TRACKED_CLIENTS:
            # Still full of active clients: drop the one that failed longest ago.
            oldest = min(self._failures, key=lambda c: self._failures[c][-1])
            del self._failures[oldest]
