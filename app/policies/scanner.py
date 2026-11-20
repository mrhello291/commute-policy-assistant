"""Flags text that looks like instructions aimed at an AI model.

This is a tripwire, not the defence. Pattern lists can always be evaded, so
the real protection is elsewhere: the model has no tools, sees only the
caller's own company's excerpts, and must answer in a fixed JSON shape that
code validates. The scanner exists so a suspicious policy upload is logged
and visible, and so flagged excerpts get an extra warning in the prompt.

Flagged text is still indexed. A legitimate policy may mention "the system"
or "instructions", and silently dropping policy text would be worse than
answering from it carefully.

The same patterns check the model's answers, where a false match costs the
employee their answer. So they look for instructions aimed at the model
("ignore all previous instructions", "your system prompt"), not for the
words alone: "no one can override the escort rule" is a safety rule, and
"the booking system rules" is about a booking tool.
"""

import dataclasses
import re

from app.policies.models import Chunk

_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        # "ignore all previous instructions", "disregard the above rules"
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}"
        r"\b(previous|prior|above|earlier|preceding|original)\s+([a-z]+\s+)?(instruction|rule|prompt|guideline)s?\b"
        # "ignore the instructions above", "forget the rules you were given"
        r"|\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(instruction|rule|prompt|guideline)s?\s+"
        r"(above|so far|you were given|you have been given)\b"
        # "forget your instructions", "override your system prompt"
        r"|\b(ignore|disregard|forget|override)\s+(all\s+)?your\s+((previous|prior|original|system)\s+)?"
        r"(instruction|rule|prompt|guideline)s?\b",
        re.IGNORECASE,
    ),
    "addresses_ai": re.compile(
        r"\b(note|message|instructions?)\s+(to|for)\s+(any\s+|all\s+)?"
        r"(ai|assistants?|chat\s?bots?|llms?|language models?|automated)\b"
        r"|\byou are (now )?(an? )?(ai|assistant|chat\s?bot)\b",
        re.IGNORECASE,
    ),
    "system_prompt": re.compile(r"\b(system|developer)\s+prompts?\b|\bdeveloper\s+(message|mode)\b", re.IGNORECASE),
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
