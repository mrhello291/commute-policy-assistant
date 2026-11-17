"""Loading a policy version so that it replaces the old one all at once.

The sequence is the classic blue/green switch:

1. Parse and validate the whole document. A bad document fails here, before
   anything is written.
2. Record the version as `staging` in the registry.
3. Embed every chunk and write it to the vector store tagged with the new
   version number. Retrieval always filters on the live version, so these
   chunks are invisible.
4. Flip the registry's live pointer in one transaction. This is the only
   step that changes what users see.
5. Delete the old version's chunks. If this fails nothing is wrong, only
   untidy: `reconcile()` removes leftovers on the next start.

If anything in steps 2 to 4 fails, the old version stays live and the
half-written chunks are removed.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from app.errors import IngestionFailed, PermissionDenied
from app.policies.chunker import SectionChunker
from app.policies.models import PolicyDocument
from app.policies.parser import MarkdownPolicyParser
from app.policies.registry import PolicyRegistry
from app.policies.scanner import InjectionScanner
from app.retrieval.embeddings import Embedder
from app.retrieval.vector_store import ChromaPolicyStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionReport:
    company_id: str
    version: int
    activated: bool
    chunk_count: int = 0
    previous_version: int | None = None
    flagged_sections: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class ReconcileReport:
    interrupted_versions: list[tuple[str, int]] = field(default_factory=list)
    removed_chunks: int = 0


class IngestionService:
    def __init__(
        self,
        registry: PolicyRegistry,
        store: ChromaPolicyStore,
        embedder: Embedder,
        parser: MarkdownPolicyParser | None = None,
        chunker: SectionChunker | None = None,
        scanner: InjectionScanner | None = None,
    ):
        self._registry = registry
        self._store = store
        self._embedder = embedder
        self._parser = parser or MarkdownPolicyParser()
        self._chunker = chunker or SectionChunker()
        self._scanner = scanner or InjectionScanner()
        self._listeners: list[Callable[[str], None]] = []
        # Uploads are rare, so one lock for all of them is simplest. Without
        # it, two uploads of the same version could each clean up the other's
        # chunks. (One process only; several workers would need a DB lock.)
        self._lock = threading.Lock()

    def on_activate(self, listener: Callable[[str], None]) -> None:
        """Call `listener(company_id)` whenever a company's live version changes."""
        self._listeners.append(listener)

    def ingest(self, raw: str, acting_company: str | None = None) -> IngestionReport:
        """Ingest one policy document.

        `acting_company` is the company of the admin doing the upload, taken
        from their token. It is None only for the trusted seed loader.
        """
        doc = self._parser.parse(raw)
        if acting_company is not None and doc.company_id != acting_company:
            raise PermissionDenied("policy belongs to a different company")
        with self._lock:
            return self._ingest(doc)

    def _ingest(self, doc: PolicyDocument) -> IngestionReport:
        live = self._registry.active(doc.company_id)
        if live and live.version == doc.version and live.checksum == doc.checksum:
            return IngestionReport(doc.company_id, doc.version, activated=False, note="already active")

        self._registry.begin(doc)
        try:
            chunks = self._scanner.flag(self._chunker.chunk(doc))
            embeddings = self._embedder.embed([c.indexed_text for c in chunks])
            self._store.add(chunks, embeddings)
            previous = self._registry.activate(doc.company_id, doc.version)
        except Exception as exc:
            log.exception("ingestion of %s v%s failed", doc.company_id, doc.version)
            self._abandon(doc.company_id, doc.version, repr(exc))
            raise IngestionFailed(f"could not ingest {doc.company_id} v{doc.version}") from exc

        flagged = tuple(sorted({c.section_id for c in chunks if c.flags}))
        if flagged:
            log.warning(
                "policy %s v%s has instruction-like text in sections %s",
                doc.company_id, doc.version, ", ".join(flagged),
            )
        if previous is not None:
            self._delete_quietly(doc.company_id, previous)
        for listener in self._listeners:
            listener(doc.company_id)

        log.info("activated %s v%s (%d chunks, replaced v%s)", doc.company_id, doc.version, len(chunks), previous)
        return IngestionReport(
            company_id=doc.company_id,
            version=doc.version,
            activated=True,
            chunk_count=len(chunks),
            previous_version=previous,
            flagged_sections=flagged,
        )

    def reconcile(self) -> ReconcileReport:
        """Clean up after crashes: fail interrupted versions, drop stray chunks."""
        interrupted = []
        for version in self._registry.staging():
            self._registry.mark_failed(version.company_id, version.version, "interrupted before activation")
            interrupted.append((version.company_id, version.version))

        removed = 0
        for company_id in self._store.company_ids():
            live = self._registry.active(company_id)
            removed += self._store.delete_all_except(company_id, live.version if live else None)

        if interrupted or removed:
            log.warning("reconcile: interrupted=%s removed_chunks=%d", interrupted, removed)
        return ReconcileReport(interrupted, removed)

    def _abandon(self, company_id: str, version: int, detail: str) -> None:
        try:
            self._registry.mark_failed(company_id, version, detail)
            live = self._registry.active(company_id)
        except Exception:
            log.exception("could not mark %s v%s as failed", company_id, version)
            return  # can't confirm it isn't live, so leave the chunks for reconcile()
        if live is None or live.version != version:
            self._delete_quietly(company_id, version)

    def _delete_quietly(self, company_id: str, version: int) -> None:
        try:
            self._store.delete_version(company_id, version)
        except Exception:
            # Harmless: retrieval only reads the live version, and
            # reconcile() removes these on the next start.
            log.exception("could not delete chunks of %s v%s", company_id, version)
