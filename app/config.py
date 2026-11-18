"""Runtime configuration.

Every tunable lives here and is read from environment variables (or a local
`.env` file). Secrets such as API keys are only ever supplied this way, so
nothing sensitive needs to be written into the repository.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Only good enough for a laptop. Starting in prod with it is refused.
DEV_JWT_SECRET = "dev-only-secret-change-me-before-deploying"


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"


class EmbedderKind(StrEnum):
    MINILM = "minilm"
    HASHING = "hashing"


class RetrieverKind(StrEnum):
    DENSE = "dense"
    HYBRID = "hybrid"


class LLMProvider(StrEnum):
    AUTO = "auto"  # first provider with a key set: Groq, then Gemini
    GROQ = "groq"
    GEMINI = "gemini"
    NONE = "none"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Environment = Environment.DEV

    # Where the SQLite registry and Chroma files are written.
    data_dir: Path = Path("var")
    # Policy documents loaded on first start.
    policy_seed_dir: Path = Path("data/policies")

    # "hashing" is only meant for tests; it has no semantic understanding.
    embedder: EmbedderKind = EmbedderKind.MINILM
    retriever: RetrieverKind = RetrieverKind.DENSE
    # Prefix each chunk with its document and section headings before
    # indexing. Turn off only to reproduce the "before" evaluation run.
    contextual_headers: bool = True

    # LLM provider. Keys come from the environment only.
    llm_provider: LLMProvider = LLMProvider.AUTO
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"

    # Answering limits. See the README section "Staying inside the numbers".
    max_llm_calls: int = Field(default=2, ge=1, le=2)
    max_cited_chunks: int = Field(default=3, ge=1, le=3)
    request_deadline_s: float = 2.8
    llm_timeout_s: float = 2.2
    retrieval_timeout_s: float = 0.8
    # Don't start an LLM call with less time than this left; say "try again" instead.
    min_llm_window_s: float = 0.5
    # Below this cosine similarity the policy is treated as silent on the
    # question and no LLM call is made.
    evidence_min_similarity: float = 0.25

    # JWT (the mock identity provider and the API share this secret).
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=32)
    jwt_issuer: str = "moveinsync-mock-idp"
    jwt_audience: str = "commute-policy-assistant"
    jwt_ttl_minutes: int = Field(default=480, ge=1)

    # Sign-in. The directory stands in for each company's HR system.
    employee_directory: Path = Path("data/employees.json")
    # Signing in on one more device than this ends the oldest session.
    max_sessions_per_employee: int = Field(default=5, ge=1, le=50)
    # Expired and revoked token records are kept this long as an audit trail.
    token_retention_days: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _no_dev_secret_in_prod(self) -> "Settings":
        # Anyone who has read this repository knows the dev secret and could
        # sign a token for any company.
        if self.app_env is Environment.PROD and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("JWT_SECRET must be set to your own value when APP_ENV=prod")
        return self

    @property
    def is_dev(self) -> bool:
        return self.app_env is Environment.DEV


@lru_cache
def get_settings() -> Settings:
    return Settings()
