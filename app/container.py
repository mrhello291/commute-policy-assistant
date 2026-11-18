"""Builds the application's object graph in one place.

There is no DI framework: a plain class creates each collaborator once and
hands it to whatever needs it. Tests build their own `Container` with fakes.
"""

import logging
from datetime import UTC, datetime, timedelta

from app.answering.service import QuestionAnsweringService
from app.auth import TokenIssuer, TokenVerifier
from app.auth.directory import EmployeeDirectory
from app.auth.service import AuthService
from app.auth.token_store import TokenStore
from app.config import EmbedderKind, LLMProvider, RetrieverKind, Settings
from app.llm.base import LLMClient, UnconfiguredLLM
from app.llm.gemini_client import GeminiLLMClient
from app.llm.groq_client import GroqLLMClient
from app.policies.chunker import SectionChunker
from app.policies.ingestion import IngestionService
from app.policies.registry import PolicyRegistry
from app.policies.seed import PolicySeeder
from app.retrieval.bm25 import BM25Cache
from app.retrieval.embeddings import Embedder, HashingEmbedder, MiniLMEmbedder
from app.retrieval.retriever import DenseRetriever, HybridRetriever, Retriever
from app.retrieval.vector_store import ChromaPolicyStore

log = logging.getLogger(__name__)


def build_embedder(kind: EmbedderKind) -> Embedder:
    if kind is EmbedderKind.HASHING:
        return HashingEmbedder()
    return MiniLMEmbedder()


def build_llm(settings: Settings) -> LLMClient:
    """Pick the LLM from settings. With no key at all, the service still runs
    and tells users the assistant is unavailable instead of crashing."""
    provider = settings.llm_provider
    if provider is LLMProvider.AUTO:
        if settings.groq_api_key:
            provider = LLMProvider.GROQ
        elif settings.gemini_api_key:
            provider = LLMProvider.GEMINI
        else:
            provider = LLMProvider.NONE

    if provider is LLMProvider.GROQ and settings.groq_api_key:
        return GroqLLMClient(settings.groq_api_key.get_secret_value(), settings.groq_model)
    if provider is LLMProvider.GEMINI and settings.gemini_api_key:
        return GeminiLLMClient(settings.gemini_api_key.get_secret_value(), settings.gemini_model)
    if provider is not LLMProvider.NONE:
        log.warning("LLM provider %s selected but its API key is not set", provider)
    log.warning("no LLM configured: questions will get a 'temporarily unavailable' reply")
    return UnconfiguredLLM()


class Container:
    def __init__(self, settings: Settings, embedder: Embedder | None = None, llm: LLMClient | None = None):
        self.settings = settings
        self.token_store = TokenStore(settings.data_dir / "auth.db")
        self.auth = AuthService(
            directory=EmployeeDirectory.load(settings.employee_directory),
            store=self.token_store,
            issuer=TokenIssuer(
                secret=settings.jwt_secret,
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
                ttl=timedelta(minutes=settings.jwt_ttl_minutes),
            ),
            verifier=TokenVerifier(
                secret=settings.jwt_secret,
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
            ),
            max_sessions_per_employee=settings.max_sessions_per_employee,
        )

        self.embedder = embedder or build_embedder(settings.embedder)
        self.registry = PolicyRegistry(settings.data_dir / "registry.db")
        self.store = ChromaPolicyStore(settings.data_dir / "chroma")
        self.ingestion = IngestionService(
            self.registry,
            self.store,
            self.embedder,
            chunker=SectionChunker(contextual_headers=settings.contextual_headers),
        )
        self.llm = llm or build_llm(settings)
        self.retriever = self._build_retriever()
        self.answering = QuestionAnsweringService(self.registry, self.retriever, self.llm, settings)

    def _build_retriever(self) -> Retriever:
        if self.settings.retriever is RetrieverKind.DENSE:
            return DenseRetriever(self.store, self.embedder)
        bm25 = BM25Cache(self.store)
        self.ingestion.on_activate(bm25.invalidate)
        return HybridRetriever(self.store, self.embedder, bm25)

    def startup(self) -> None:
        """Repair anything a previous crash left behind, then load sample policies."""
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.token_retention_days)
        purged = self.token_store.purge(expired_before=int(cutoff.timestamp()))
        if purged:
            log.info("removed %d token records older than %d days", purged, self.settings.token_retention_days)
        self.ingestion.reconcile()
        reports = PolicySeeder(self.ingestion).seed(self.settings.policy_seed_dir)
        live = self.registry.active_companies()
        log.info(
            "startup complete: %d new versions loaded, live policies: %s",
            sum(r.activated for r in reports),
            ", ".join(f"{c} v{self.registry.active(c).version}" for c in sorted(live)) or "none",
        )
