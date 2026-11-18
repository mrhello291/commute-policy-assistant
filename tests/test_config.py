import pytest
from pydantic import ValidationError

from app.config import DEV_JWT_SECRET, Environment, Settings


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "a-much-longer-secret-value-for-production-use")
    settings = Settings(_env_file=None)
    assert settings.app_env is Environment.PROD
    assert not settings.is_dev


def test_defaults_are_dev(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None).is_dev


def test_prod_refuses_the_dev_jwt_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None)
    monkeypatch.setenv("JWT_SECRET", DEV_JWT_SECRET)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None)


def test_dev_may_use_the_dev_secret(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert Settings(_env_file=None).jwt_secret == DEV_JWT_SECRET
