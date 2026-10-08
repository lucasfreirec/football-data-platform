from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.core.exceptions import ConfigurationError
from app.db.models import Event, Match
from app.ingestion import cli

runner = CliRunner()


@pytest.fixture
def wired_cli(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    """Point the CLI at the in-memory test database."""

    @contextmanager
    def _session_scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(cli, "session_scope", _session_scope)
    monkeypatch.setattr(cli, "check_database_connection", lambda: True)


@pytest.mark.usefixtures("wired_cli")
def test_ingest_succeeds_and_prints_a_summary(raw_source_dir: Path, session: Session) -> None:
    result = runner.invoke(cli.app, ["ingest", "--source-dir", str(raw_source_dir)])

    assert result.exit_code == 0, result.output
    assert "Ingestion summary" in result.output
    assert "matches processed : 2" in result.output
    assert "events imported   : 5" in result.output
    assert "lineups imported  : 6" in result.output
    assert "validation failures: 5" in result.output
    assert session.scalar(select(func.count()).select_from(Match)) == 2


@pytest.mark.usefixtures("wired_cli")
def test_rejected_records_are_listed(raw_source_dir: Path) -> None:
    result = runner.invoke(cli.app, ["ingest", "--source-dir", str(raw_source_dir)])

    assert "Unparseable time" in result.output
    assert "match 7299" in result.output


@pytest.mark.usefixtures("wired_cli")
def test_running_twice_is_idempotent(raw_source_dir: Path, session: Session) -> None:
    for _ in range(2):
        result = runner.invoke(cli.app, ["ingest", "--source-dir", str(raw_source_dir)])
        assert result.exit_code == 0, result.output

    assert session.scalar(select(func.count()).select_from(Match)) == 2
    assert session.scalar(select(func.count()).select_from(Event)) == 5


@pytest.mark.usefixtures("wired_cli")
def test_missing_source_directory_exits_non_zero(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["ingest", "--source-dir", str(tmp_path / "absent")])

    assert result.exit_code == cli.EXIT_SOURCE
    assert "Source directory does not exist" in result.output


@pytest.mark.usefixtures("wired_cli")
def test_unknown_season_exits_non_zero(raw_source_dir: Path) -> None:
    result = runner.invoke(
        cli.app, ["ingest", "--source-dir", str(raw_source_dir), "--season", "1999/2000"]
    )

    assert result.exit_code == cli.EXIT_SOURCE
    assert "1999/2000" in result.output


@pytest.mark.usefixtures("wired_cli")
def test_source_dir_defaults_to_settings(
    monkeypatch: pytest.MonkeyPatch, raw_source_dir: Path, session: Session
) -> None:
    monkeypatch.setenv("STATSBOMB_SOURCE_DIR", str(raw_source_dir))

    result = runner.invoke(cli.app, ["ingest"])

    assert result.exit_code == 0, result.output
    assert session.scalar(select(func.count()).select_from(Match)) == 2


def test_unreachable_database_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, raw_source_dir: Path
) -> None:
    monkeypatch.setattr(cli, "check_database_connection", lambda: False)

    result = runner.invoke(cli.app, ["ingest", "--source-dir", str(raw_source_dir)])

    assert result.exit_code == cli.EXIT_DATABASE
    assert "Cannot reach the database" in result.output


def test_invalid_configuration_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken() -> None:
        raise ConfigurationError("DATABASE_URL must be a PostgreSQL URL")

    monkeypatch.setattr(cli, "get_settings", _broken)

    result = runner.invoke(cli.app, ["ingest"])

    assert result.exit_code == cli.EXIT_CONFIGURATION
    assert "DATABASE_URL" in result.output


def test_database_url_credentials_are_not_printed() -> None:
    masked = cli._safe_url("postgresql+psycopg://football:secret@localhost:5432/football_data")

    assert "secret" not in masked
    assert masked == "postgresql+psycopg://localhost:5432/football_data"


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(cli.app, [])

    assert result.exit_code != 0
    assert "Import StatsBomb Open Data" in result.output
