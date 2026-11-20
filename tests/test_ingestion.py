import sqlite3
import uuid
from pathlib import Path

import pytest

from app.errors import IngestionFailed, PermissionDenied, PolicyFormatError, VersionConflict
from app.policies.chunker import SectionChunker
from app.policies.ingestion import IngestionService
from app.policies.parser import MarkdownPolicyParser
from app.policies.registry import PolicyRegistry
from app.retrieval.embeddings import HashingEmbedder
from app.retrieval.retriever import DenseRetriever
from app.retrieval.vector_store import ChromaPolicyStore

MARKDOWN_DIR = Path(__file__).resolve().parent.parent / "data" / "archive" / "markdown-policies"
YAMUNA_V1 = (MARKDOWN_DIR / "yamuna" / "v1.md").read_text()
YAMUNA_V2 = (MARKDOWN_DIR / "yamuna" / "v2.md").read_text()


class FlakyEmbedder(HashingEmbedder):
    """Fails on the call number given, to simulate an outage mid-ingest."""

    def __init__(self, fail_on_call: int):
        super().__init__()
        self.calls = 0
        self.fail_on_call = fail_on_call

    def embed(self, texts):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise ConnectionError("embedding backend went away")
        return super().embed(texts)


class StoreThatDiesHalfway(ChromaPolicyStore):
    """Writes the first half of a batch, then fails, like a crash mid-write."""

    def __init__(self, path):
        super().__init__(path)
        self.armed = False

    def add(self, chunks, embeddings):
        if not self.armed:
            return super().add(chunks, embeddings)
        half = len(chunks) // 2
        super().add(chunks[:half], embeddings[:half])
        raise OSError("disk full")


class RegistryThatCannotSwitch(PolicyRegistry):
    """The switch transaction fails, e.g. the database is locked."""

    def activate(self, company_id, version):
        raise sqlite3.OperationalError("database is locked")


class RegistryThatGoesDown(RegistryThatCannotSwitch):
    """The switch fails and so does recording the failure: the outcome can't be confirmed."""

    def mark_failed(self, company_id, version, detail):
        raise sqlite3.OperationalError("disk I/O error")


class StoreThatCannotPrune(ChromaPolicyStore):
    def delete_all_except(self, company_id, keep):
        raise OSError("disk unavailable")


_open_stores: list[ChromaPolicyStore] = []


@pytest.fixture(autouse=True)
def close_stores():
    yield
    while _open_stores:
        _open_stores.pop().close()


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "registry.db", tmp_path / f"chroma-{uuid.uuid4().hex}"


def make_service(paths, embedder=None, store=None):
    registry = PolicyRegistry(paths[0])
    store = store or ChromaPolicyStore(paths[1])
    _open_stores.append(store)
    return IngestionService(registry, store, embedder or HashingEmbedder()), registry, store


def as_version(text, version):
    return text.replace("version: 2", f"version: {version}", 1)


def versions_in_store(store, company="yamuna"):
    return {c.version for v in (1, 2, 3) for c in store.chunks(company, v)}


def test_first_ingest_activates(paths):
    service, registry, store = make_service(paths)
    report = service.ingest(YAMUNA_V1)
    assert report.activated and report.previous_version is None
    assert registry.active("yamuna").version == 1
    assert report.chunk_count == len(store.chunks("yamuna", 1)) > 0


def test_new_version_replaces_old_completely(paths):
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V1)
    report = service.ingest(YAMUNA_V2)
    assert report.previous_version == 1
    assert registry.active("yamuna").version == 2
    live = registry.active("yamuna")
    hits = DenseRetriever(store, HashingEmbedder()).retrieve("yamuna", live.version, "escort for night drops", 10)
    assert hits and {h.chunk.version for h in hits} == {2}
    texts = " ".join(c.text for c in store.chunks("yamuna", 2))
    assert "7:30 PM" in texts and "8:00 PM" not in texts


