import math

from app.retrieval.embeddings import HashingEmbedder


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingEmbedder()
    first, again = embedder.embed(["night drop escort"])[0], embedder.embed(["night drop escort"])[0]
    assert first == again
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)


def test_hashing_embedder_reflects_word_overlap():
    embedder = HashingEmbedder()
    query, related, unrelated = embedder.embed(
        ["escort on night drops", "security escort for night drops", "guest travel approval"]
    )
    assert cosine(query, related) > cosine(query, unrelated)
