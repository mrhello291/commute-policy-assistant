"""The only shape the model is allowed to answer in.

The model never writes citations itself. It picks excerpt labels (S1, S2,
S3) and code turns those into document and section references from stored
metadata, so a citation can't point at text the model was never shown.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_ANSWER_CHARS = 1000


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["answered", "not_covered"]
    answer: str = Field(max_length=MAX_ANSWER_CHARS)
    citations: list[str] = Field(max_length=3)


def answer_json_schema(labels: list[str]) -> dict:
    """JSON schema for the reply, with citations limited to the given labels.

    Written by hand rather than generated so it stays inside what strict
    structured-output modes accept: every property required, no extras.
    """
    return {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["answered", "not_covered"]},
            "answer": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "string", "enum": labels}},
        },
        "required": ["verdict", "answer", "citations"],
        "additionalProperties": False,
    }
