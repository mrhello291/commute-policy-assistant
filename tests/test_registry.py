import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from app.errors import DifferentDocument, VersionConflict
from app.policies.models import PolicyDocument
from app.policies.registry import PolicyRegistry

DOC = PolicyDocument("acme", "Acme", "acme-policy", "Acme Policy", 1, date(2026, 1, 1), (), "sum-1")


@pytest.fixture
def registry(tmp_path):
    return PolicyRegistry(tmp_path / "registry.db")


def stage_and_activate(registry, doc):
    registry.begin(doc)
    return registry.activate(doc.company_id, doc.version)


def test_staged_version_is_not_live_until_activated(registry):
    registry.begin(DOC)
    assert registry.active("acme") is None
    assert registry.activate("acme", 1) is None
    assert registry.active("acme").version == 1


def test_activation_retires_previous_version(registry):
    stage_and_activate(registry, DOC)
    previous = stage_and_activate(registry, replace(DOC, version=2, checksum="sum-2"))
    assert previous == 1
    assert registry.active("acme").version == 2
    assert registry.get("acme", 1).status == "retired"


def test_versions_only_move_forward(registry):
    stage_and_activate(registry, replace(DOC, version=2))
    with pytest.raises(VersionConflict):
        registry.begin(replace(DOC, version=2, checksum="other"))
    with pytest.raises(VersionConflict):
        registry.begin(replace(DOC, version=1))


def test_failed_version_can_be_retried(registry):
    registry.begin(DOC)
    registry.mark_failed("acme", 1, "embedding failed")
    assert registry.get("acme", 1).status == "failed"
    stage_and_activate(registry, DOC)
    assert registry.active("acme").version == 1


def test_only_staged_versions_can_be_activated(registry):
    with pytest.raises(VersionConflict):
        registry.activate("acme", 1)
    registry.begin(DOC)
    registry.mark_failed("acme", 1, "x")
    with pytest.raises(VersionConflict):
        registry.activate("acme", 1)


def test_companies_are_independent(registry):
    stage_and_activate(registry, DOC)
    stage_and_activate(registry, replace(DOC, company_id="zeta", company_name="Zeta", version=5))
    assert registry.active_companies() == {"acme": "Acme", "zeta": "Zeta"}
    assert registry.active("acme").version == 1


def test_database_refuses_two_active_versions(registry, tmp_path):
    stage_and_activate(registry, DOC)
    registry.begin(replace(DOC, version=2))
    conn = sqlite3.connect(tmp_path / "registry.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE policy_versions SET status = 'active' WHERE version = 2")


def test_a_different_document_cannot_replace_the_live_policy(registry):
    stage_and_activate(registry, DOC)
    sop = replace(DOC, doc_id="night-escort-sop", title="Night Shift Escort SOP", version=2, checksum="sum-2")
    with pytest.raises(DifferentDocument) as refused:
        registry.begin(sop)
    assert (refused.value.live_doc_id, refused.value.live_title) == ("acme-policy", "Acme Policy")
    assert registry.active("acme").version == 1
    assert registry.get("acme", 2) is None
