from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import EmbedderKind, Environment, LLMProvider, Settings
from app.main import create_app

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        app_env=Environment.DEV,
        data_dir=tmp_path / "data",
        policy_seed_dir=POLICY_DIR,
        embedder=EmbedderKind.HASHING,
        # Never let a key from the developer's shell reach the test suite.
        llm_provider=LLMProvider.NONE,
        jwt_secret="test-secret-that-is-at-least-32-bytes-long",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client
