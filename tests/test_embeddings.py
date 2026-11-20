import math
from pathlib import Path

import pytest

from app.retrieval.embeddings import HashingEmbedder, MiniLMEmbedder


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


def _minilm_downloaded() -> bool:
    try:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    except ImportError:
        return False
    folder = Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME
    return (folder / "model.onnx").exists()


needs_model = pytest.mark.skipif(not _minilm_downloaded(), reason="MiniLM model not downloaded (the image has it)")


@needs_model
def test_capping_threads_does_not_change_the_vectors():
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    texts = ["Who approves an airport trip?", "A security escort travels on every drop after 7:30 PM."]
    capped = MiniLMEmbedder(threads_per_query=2).embed(texts)
    default = [list(v) for v in ONNXMiniLM_L6_V2()(texts)]
    for ours, theirs in zip(capped, default, strict=True):
        assert max(abs(a - b) for a, b in zip(ours, theirs, strict=True)) < 1e-5


@needs_model
def test_the_session_uses_the_capped_thread_count():
    embedder = MiniLMEmbedder(threads_per_query=2)
    embedder.embed(["warm up"])
    assert embedder._model.model.get_session_options().intra_op_num_threads == 2
