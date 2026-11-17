import dataclasses

import pytest

from app.errors import TenantIsolationError
from app.retrieval.retriever import DenseRetriever, ensure_same_tenant


@pytest.fixture
def container(seeded_container):
    return seeded_container


def test_dense_retriever_finds_the_relevant_section(container):
    retriever = DenseRetriever(container.store, container.embedder)
    results = retriever.retrieve("bluepeak", 1, "security escort for women on night drops", k=3)
    assert len(results) == 3
    # The hashing embedder only sees word overlap, so ask for "in the top 3"
    # here; ranking quality with the real model is measured by the eval set.
    assert "4.2" in {r.chunk.section_id for r in results}
    assert results == sorted(results, key=lambda r: r.score, reverse=True)


def test_tenant_check_rejects_foreign_chunk(container):
    retriever = DenseRetriever(container.store, container.embedder)
    results = retriever.retrieve("bluepeak", 1, "escort", k=1)
    foreign = dataclasses.replace(results[0], chunk=dataclasses.replace(results[0].chunk, company_id="arcadia"))
    with pytest.raises(TenantIsolationError):
        ensure_same_tenant("bluepeak", 1, [foreign])


def test_tenant_check_rejects_retired_version(container):
    retriever = DenseRetriever(container.store, container.embedder)
    results = retriever.retrieve("arcadia", 2, "escort", k=1)
    with pytest.raises(TenantIsolationError):
        ensure_same_tenant("arcadia", 1, results)
