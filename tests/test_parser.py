from datetime import date
from pathlib import Path

import pytest

from app.errors import PolicyFormatError
from app.policies.parser import MarkdownPolicyParser

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"

HEADER = """---
company_id: acme
company_name: Acme
doc_id: acme-policy
title: Acme Policy
version: 3
effective_from: 2026-02-01
---
"""


def parse(text: str):
    return MarkdownPolicyParser().parse(text)


def test_parses_front_matter_and_sections():
    doc = parse(
        HEADER
        + """
# Acme Policy

## 1. Scope

Everyone.

## 2. Night travel

### 2.1 Escort

Escort after 9 PM.
"""
    )
    assert (doc.company_id, doc.version, doc.effective_from) == ("acme", 3, date(2026, 2, 1))
    assert [s.section_id for s in doc.sections] == ["1", "2.1"]
    escort = doc.sections[1]
    assert escort.title == "Escort"
    assert escort.path == ("2. Night travel", "2.1 Escort")
    assert escort.body == "Escort after 9 PM."


def test_parent_with_its_own_text_is_kept():
    doc = parse(HEADER + "## 1. Rules\n\nIntro text.\n\n### 1.1 Detail\n\nMore.\n")
    assert [(s.section_id, s.body) for s in doc.sections] == [("1", "Intro text."), ("1.1", "More.")]


def test_checksum_changes_with_content():
    a = parse(HEADER + "## 1. Scope\n\nA.\n")
    b = parse(HEADER + "## 1. Scope\n\nB.\n")
    assert a.checksum != b.checksum


@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "empty"),
        ("## 1. Scope\n\nNo header.\n", "front matter"),
        (HEADER.replace("version: 3", "version: zero") + "## 1. A\n\nx\n", "version"),
        (HEADER.replace("version: 3\n", "") + "## 1. A\n\nx\n", "missing: version"),
        (HEADER.replace("company_id: acme", "company_id: ../etc") + "## 1. A\n\nx\n", "company_id"),
        (HEADER + "## Scope\n\nUnnumbered.\n", "no section number"),
        (HEADER + "## 1. A\n\nx\n\n## 1. B\n\ny\n", "more than once"),
        (HEADER + "## 1. A\n\nx\n\n### 2.1 B\n\ny\n", "not inside"),
        (HEADER + "Loose text.\n\n## 1. A\n\nx\n", "before the first"),
        (HEADER + "## 1. Empty\n", "no numbered sections"),
    ],
)
def test_rejects_malformed_documents(text, reason):
    with pytest.raises(PolicyFormatError, match=reason):
        parse(text)


@pytest.mark.parametrize("path", sorted(POLICY_DIR.glob("*/v*.md")), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_sample_policies_parse(path):
    doc = parse(path.read_text())
    assert doc.company_id == path.parent.name
    assert len(doc.sections) >= 8