def test_question_pinned_to_old_version_still_finds_it_after_the_switch(paths):
    """A question reads "v1 is live", an upload switches to v2, then the question searches.

    Deleting v1's chunks at the switch used to make this search come back
    empty, so the employee was told the policy didn't cover their question.
    """
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V1)
    pinned = registry.active("yamuna").version
    service.ingest(YAMUNA_V2)
    hits = DenseRetriever(store, HashingEmbedder()).retrieve("yamuna", pinned, "Who approves an airport trip?", 3)
    assert hits and {h.chunk.version for h in hits} == {1}


def test_only_the_live_and_just_retired_versions_are_kept(paths):
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V1)
    service.ingest(YAMUNA_V2)
    assert versions_in_store(store) == {1, 2}
    service.ingest(YAMUNA_V2.replace("version: 2", "version: 3"))
    assert versions_in_store(store) == {2, 3}


def test_restart_keeps_only_the_live_version(paths):
    service, _, _ = make_service(paths)
    service.ingest(YAMUNA_V1)
    service.ingest(YAMUNA_V2)
    restarted, _, store = make_service(paths)
    assert restarted.reconcile().removed_chunks > 0
    assert versions_in_store(store) == {2}


def test_reingesting_the_live_version_is_a_no_op(paths):
    service, _, _ = make_service(paths)
    service.ingest(YAMUNA_V1)
    report = service.ingest(YAMUNA_V1)
    assert not report.activated and report.note == "already active"


def test_older_version_is_refused(paths):
    service, registry, _ = make_service(paths)
    service.ingest(YAMUNA_V2)
    with pytest.raises(VersionConflict):
        service.ingest(YAMUNA_V1)
    assert registry.active("yamuna").version == 2


def test_failed_embedding_keeps_old_version_live(paths):
    service, registry, store = make_service(paths, embedder=FlakyEmbedder(fail_on_call=2))
    service.ingest(YAMUNA_V1)
    with pytest.raises(IngestionFailed):
        service.ingest(YAMUNA_V2)
    assert registry.active("yamuna").version == 1
    assert registry.get("yamuna", 2).status == "failed"
    assert versions_in_store(store) == {1}


def test_crash_halfway_through_writing_chunks_keeps_old_version_live(paths):
    store = StoreThatDiesHalfway(paths[1])
    service, registry, _ = make_service(paths, store=store)
    service.ingest(YAMUNA_V1)
    store.armed = True
    with pytest.raises(IngestionFailed):
        service.ingest(YAMUNA_V2)
    assert registry.active("yamuna").version == 1
    assert versions_in_store(store) == {1}


def test_failed_version_can_be_retried(paths):
    service, registry, _ = make_service(paths, embedder=FlakyEmbedder(fail_on_call=2))
    service.ingest(YAMUNA_V1)
    with pytest.raises(IngestionFailed):
        service.ingest(YAMUNA_V2)
    service.ingest(YAMUNA_V2)
    assert registry.active("yamuna").version == 2


def test_reconcile_cleans_up_after_a_process_crash(paths):
    """Simulates the process dying after writing chunks but before activation."""
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V1)
    doc = MarkdownPolicyParser().parse(YAMUNA_V2)
    registry.begin(doc)
    chunks = SectionChunker().chunk(doc)
    store.add(chunks, HashingEmbedder().embed([c.indexed_text for c in chunks]))

    restarted, registry, store = make_service(paths)
    report = restarted.reconcile()
    assert report.interrupted_versions == [("yamuna", 2)]
    assert report.removed_chunks == len(chunks)
    assert registry.active("yamuna").version == 1
    assert versions_in_store(store) == {1}


def test_admin_cannot_ingest_for_another_company(paths):
    service, registry, _ = make_service(paths)
    with pytest.raises(PermissionDenied):
        service.ingest(YAMUNA_V1, acting_company="gomti")
    assert registry.active("yamuna") is None


def test_bad_document_writes_nothing(paths):
    service, registry, store = make_service(paths)
    with pytest.raises(PolicyFormatError):
        service.ingest("---\ncompany_id: yamuna\n---\n## 1. A\n\nx\n")
    assert registry.active("yamuna") is None
    assert store.company_ids() == []


