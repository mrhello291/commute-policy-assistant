import shutil

import pytest

from app.answering.service import AnswerStatus
from app.auth import Principal, Role
from app.config import Settings
from app.container import Container
from tests.conftest import MARKDOWN_DIR, POLICY_DIR


@pytest.fixture
def started():
    """Builds and starts containers, and closes them when the test ends."""
    containers = []

    def start(settings: Settings) -> Container:
        container = Container(settings)
        containers.append(container)
        container.startup()
        return container

    yield start
    for container in containers:
        container.close()


def test_startup_loads_newest_version_of_each_company(settings: Settings, started):
    container = started(settings)
    live = {c: container.registry.active(c).version for c in container.registry.active_companies()}
    assert live == {"yamuna": 2, "gomti": 1, "medhavi": 1}


def test_restart_is_idempotent(settings: Settings, started):
    started(settings)
    container = started(settings)
    assert container.registry.active("yamuna").version == 2
    assert {c.version for c in container.store.chunks("yamuna", 2)} == {2}
    assert container.store.chunks("yamuna", 1) == []


def test_missing_seed_directory_is_not_fatal(settings: Settings, tmp_path, started):
    container = started(settings.model_copy(update={"policy_seed_dir": tmp_path / "nope"}))
    assert container.registry.active_companies() == {}


def _former_client_policy() -> str:
    """Yamuna's v1 relabelled as a company that is not in the directory."""
    text = (MARKDOWN_DIR / "yamuna" / "v1.md").read_text()
    return text.replace("company_id: yamuna", "company_id: arcadia").replace(
        "company_name: Yamuna Software Services", "company_name: Arcadia Systems"
    )


def test_former_clients_are_removed_at_startup(settings: Settings, started):
    first = started(settings)
    first.ingestion.ingest(_former_client_policy())
    assert "arcadia" in first.registry.active_companies()

    container = started(settings)
    assert set(container.registry.active_companies()) == {"yamuna", "gomti", "medhavi"}
    assert "arcadia" not in container.registry.company_ids()
    assert "arcadia" not in container.store.company_ids()


def test_questions_naming_a_former_client_are_no_longer_refused_as_other_company(settings: Settings, started):
    first = started(settings)
    first.ingestion.ingest(_former_client_policy())

    container = started(settings)
    result = container.answering.ask(Principal("YAM-1001", "yamuna", Role.EMPLOYEE), "Does Arcadia pay for cabs?")
    assert result.status is not AnswerStatus.OTHER_COMPANY


def test_policy_folders_of_non_clients_are_not_loaded(settings: Settings, tmp_path, started):
    seed = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, seed)
    (seed / "arcadia").mkdir()
    (seed / "arcadia" / "v1.md").write_text(_former_client_policy())
    container = started(settings.model_copy(update={"policy_seed_dir": seed}))
    assert "arcadia" not in container.registry.company_ids()
    assert set(container.registry.active_companies()) == {"yamuna", "gomti", "medhavi"}
