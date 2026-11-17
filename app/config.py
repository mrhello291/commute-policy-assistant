"""Runtime configuration.

Every tunable lives here and is read from environment variables (or a local
`.env` file). Secrets such as API keys are only ever supplied this way, so
nothing sensitive needs to be written into the repository.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"


class EmbedderKind(StrEnum):
    MINILM = "minilm"
    HASHING = "hashing"


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

    # LLM provider. Keys come from the environment only.
    llm_provider: LLMProvider = LLMProvider.AUTO
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"

    # JWT (the mock identity provider and the API share this secret).
    jwt_secret: str = Field(default="dev-only-secret-change-me-before-deploying", min_length=32)
    jwt_issuer: str = "moveinsync-mock-idp"
    jwt_audience: str = "commute-policy-assistant"
    jwt_ttl_minutes: int = 480

    @property
    def is_dev(self) -> bool:
        return self.app_env is Environment.DEV


@lru_cache
def get_settings() -> Settings:
    return Settings()
