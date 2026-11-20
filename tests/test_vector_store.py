import uuid

import pytest

from app.policies.models import Chunk
from app.retrieval.embeddings import HashingEmbedder
from app.retrieval.vector_store import ChromaPolicyStore


def make_chunk(company="acme", version=1, section="1", text="Escort after 9 PM.", flags=()):
    return Chunk(
        chunk_id=f"{company}:doc:v{version}:{section}:0",
        company_id=company,
        company_name=company.title(),
        doc_id="doc",
        doc_title=f"{company.title()} Policy",
        version=version,
        section_id=section,
        section_title="Escort",
        section_path=(f"{section}. Escort",),
        part=0,
        text=text,
        flags=flags,
    )


@pytest.fixture
def store(tmp_path):
    store = ChromaPolicyStore(tmp_path / f"chroma-{uuid.uuid4().hex}")
    yield store
    store.close()


@pytest.fixture
def embedder():
    return HashingEmbedder()


def add(store, embedder, *chunks):
    store.add(list(chunks), embedder.embed([c.indexed_text for c in chunks]))


def test_round_trip_preserves_every_field(store, embedder):
    chunk = make_chunk(flags=("addresses_ai",))
    add(store, embedder, chunk)
    [hit] = store.query("acme", 1, embedder.embed([chunk.indexed_text])[0], k=3)
    assert hit.chunk == chunk
    assert hit.similarity == pytest.approx(1.0, abs=1e-3)


def test_query_only_sees_requested_version(store, embedder):
    add(store, embedder, make_chunk(version=1, text="old rule"), make_chunk(version=2, text="new rule"))
    hits = store.query("acme", 2, embedder.embed(["rule"])[0], k=5)
    assert [h.chunk.text for h in hits] == ["new rule"]


def test_page_range_survives_the_round_trip(store, embedder):
    import dataclasses

    add(store, embedder, dataclasses.replace(make_chunk(text="paged rule"), pages=(2, 3)))
    add(store, embedder, make_chunk(company="zeta", text="markdown rule"))
    [paged] = store.query("acme", 1, embedder.embed(["rule"])[0], k=5)
    [unpaged] = store.query("zeta", 1, embedder.embed(["rule"])[0], k=5)
    assert (paged.chunk.pages, unpaged.chunk.pages) == ((2, 3), None)


def test_companies_live_in_separate_collections(store, embedder):
    add(store, embedder, make_chunk(company="acme", text="acme escort rule"))
    add(store, embedder, make_chunk(company="zeta", text="zeta escort rule"))
    hits = store.query("acme", 1, embedder.embed(["zeta escort rule"])[0], k=5)
    assert {h.chunk.company_id for h in hits} == {"acme"}
    assert store.company_ids() == ["acme", "zeta"]


def test_delete_all_except_keeps_the_given_versions(store, embedder):
    add(store, embedder, *(make_chunk(version=v) for v in (1, 2, 3, 4)))
    assert store.delete_all_except("acme", {2, 3}) == 2
    assert [c.version for c in store.chunks("acme", 2) + store.chunks("acme", 3)] == [2, 3]
    assert store.chunks("acme", 1) == store.chunks("acme", 4) == []


def test_delete_all_except_nothing_empties_the_company(store, embedder):
    add(store, embedder, make_chunk(version=1), make_chunk(version=2))
    assert store.delete_all_except("acme", set()) == 2


def test_mixed_company_batch_is_refused(store, embedder):
    chunks = [make_chunk(company="acme"), make_chunk(company="zeta")]
    with pytest.raises(ValueError):
        store.add(chunks, embedder.embed(["a", "b"]))
