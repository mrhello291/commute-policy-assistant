import uuid
from pathlib import Path

import pytest

from app.errors import IngestionFailed, PermissionDenied, PolicyFormatError, VersionConflict
from app.policies.chunker import SectionChunker
from app.policies.ingestion import IngestionService
from app.policies.parser import MarkdownPolicyParser
from app.policies.registry import PolicyRegistry
from app.retrieval.embeddings import HashingEmbedder
from app.retrieval.vector_store import ChromaPolicyStore

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"
YAMUNA_V1 = (POLICY_DIR / "yamuna" / "v1.md").read_text()
YAMUNA_V2 = (POLICY_DIR / "yamuna" / "v2.md").read_text()


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


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "registry.db", tmp_path / f"chroma-{uuid.uuid4().hex}"


def make_service(paths, embedder=None, store=None):
    registry = PolicyRegistry(paths[0])
    store = store or ChromaPolicyStore(paths[1])
    return IngestionService(registry, store, embedder or HashingEmbedder()), registry, store


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
    assert versions_in_store(store) == {2}
    texts = " ".join(c.text for c in store.chunks("yamuna", 2))
    assert "7:30 PM" in texts and "8:00 PM" not in texts


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
    assert versions_in_store(store) == {2}
    assert len(store.chunks("yamuna", 2)) == activated.chunk_count
