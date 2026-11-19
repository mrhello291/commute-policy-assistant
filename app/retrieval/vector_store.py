"""Policy chunks in Chroma, one collection per company.

Keeping each company in its own collection makes isolation structural: the
collection name comes from the caller's token, so a query physically cannot
reach another company's vectors, even if a metadata filter were wrong. Each
chunk still records its company and version so results can be checked again
on the way out.
"""

from collections.abc import Collection
from pathlib import Path

import chromadb

from app.policies.models import Chunk
from app.retrieval.models import ScoredChunk

_COLLECTION_PREFIX = "policy_"


def collection_name(company_id: str) -> str:
    return f"{_COLLECTION_PREFIX}{company_id}"


class ChromaPolicyStore:
    def __init__(self, path: Path | None = None):
        settings = chromadb.Settings(anonymized_telemetry=False)
        if path is None:
            self._client = chromadb.EphemeralClient(settings=settings)
        else:
            path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(path), settings=settings)

    def _collection(self, company_id: str):
        return self._client.get_or_create_collection(
            collection_name(company_id),
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )

    # -- writes -----------------------------------------------------------

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("every chunk needs exactly one embedding")
        if not chunks:
            return
        companies = {c.company_id for c in chunks}
        if len(companies) != 1:
            raise ValueError("a batch must belong to a single company")
        self._collection(companies.pop()).upsert(
            ids=[c.chunk_id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[self._to_metadata(c) for c in chunks],
        )

    def delete_version(self, company_id: str, version: int) -> None:
        self._collection(company_id).delete(where={"version": version})

    def delete_all_except(self, company_id: str, keep: Collection[int]) -> int:
        """Remove every chunk whose version is not in `keep`. Returns how many were removed."""
        collection = self._collection(company_id)
        where = {"version": {"$nin": sorted(keep)}} if keep else None
        ids = collection.get(where=where, include=[])["ids"]
        if ids:
            collection.delete(ids=ids)
        return len(ids)

    def delete_company(self, company_id: str) -> None:
        """Drop a company's whole collection, if it has one."""
        if company_id in self.company_ids():
            self._client.delete_collection(collection_name(company_id))

    # -- reads ------------------------------------------------------------

    def query(self, company_id: str, version: int, embedding: list[float], k: int) -> list[ScoredChunk]:
        collection = self._collection(company_id)
        result = collection.query(
            query_embeddings=[embedding],
            n_results=k,
            where={"version": version},
            include=["documents", "metadatas", "distances"],
        )
        scored = []
        for chunk_id, text, meta, distance in zip(
            result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        ):
            similarity = 1.0 - float(distance)
            scored.append(ScoredChunk(self._to_chunk(chunk_id, text, meta), similarity, similarity))
        return scored

    def chunks(self, company_id: str, version: int) -> list[Chunk]:
        result = self._collection(company_id).get(where={"version": version}, include=["documents", "metadatas"])
        chunks = [
            self._to_chunk(i, t, m)
            for i, t, m in zip(result["ids"], result["documents"], result["metadatas"], strict=True)
        ]
        return sorted(chunks, key=lambda c: c.chunk_id)

    def company_ids(self) -> list[str]:
        return sorted(
            c.name.removeprefix(_COLLECTION_PREFIX)
            for c in self._client.list_collections()
            if c.name.startswith(_COLLECTION_PREFIX)
        )

    # -- mapping ----------------------------------------------------------

    @staticmethod
    def _to_metadata(chunk: Chunk) -> dict:
        return {
            "company_id": chunk.company_id,
            "company_name": chunk.company_name,
            "doc_id": chunk.doc_id,
            "doc_title": chunk.doc_title,
            "version": chunk.version,
            "section_id": chunk.section_id,
            "section_title": chunk.section_title,
            "section_path": "\x1f".join(chunk.section_path),
            "part": chunk.part,
            "flags": ",".join(chunk.flags),
            "contextual": chunk.contextual,
            # Chroma metadata can't hold None or tuples: 0 means "no pages" (Markdown).
            "page_start": chunk.pages[0] if chunk.pages else 0,
            "page_end": chunk.pages[1] if chunk.pages else 0,
        }

    @staticmethod
    def _to_chunk(chunk_id: str, text: str, meta: dict) -> Chunk:
        return Chunk(
            chunk_id=chunk_id,
            company_id=meta["company_id"],
            company_name=meta["company_name"],
            doc_id=meta["doc_id"],
            doc_title=meta["doc_title"],
            version=int(meta["version"]),
            section_id=meta["section_id"],
            section_title=meta["section_title"],
            section_path=tuple(meta["section_path"].split("\x1f")),
            part=int(meta["part"]),
            text=text,
            flags=tuple(f for f in meta["flags"].split(",") if f),
            contextual=bool(meta.get("contextual", True)),
            pages=(int(meta["page_start"]), int(meta["page_end"])) if meta.get("page_start") else None,
        )
