import pytest

from app.config import RetrieverKind
from app.container import Container
from app.retrieval.bm25 import BM25Index, stem, tokenize
from app.retrieval.retriever import HybridRetriever, reciprocal_rank_fusion
from tests.factories import chunk


def test_rrf_rewards_agreement_between_rankings():
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]], k=60)
    assert max(scores, key=scores.get) == "b"
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)


def test_tokenize_keeps_numbers_and_drops_stopwords():
    assert tokenize("What is the INR 250 no-show fee?") == ["inr", "250", "no", "show", "fee"]


@pytest.mark.parametrize(
    "a,b", [("saturday", "saturdays"), ("book", "booked"), ("approve", "approves"), ("booking", "book")]
)
def test_stemming_matches_simple_word_forms(a, b):
    assert stem(a) == stem(b)


def test_bm25_matches_exact_terms():
    index = BM25Index(
        [
            chunk(section_id="8", text="INR 250 is deducted from the employee's salary."),
            chunk(section_id="5", text="Weekend cabs need approval."),
            chunk(section_id="7", text="Trips are capped at INR 1,000."),
        ]
    )
    hits = index.search("how much salary is cut", k=3)
    assert hits[0][0].section_id == "8"
    assert index.search("the and of", k=3) == []


@pytest.fixture
def container(settings):
    with Container(settings.model_copy(update={"retriever": RetrieverKind.HYBRID})) as container:
        container.startup()
        yield container


def test_container_builds_hybrid_retriever(container):
    assert isinstance(container.retriever, HybridRetriever)


def test_hybrid_finds_keyword_match_and_stays_in_tenant(container):
    results = container.retriever.retrieve(
        "gomti", 1, "How much money gets cut from my salary if I keep missing my cab?", 3
    )
    assert results[0].chunk.section_id == "8"
    assert {r.chunk.company_id for r in results} == {"gomti"}
    assert len(results) == 3


def test_bm25_index_follows_new_versions(container, settings):
    from tests.conftest import MARKDOWN_DIR

    v3 = (
        (MARKDOWN_DIR / "yamuna" / "v2.md")
        .read_text()
        .replace("version: 2", "version: 3")
        .replace("Guests must make their", "Pets and guests must make their")
    )
    container.retriever.retrieve("yamuna", 2, "guests", 3)  # warm the cache
    container.ingestion.ingest(v3)
    results = container.retriever.retrieve("yamuna", 3, "pets and guests", 3)
    assert results[0].chunk.version == 3 and results[0].chunk.section_id == "9"
