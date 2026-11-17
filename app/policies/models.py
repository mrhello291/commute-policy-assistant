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


@dataclass(frozen=True, slots=True)
class Chunk:
    """The unit that is embedded, retrieved and cited.

    Usually one chunk per section; long sections are split on paragraph
    boundaries but keep their section id, so a citation still points at
    the right place in the document.
    """

    chunk_id: str
    company_id: str
    company_name: str
    doc_id: str
    doc_title: str
    version: int
    section_id: str
    section_title: str
    section_path: tuple[str, ...]
    part: int
    text: str
    # Names of injection patterns the scanner matched, empty when clean.
    flags: tuple[str, ...] = ()

    @property
    def indexed_text(self) -> str:
        """Text used for embedding and keyword search.

        Prefixing the heading path gives short sections enough context to
        be found ("4.2 Security escort" alone says little about night drops).
        """
        heading = " > ".join((self.doc_title, *self.section_path))
        return f"{heading}\n\n{self.text}"
