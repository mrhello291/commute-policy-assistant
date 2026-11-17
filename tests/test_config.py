from app.config import Environment, Settings


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "a-much-longer-secret-value")
    settings = Settings(_env_file=None)
    assert settings.app_env is Environment.PROD
    assert not settings.is_dev


def test_defaults_are_dev(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None).is_dev
