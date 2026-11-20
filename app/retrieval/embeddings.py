"""Text embedders.

`MiniLMEmbedder` is the real one: all-MiniLM-L6-v2 run through ONNX on the
CPU. It is a 22M-parameter model (about 90 MB), takes a few milliseconds per
query and needs no API key, so the service, its tests and the evaluation all
work offline. Only answer generation needs a hosted LLM.

`HashingEmbedder` is a deterministic stand-in for tests. It has no semantic
understanding, only word overlap, which is enough to exercise the plumbing
without downloading a model.
"""

import hashlib
import math
import os
import re
from functools import cached_property
from typing import Protocol


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in order."""
        ...


class MiniLMEmbedder:
    """MiniLM through Chroma's ONNX wrapper, with the CPU threads per query capped.

    ONNX Runtime's default is one thread per core for every query. That is
    fastest for one query at a time (about 18 ms here), but several searches
    at once then fight over the cores, and when the machine is also busy they
    slow sharply: 8 parallel queries took 1.2 s at p95, past the 0.8 s
    retrieval timeout, so questions ended in "try again". With 2 threads per
    query, one query takes about 37 ms and 8 parallel ones about 0.4 s at p95
    on the same busy machine. The vectors are the same either way.
    """

    name = "all-MiniLM-L6-v2"
    THREADS_PER_QUERY = 2

    def __init__(self, threads_per_query: int = THREADS_PER_QUERY) -> None:
        self._model = _minilm(threads_per_query)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(x) for x in vector] for vector in self._model(texts)]


def _minilm(threads_per_query: int):
    # Imported here so test runs that use the hashing embedder never touch
    # onnxruntime or the model download.
    import onnxruntime as ort
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    class ThreadCappedMiniLM(ONNXMiniLM_L6_V2):
        # Chroma builds its session here with default threading; the rest of
        # its wrapper (download, tokenizer, pooling) is used unchanged.
        # chromadb is pinned, and tests/test_embeddings.py checks the vectors.
        @cached_property
        def model(self):
            options = ort.SessionOptions()
            options.log_severity_level = 3
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.intra_op_num_threads = threads_per_query
            path = os.path.join(self.DOWNLOAD_PATH, self.EXTRACTED_FOLDER_NAME, "model.onnx")
            return ort.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=options)

    return ThreadCappedMiniLM()


class HashingEmbedder:
    name = "hashing"

    _TOKEN = re.compile(r"[a-z0-9]+")

    def __init__(self, dimensions: int = 256):
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in self._TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]
