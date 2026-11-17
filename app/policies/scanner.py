"""Flags text that looks like instructions aimed at an AI model.

This is a tripwire, not the defence. Pattern lists can always be evaded, so
the real protection is elsewhere: the model has no tools, sees only the
caller's own company's excerpts, and must answer in a fixed JSON shape that
code validates. The scanner exists so a suspicious policy upload is logged
and visible, and so flagged excerpts get an extra warning in the prompt.

Flagged text is still indexed. A legitimate policy may mention "the system"
or "instructions", and silently dropping policy text would be worse than
answering from it carefully.
"""

import dataclasses
import re

from app.policies.models import Chunk

_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instruction|rule|prompt|guideline)s?\b",
        re.IGNORECASE,
    ),
    "addresses_ai": re.compile(
        r"\b(note|message|instructions?)\s+(to|for)\s+(any\s+|all\s+)?"
        r"(ai|assistants?|chat\s?bots?|llms?|language models?|automated)\b"
        r"|\byou are (now )?(an? )?(ai|assistant|chat\s?bot)\b",
        re.IGNORECASE,
    ),
    "system_prompt": re.compile(r"\b(system|developer)\s+(prompt|message|rules)\b", re.IGNORECASE),
    "role_markup": re.compile(r"<\|?\s*(system|assistant|im_start)\s*\|?>|\[/?INST\]", re.IGNORECASE),
    "reveal_prompt": re.compile(
        r"\b(reveal|print|show|repeat)\b[^.\n]{0,20}\b(your|the)\s+(prompt|instructions)\b",
        re.IGNORECASE,
    ),
}


class InjectionScanner:
    def scan(self, text: str) -> tuple[str, ...]:
        """Return the names of matched patterns, sorted; empty if none."""
        return tuple(sorted(name for name, pattern in _PATTERNS.items() if pattern.search(text)))

    def flag(self, chunks: list[Chunk]) -> list[Chunk]:
        return [dataclasses.replace(c, flags=self.scan(c.text)) for c in chunks]
