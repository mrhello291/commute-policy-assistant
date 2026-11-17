from app.config import Settings
from app.container import Container


def test_startup_loads_newest_version_of_each_company(settings: Settings):
    container = Container(settings)
    container.startup()
    live = {c: container.registry.active(c).version for c in container.registry.active_companies()}
    assert live == {"arcadia": 2, "bluepeak": 1, "corvane": 1}


def test_restart_is_idempotent(settings: Settings):
    Container(settings).startup()
    container = Container(settings)
    container.startup()
    assert container.registry.active("arcadia").version == 2
    assert {c.version for c in container.store.chunks("arcadia", 2)} == {2}
    assert container.store.chunks("arcadia", 1) == []


def test_missing_seed_directory_is_not_fatal(settings: Settings, tmp_path):
    container = Container(settings.model_copy(update={"policy_seed_dir": tmp_path / "nope"}))
    container.startup()
    assert container.registry.active_companies() == {}
