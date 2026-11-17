"""Builds the application's object graph in one place.

There is no DI framework: a plain class creates each collaborator once and
hands it to whatever needs it. Tests build their own `Container` with fakes.
"""

import logging
from datetime import timedelta

from app.auth import TokenIssuer, TokenVerifier
from app.config import EmbedderKind, Settings
from app.policies.ingestion import IngestionService
from app.policies.registry import PolicyRegistry
from app.policies.seed import PolicySeeder
from app.retrieval.embeddings import Embedder, HashingEmbedder, MiniLMEmbedder
from app.retrieval.vector_store import ChromaPolicyStore

log = logging.getLogger(__name__)


def build_embedder(kind: EmbedderKind) -> Embedder:
    if kind is EmbedderKind.HASHING:
        return HashingEmbedder()
    return MiniLMEmbedder()


class Container:
    def __init__(self, settings: Settings, embedder: Embedder | None = None):
        self.settings = settings
        self.token_issuer = TokenIssuer(
            secret=settings.jwt_secret,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            ttl=timedelta(minutes=settings.jwt_ttl_minutes),
        )
        self.token_verifier = TokenVerifier(
            secret=settings.jwt_secret,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )

        self.embedder = embedder or build_embedder(settings.embedder)
        self.registry = PolicyRegistry(settings.data_dir / "registry.db")
        self.store = ChromaPolicyStore(settings.data_dir / "chroma")
        self.ingestion = IngestionService(self.registry, self.store, self.embedder)

    def startup(self) -> None:
        """Repair anything a previous crash left behind, then load sample policies."""
        self.ingestion.reconcile()
        reports = PolicySeeder(self.ingestion).seed(self.settings.policy_seed_dir)
        live = self.registry.active_companies()
        log.info(
            "startup complete: %d new versions loaded, live policies: %s",
            sum(r.activated for r in reports),
            ", ".join(f"{c} v{self.registry.active(c).version}" for c in sorted(live)) or "none",
        )
