from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import EmbedderKind, Environment, LLMProvider, Settings
from app.container import Container
from app.main import create_app

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
POLICY_DIR = DATA_DIR / "policies"
# The Markdown the sample PDFs were made from, for tests that need to edit a policy's text.
MARKDOWN_DIR = DATA_DIR / "archive" / "markdown-policies"


def make_settings(data_dir: Path) -> Settings:
    """Offline, deterministic settings: hashing embedder and no LLM provider."""
    return Settings(
        _env_file=None,
        app_env=Environment.DEV,
        data_dir=data_dir,
        policy_seed_dir=POLICY_DIR,
        employee_directory=DATA_DIR / "employees.json",
        embedder=EmbedderKind.HASHING,
        # Never let a key from the developer's shell reach the test suite.
        llm_provider=LLMProvider.NONE,
        jwt_secret="test-secret-that-is-at-least-32-bytes-long",
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path / "data")


@pytest.fixture(scope="module")
def seeded_container(tmp_path_factory) -> Container:
    """A container with the sample policies loaded, shared within a test module."""
    container = Container(make_settings(tmp_path_factory.mktemp("seeded")))
    container.startup()
    return container


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client