def test_listeners_hear_about_activation(paths):
    service, _, _ = make_service(paths)
    heard = []
    service.on_activate(heard.append)
    service.ingest(YAMUNA_V1)
    service.ingest(YAMUNA_V1)
    assert heard == ["yamuna"]


def test_concurrent_uploads_of_same_version_leave_one_clean_copy(paths):
    from concurrent.futures import ThreadPoolExecutor

    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda _: service.ingest(YAMUNA_V2), range(4)))
    [activated] = [r for r in reports if r.activated]
    assert registry.active("yamuna").version == 2
    assert versions_in_store(store) == {1, 2}
    assert len(store.chunks("yamuna", 2)) == activated.chunk_count


def test_offboarding_removes_only_former_clients(paths):
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V2)
    service.ingest(YAMUNA_V1.replace("company_id: yamuna", "company_id: gone"))
    notified = []
    service.on_activate(notified.append)

    assert service.offboard_all_except({"yamuna"}) == ["gone"]
    assert registry.company_ids() == ["yamuna"]
    assert store.company_ids() == ["yamuna"]
    assert registry.active("yamuna").version == 2
    assert notified == ["gone"]
    assert service.offboard_all_except({"yamuna"}) == []


def test_version_numbers_may_skip_but_never_go_back(paths):
    service, registry, _ = make_service(paths)
    service.ingest(YAMUNA_V2)
    service.ingest(as_version(YAMUNA_V2, 7))
    assert registry.active("yamuna").version == 7
    with pytest.raises(VersionConflict):
        service.ingest(as_version(YAMUNA_V2, 5))
    assert registry.active("yamuna").version == 7


def test_live_version_number_cannot_be_reused_for_different_text(paths):
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V2)
    with pytest.raises(VersionConflict):
        service.ingest(YAMUNA_V2.replace("7:30 PM", "7:45 PM"))
    texts = " ".join(c.text for c in store.chunks("yamuna", 2))
    assert "7:30 PM" in texts and "7:45 PM" not in texts


def test_failed_switch_keeps_old_version_live_and_removes_new_chunks(paths):
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V1)
    broken = IngestionService(RegistryThatCannotSwitch(paths[0]), store, HashingEmbedder())
    with pytest.raises(IngestionFailed):
        broken.ingest(YAMUNA_V2)
    assert registry.active("yamuna").version == 1
    assert registry.get("yamuna", 2).status == "failed"
    assert versions_in_store(store) == {1}


def test_failed_cleanup_after_the_switch_is_harmless(paths):
    store = StoreThatCannotPrune(paths[1])
    service, registry, _ = make_service(paths, store=store)
    service.ingest(YAMUNA_V1)
    service.ingest(YAMUNA_V2)
    report = service.ingest(as_version(YAMUNA_V2, 3))
    assert report.activated and registry.active("yamuna").version == 3
    assert versions_in_store(store) == {1, 2, 3}  # v1 should have gone but couldn't
    hits = DenseRetriever(store, HashingEmbedder()).retrieve("yamuna", 3, "escort for night drops", 10)
    assert hits and {h.chunk.version for h in hits} == {3}

    restarted, _, clean_store = make_service(paths)
    restarted.reconcile()
    assert versions_in_store(clean_store) == {3}


def test_chunks_are_kept_for_reconcile_when_the_outcome_is_unknown(paths):
    """If the registry can't confirm the new version isn't live, its chunks must not be deleted."""
    service, registry, store = make_service(paths)
    service.ingest(YAMUNA_V1)
    broken = IngestionService(RegistryThatGoesDown(paths[0]), store, HashingEmbedder())
    with pytest.raises(IngestionFailed):
        broken.ingest(YAMUNA_V2)
    assert registry.active("yamuna").version == 1
    assert versions_in_store(store) == {1, 2}

    restarted, registry, store = make_service(paths)
    assert restarted.reconcile().interrupted_versions == [("yamuna", 2)]
    assert registry.active("yamuna").version == 1
    assert versions_in_store(store) == {1}
