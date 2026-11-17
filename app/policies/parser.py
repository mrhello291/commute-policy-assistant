"""Parses Markdown policy files into numbered sections.

Expected format: a front-matter block between `---` lines, an optional `#`
title, then `##` / `###` headings that start with a section number
(`## 4. Night-shift travel`, `### 4.2 Security escort`). Section numbers are
what answers cite, so the parser is strict about them: a document that can't
be cited reliably is rejected before anything is stored.
"""

import hashlib
import re
from datetime import date

import yaml

from app.errors import PolicyFormatError
from app.policies.models import PolicyDocument, Section

MAX_DOCUMENT_BYTES = 200_000

_COMPANY_ID = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,38}[a-z0-9])$")
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")
_REQUIRED_FIELDS = ("company_id", "company_name", "doc_id", "title", "version", "effective_from")


class MarkdownPolicyParser:
    def parse(self, raw: str) -> PolicyDocument:
        if not raw or not raw.strip():
            raise PolicyFormatError("document is empty")
        if len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise PolicyFormatError(f"document is larger than {MAX_DOCUMENT_BYTES} bytes")

        raw = raw.replace("\r\n", "\n")
        meta, body = self._split_front_matter(raw)
        sections = self._parse_sections(body)

        return PolicyDocument(
            company_id=meta["company_id"],
            company_name=meta["company_name"],
            doc_id=meta["doc_id"],
            title=meta["title"],
            version=meta["version"],
            effective_from=meta["effective_from"],
            sections=tuple(sections),
            checksum=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )

    # -- front matter -----------------------------------------------------

    def _split_front_matter(self, raw: str) -> tuple[dict, str]:
        match = _FRONT_MATTER.match(raw)
        if not match:
            raise PolicyFormatError("missing front matter block")
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise PolicyFormatError("front matter is not valid YAML") from exc
        if not isinstance(meta, dict):
            raise PolicyFormatError("front matter must be a set of key: value lines")

        missing = [f for f in _REQUIRED_FIELDS if meta.get(f) in (None, "")]
        if missing:
            raise PolicyFormatError(f"front matter is missing: {', '.join(missing)}")

        company_id = str(meta["company_id"])
        if not _COMPANY_ID.match(company_id):
            raise PolicyFormatError("company_id must be 2-40 lowercase letters, digits, '-' or '_'")
        version = meta["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise PolicyFormatError("version must be a positive whole number")
        effective_from = meta["effective_from"]
        if not isinstance(effective_from, date):
            raise PolicyFormatError("effective_from must be a date like 2026-01-31")

        clean = {
            "company_id": company_id,
            "company_name": str(meta["company_name"]).strip(),
            "doc_id": str(meta["doc_id"]).strip(),
            "title": str(meta["title"]).strip(),
            "version": version,
            "effective_from": effective_from,
        }
        return clean, raw[match.end() :]

    # -- sections ---------------------------------------------------------

    def _parse_sections(self, body: str) -> list[Section]:
        sections: list[Section] = []
        seen_ids: set[str] = set()
        # Open headings by level, used to build each section's path.
        open_headings: dict[int, tuple[str, str]] = {}
        current: tuple[str, str, tuple[str, ...]] | None = None
        buffer: list[str] = []

        def flush() -> None:
            text = "\n".join(buffer).strip()
            buffer.clear()
            if current is not None and text:
                section_id, title, path = current
                sections.append(Section(section_id, title, path, text))

        for line in body.split("\n"):
            heading = _HEADING.match(line)
            if heading is None:
                if current is None and line.strip():
                    raise PolicyFormatError("text found before the first numbered section")
                buffer.append(line)
                continue

            level = len(heading.group(1))
            if level == 1:
                if current is not None:
                    raise PolicyFormatError("a '#' title may only appear before the first section")
                continue
            if level > 3:
                raise PolicyFormatError(f"headings deeper than '###' are not supported: {line!r}")

            numbered = _NUMBERED.match(heading.group(2))
            if numbered is None:
                raise PolicyFormatError(f"heading has no section number: {line!r}")
            section_id, title = numbered.group(1), numbered.group(2).strip()

            if section_id in seen_ids:
                raise PolicyFormatError(f"section {section_id} appears more than once")
            if level == 3:
                parent = open_headings.get(2)
                if parent is None or not section_id.startswith(parent[0] + "."):
                    raise PolicyFormatError(f"section {section_id} is not inside its parent section")
            seen_ids.add(section_id)

            flush()
            open_headings[level] = (section_id, title)
            if level == 2:
                open_headings.pop(3, None)
            path = tuple(
                f"{sid} {t}" if "." in sid else f"{sid}. {t}" for lvl, (sid, t) in sorted(open_headings.items())
            )
            current = (section_id, title, path)

        flush()
        if not sections:
            raise PolicyFormatError("document has no numbered sections with text")
        return sections
