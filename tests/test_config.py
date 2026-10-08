from pathlib import Path

import pytest

from app.core.config import Settings, load_settings
from app.core.exceptions import ConfigurationError


def test_defaults_are_usable_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.statsbomb_source_dir == Path("./data/raw")
    assert settings.api_port == 8000


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/other")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("STATSBOMB_SOURCE_DIR", "/tmp/source")
    monkeypatch.setenv("API_PORT", "9001")

    settings = load_settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/other"
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.statsbomb_source_dir == Path("/tmp/source")
    assert settings.api_port == 9001


@pytest.mark.parametrize(
    "database_url",
    ["", "sqlite:///./local.db", "mysql://u:p@localhost/db", "not-a-url"],
)
def test_non_postgres_database_url_is_rejected(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        load_settings(_env_file=None)


@pytest.mark.parametrize("api_port", ["0", "70000", "abc"])
def test_invalid_api_port_is_rejected(monkeypatch: pytest.MonkeyPatch, api_port: str) -> None:
    monkeypatch.setenv("API_PORT", api_port)

    with pytest.raises(ConfigurationError, match="Invalid application configuration"):
        load_settings(_env_file=None)


def test_invalid_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ConfigurationError):
        load_settings(_env_file=None)
