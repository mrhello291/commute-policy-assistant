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
import re
from typing import Protocol


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in order."""
        ...


class MiniLMEmbedder:
    name = "all-MiniLM-L6-v2"

    def __init__(self) -> None:
        # Imported here so test runs that use the hashing embedder never
        # touch onnxruntime or the model download.
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        self._model = ONNXMiniLM_L6_V2()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(x) for x in vector] for vector in self._model(texts)]


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
