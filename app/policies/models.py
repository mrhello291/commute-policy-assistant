"""Value objects for policy documents.

They are frozen dataclasses: once a document is parsed nothing downstream can
quietly change which company or version a piece of text belongs to.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Section:
    """One numbered section of a policy, e.g. `4.2 Security escort`."""

    section_id: str
    title: str
    # Headings from the top of the document down to this section,
    # e.g. ("4. Night-shift travel", "4.2 Security escort").
    path: tuple[str, ...]
    body: str


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    company_id: str
    company_name: str
    doc_id: str
    title: str
    version: int
    effective_from: date
    sections: tuple[Section, ...]
    checksum: str
