import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SourceManifest
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_statsbomb_data.sh"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("bash", "curl", "python3")),
    reason="the download script needs bash, curl, and python3",
)


@pytest.fixture
def upstream(tmp_path: Path, v2_source_dir: Path) -> Path:
    """A local stand-in for the StatsBomb data directory."""
    target = tmp_path / "upstream"
    shutil.copytree(v2_source_dir, target)
    return target


def _run(
    upstream: Path, *args: str, revision: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("STATSBOMB_")}
    env["STATSBOMB_BASE_URL"] = upstream.as_uri()
    if revision is not None:
        env["STATSBOMB_REVISION"] = revision
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def _files(target: Path) -> set[str]:
    return {
        str(path.relative_to(target))
        for path in target.rglob("*")
        if path.is_file() and path.suffix == ".json"
    }


class TestDownload:
    def test_downloads_the_complete_season(self, upstream: Path, tmp_path: Path) -> None:
        target = tmp_path / "raw"

        result = _run(upstream, "Champions League", "2017/2018", str(target))

        assert result.returncode == 0, result.stderr
        assert _files(target) == {
            "competitions.json",
            "matches/16/1.json",
            "events/9001.json",
            "events/9002.json",
            "lineups/9001.json",
            "lineups/9002.json",
        }
        assert "Matches in the source list : 2" in result.stdout
        assert "Files downloaded           : 6" in result.stdout
        assert "Files skipped (up to date) : 0" in result.stdout
        assert "Files unavailable          : 0" in result.stdout

    def test_downloaded_files_match_the_source_exactly(
        self, upstream: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "raw"
        _run(upstream, "Champions League", "2017/2018", str(target))

        for relative in _files(target):
            assert (target / relative).read_bytes() == (upstream / relative).read_bytes()

    def test_season_ids_are_resolved_from_names(self, upstream: Path, tmp_path: Path) -> None:
        result = _run(upstream, "Champions League", "2016/2017", str(tmp_path / "raw"))

        assert "competition_id=16 season_id=2" in result.stdout
        assert (tmp_path / "raw" / "matches" / "16" / "2.json").is_file()

    def test_no_partial_files_are_left_behind(self, upstream: Path, tmp_path: Path) -> None:
        target = tmp_path / "raw"
        _run(upstream, "Champions League", "2017/2018", str(target))

        assert list(target.rglob("*.part")) == []


class TestArgumentParsing:
    @pytest.mark.parametrize(
        "args",
        [
            ["Champions", "League", "2017/2018"],
            ["Champions League", "2017/2018"],
            ["champions", "league", "2017/2018"],
            ["CHAMPIONS LEAGUE", "2017/2018"],
        ],
    )
    def test_competition_name_may_be_quoted_or_not_and_any_case(
        self, upstream: Path, tmp_path: Path, args: list[str]
    ) -> None:
        target = tmp_path / "raw"

        result = _run(upstream, *args, str(target))

        assert result.returncode == 0, result.stderr
        assert "Matches in the source list : 2" in result.stdout

    def test_target_directory_defaults_to_data_raw(self, upstream: Path, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("STATSBOMB_")}
        env["STATSBOMB_BASE_URL"] = upstream.as_uri()

        result = subprocess.run(
            ["bash", str(SCRIPT), "Champions League", "2017/2018"],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "data" / "raw" / "competitions.json").is_file()

    def test_a_missing_season_argument_is_an_error(self, upstream: Path, tmp_path: Path) -> None:
        result = _run(upstream, "Champions", "League", str(tmp_path / "raw"))

        assert result.returncode != 0
        assert "no season" in result.stderr


class TestRevisionPinning:
    def test_revision_is_recorded(self, upstream: Path, tmp_path: Path) -> None:
        target = tmp_path / "raw"

        _run(upstream, "Champions League", "2017/2018", str(target), revision="abc123")

        assert (target / "SOURCE_REVISION").read_text().strip() == "abc123"

    def test_rerun_at_the_same_revision_skips_every_file(
        self, upstream: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "raw"
        _run(upstream, "Champions League", "2017/2018", str(target), revision="abc123")

        result = _run(upstream, "Champions League", "2017/2018", str(target), revision="abc123")

        assert result.returncode == 0, result.stderr
        assert "Files downloaded           : 0" in result.stdout
        assert "Files skipped (up to date) : 6" in result.stdout

    def test_a_new_revision_downloads_everything_again(
        self, upstream: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "raw"
        _run(upstream, "Champions League", "2017/2018", str(target), revision="abc123")

        result = _run(upstream, "Champions League", "2017/2018", str(target), revision="def456")

        assert "Files downloaded           : 6" in result.stdout
        assert "Files skipped (up to date) : 0" in result.stdout
        assert (target / "SOURCE_REVISION").read_text().strip() == "def456"

    def test_unpinned_downloads_are_never_skipped(self, upstream: Path, tmp_path: Path) -> None:
        target = tmp_path / "raw"
        _run(upstream, "Champions League", "2017/2018", str(target))

        result = _run(upstream, "Champions League", "2017/2018", str(target))

        assert "Files downloaded           : 6" in result.stdout
        assert not (target / "SOURCE_REVISION").exists()


class TestAvailabilityReporting:
    def test_unavailable_event_and_lineup_files_are_reported_not_fatal(
        self, upstream: Path, tmp_path: Path
    ) -> None:
        (upstream / "events" / "9002.json").unlink()
        (upstream / "lineups" / "9001.json").unlink()

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode == 0, result.stderr
        assert "Matches in the source list : 2" in result.stdout
        assert "Files downloaded           : 4" in result.stdout
        assert "Files unavailable          : 2" in result.stdout
        assert "events/9002.json" in result.stdout
        assert "lineups/9001.json" in result.stdout

    def test_a_match_with_no_files_at_all_is_still_counted(
        self, upstream: Path, tmp_path: Path
    ) -> None:
        shutil.rmtree(upstream / "events")
        shutil.rmtree(upstream / "lineups")

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode == 0, result.stderr
        assert "Matches in the source list : 2" in result.stdout
        assert "Files unavailable          : 4" in result.stdout


class TestFatalFailures:
    def test_unknown_season_fails(self, upstream: Path, tmp_path: Path) -> None:
        result = _run(upstream, "Champions League", "1999/2000", str(tmp_path / "raw"))

        assert result.returncode != 0
        assert "no 'Champions League' '1999/2000' entry" in result.stderr

    def test_unknown_competition_fails(self, upstream: Path, tmp_path: Path) -> None:
        result = _run(upstream, "Premier League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode != 0

    def test_missing_competitions_file_fails(self, upstream: Path, tmp_path: Path) -> None:
        (upstream / "competitions.json").unlink()

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode != 0
        assert "competitions.json" in result.stderr

    def test_unparseable_competitions_file_fails(self, upstream: Path, tmp_path: Path) -> None:
        (upstream / "competitions.json").write_text("{broken json")

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode != 0
        assert "not a valid JSON array" in result.stderr

    def test_competitions_file_that_is_not_a_list_fails(
        self, upstream: Path, tmp_path: Path
    ) -> None:
        (upstream / "competitions.json").write_text(json.dumps({"competition_id": 16}))

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode != 0

    def test_missing_match_list_fails(self, upstream: Path, tmp_path: Path) -> None:
        (upstream / "matches" / "16" / "1.json").unlink()

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode != 0
        assert "matches/16/1.json" in result.stderr

    def test_unparseable_match_list_fails(self, upstream: Path, tmp_path: Path) -> None:
        (upstream / "matches" / "16" / "1.json").write_text("not json")

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode != 0
        assert not (tmp_path / "raw" / "matches" / "16" / "1.json").exists()

    def test_match_list_without_match_ids_fails(self, upstream: Path, tmp_path: Path) -> None:
        (upstream / "matches" / "16" / "1.json").write_text(json.dumps([{"no_id": True}]))

        result = _run(upstream, "Champions League", "2017/2018", str(tmp_path / "raw"))

        assert result.returncode != 0
        assert "could not be parsed" in result.stderr


class TestRoundTrip:
    def test_downloaded_data_imports_and_records_the_revision(
        self, upstream: Path, tmp_path: Path, session: Session
    ) -> None:
        target = tmp_path / "raw"
        assert (
            _run(
                upstream, "Champions League", "2017/2018", str(target), revision="abc123"
            ).returncode
            == 0
        )

        summary = IngestionService(SourceLoader(target), session).run()
        session.commit()
        manifest = session.scalar(select(SourceManifest))

        assert summary.matches_listed == 2
        assert summary.event_files_found == 2
        assert manifest is not None
        assert manifest.source_version == "abc123"
        assert manifest.match_count_expected == 2
