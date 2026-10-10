import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.db.models import SourceManifest
from app.ingestion import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def _wired_cli(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    @contextmanager
    def _session_scope() -> Iterator[Session]:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        session.commit()

    monkeypatch.setattr(cli, "session_scope", _session_scope)
    monkeypatch.setattr(cli, "check_database_connection", lambda: True)


def _ingest(source: Path, *extra: str) -> tuple[int, str]:
    result = runner.invoke(cli.app, ["ingest", "--source-dir", str(source), *extra])
    return result.exit_code, result.output


def test_summary_shows_the_completeness_figures(v2_source_dir: Path) -> None:
    code, output = _ingest(v2_source_dir)

    assert code == 0, output
    for expected in (
        "matches listed    : 2",
        "matches processed : 2",
        "event files       : 2 found / 0 missing",
        "lineup files      : 2 found / 0 missing",
        "passes imported   : 8",
        "passes skipped    : 2",
        "shots imported    : 6",
        "shots skipped     : 2",
        "import status     : partial",
        "source manifest id: 1",
    ):
        assert expected in output


def test_malformed_details_are_listed_with_their_reason(v2_source_dir: Path) -> None:
    _, output = _ingest(v2_source_dir)

    assert "rejected records:" in output
    assert "pass.end_location" in output
    assert "shot.statsbomb_xg" in output
    assert "match 9002" in output


def test_a_clean_season_reports_success(v2_source_dir: Path) -> None:
    code, output = _ingest(v2_source_dir, "--season", "2016/2017")

    assert code == 0
    assert "import status     : success" in output
    assert "unavailable files:" not in output
    assert "rejected records:" not in output


def test_unavailable_files_are_not_mixed_with_rejected_records(
    tmp_path: Path, v2_source_dir: Path
) -> None:
    source = tmp_path / "raw"
    shutil.copytree(v2_source_dir, source)
    (source / "events" / "9003.json").unlink()

    code, output = _ingest(source, "--season", "2016/2017")

    assert code == 0
    assert "event files       : 0 found / 1 missing" in output
    assert "unavailable files:" in output
    assert "rejected records:" not in output
    assert "validation failures: 0" in output
    assert "import status     : partial" in output


def test_the_manifest_id_printed_matches_the_stored_row(
    v2_source_dir: Path, session: Session
) -> None:
    _, output = _ingest(v2_source_dir)
    manifest = session.scalar(select(SourceManifest))

    assert manifest is not None
    assert f"source manifest id: {manifest.id}" in output


def test_a_failed_import_exits_non_zero_and_still_prints_its_manifest(
    tmp_path: Path, v2_source_dir: Path, session: Session
) -> None:
    source = tmp_path / "raw"
    shutil.copytree(v2_source_dir, source)
    (source / "events" / "9002.json").write_text("{broken json")

    code, output = _ingest(source)

    assert code == cli.EXIT_SOURCE
    assert "import status     : failed" in output
    assert "Invalid JSON" in output
    assert "source manifest id: 1" in output
    stored = session.scalar(select(SourceManifest))
    assert stored is not None and stored.import_status == "failed"


def test_an_unresolved_season_prints_no_summary(v2_source_dir: Path) -> None:
    code, output = _ingest(v2_source_dir, "--season", "1999/2000")

    assert code == cli.EXIT_SOURCE
    assert "Ingestion summary" not in output
    assert "1999/2000" in output
