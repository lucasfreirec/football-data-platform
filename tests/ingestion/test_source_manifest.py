import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import PersistenceError, SourceError
from app.db.models import Competition, Event, Match, SourceManifest
from app.ingestion.loader import SourceLoader
from app.ingestion.persistence import IngestionRepository
from app.ingestion.service import IngestionService


def _service(source: Path, session: Session, season: str = "2017/2018") -> IngestionService:
    return IngestionService(SourceLoader(source), session, season_name=season)


def _manifests(session: Session) -> list[SourceManifest]:
    session.expire_all()
    return list(session.scalars(select(SourceManifest).order_by(SourceManifest.id)))


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def editable_source(tmp_path: Path, v2_source_dir: Path) -> Path:
    target = tmp_path / "raw"
    shutil.copytree(v2_source_dir, target)
    return target


class TestSuccessfulImport:
    def test_clean_season_is_marked_success(self, v2_source_dir: Path, session: Session) -> None:
        summary = _service(v2_source_dir, session, "2016/2017").run()
        session.commit()
        (manifest,) = _manifests(session)

        assert summary.import_status == "success"
        assert summary.manifest_id == manifest.id
        assert manifest.import_status == "success"
        assert manifest.match_count_expected == 1
        assert manifest.event_file_count_found == 1
        assert manifest.lineup_file_count_found == 1

    def test_manifest_points_at_the_resolved_competition_and_season(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        _service(v2_source_dir, session, "2016/2017").run()
        session.commit()
        (manifest,) = _manifests(session)

        assert manifest.competition.statsbomb_id == 16
        assert manifest.season.statsbomb_id == 2
        assert manifest.season.name == "2016/2017"

    def test_manifest_records_the_source_path_and_time(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        _service(v2_source_dir, session, "2016/2017").run()
        session.commit()
        (manifest,) = _manifests(session)

        assert manifest.source_path == str(v2_source_dir.resolve())
        assert manifest.imported_at is not None

    def test_source_version_is_taken_from_the_revision_file(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "SOURCE_REVISION").write_text("abc123def456\n")

        _service(editable_source, session, "2016/2017").run()
        session.commit()

        assert _manifests(session)[0].source_version == "abc123def456"

    def test_source_version_is_null_when_unknown(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        _service(v2_source_dir, session, "2016/2017").run()
        session.commit()

        assert _manifests(session)[0].source_version is None

    def test_every_run_adds_its_own_manifest(self, v2_source_dir: Path, session: Session) -> None:
        for _ in range(2):
            _service(v2_source_dir, session, "2016/2017").run()
            session.commit()

        assert len(_manifests(session)) == 2


class TestPartialImport:
    def test_rejected_records_make_the_import_partial(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        summary = _service(v2_source_dir, session).run()
        session.commit()
        (manifest,) = _manifests(session)

        assert summary.import_status == "partial"
        assert manifest.import_status == "partial"
        assert manifest.match_count_expected == 2
        assert manifest.summary["validation_failures"] == 4
        assert manifest.summary["passes_skipped"] == 2
        assert manifest.summary["shots_skipped"] == 2

    def test_missing_event_file_makes_the_import_partial(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "events" / "9003.json").unlink()

        summary = _service(editable_source, session, "2016/2017").run()
        session.commit()
        (manifest,) = _manifests(session)

        assert summary.import_status == "partial"
        assert manifest.import_status == "partial"
        assert manifest.event_file_count_found == 0
        assert manifest.lineup_file_count_found == 1
        assert manifest.summary["event_files_missing"] == 1
        assert manifest.summary["missing_files"] == [
            f"events file for match 9003: {editable_source / 'events' / '9003.json'}"
        ]

    def test_missing_lineup_file_makes_the_import_partial(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "lineups" / "9003.json").unlink()

        summary = _service(editable_source, session, "2016/2017").run()
        session.commit()

        assert summary.import_status == "partial"
        assert _manifests(session)[0].lineup_file_count_found == 0

    def test_a_skipped_match_makes_the_import_partial(
        self, editable_source: Path, session: Session
    ) -> None:
        path = editable_source / "matches" / "16" / "2.json"
        matches = json.loads(path.read_text())
        matches[0]["match_date"] = "not-a-date"
        path.write_text(json.dumps(matches))

        summary = _service(editable_source, session, "2016/2017").run()
        session.commit()

        assert summary.import_status == "partial"
        assert summary.matches_skipped == 1
        assert _manifests(session)[0].match_count_expected == 1

    def test_summary_stays_small_however_many_records_are_rejected(
        self, editable_source: Path, session: Session
    ) -> None:
        path = editable_source / "events" / "9003.json"
        events = json.loads(path.read_text())
        broken = [
            {"id": f"bad-{n}", "index": 100 + n, "type": {"name": "Pass"}, "pass": "oops"}
            for n in range(80)
        ]
        path.write_text(json.dumps(events + broken))

        _service(editable_source, session, "2016/2017").run()
        session.commit()
        manifest_summary = _manifests(session)[0].summary

        assert manifest_summary["validation_failures"] == 80
        assert len(manifest_summary["rejected"]) == 50

    def test_manifest_summary_holds_no_raw_payloads(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        _service(v2_source_dir, session).run()
        session.commit()
        text = json.dumps(_manifests(session)[0].summary)

        assert "statsbomb_xg" not in text.replace("shot.statsbomb_xg", "")
        assert "freeze_frame" not in text
        assert "location" not in text.replace("end_location", "")


class TestFailedImport:
    def test_invalid_matches_file_leaves_a_failed_manifest(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "matches" / "16" / "1.json").write_text('{"not": "an array"}')

        with pytest.raises(SourceError, match="Expected a JSON array"):
            _service(editable_source, session).run()
        session.rollback()  # what the CLI's session scope does on the way out

        (manifest,) = _manifests(session)
        assert manifest.import_status == "failed"
        assert "Expected a JSON array" in manifest.summary["failure"]
        assert manifest.competition.statsbomb_id == 16

    def test_missing_matches_file_leaves_a_failed_manifest(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "matches" / "16" / "1.json").unlink()

        with pytest.raises(SourceError, match="Source file not found"):
            _service(editable_source, session).run()
        session.rollback()

        (manifest,) = _manifests(session)
        assert manifest.import_status == "failed"
        assert manifest.match_count_expected == 0

    def test_failure_partway_through_rolls_back_the_whole_run(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "events" / "9002.json").write_text("{broken json")

        service = _service(editable_source, session)
        with pytest.raises(SourceError, match="Invalid JSON"):
            service.run()
        session.rollback()

        assert _count(session, Match) == 0
        assert _count(session, Event) == 0
        (manifest,) = _manifests(session)
        assert manifest.import_status == "failed"
        assert manifest.match_count_expected == 2
        assert manifest.event_file_count_found == 0

    def test_failed_run_does_not_report_the_rolled_back_counts(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "events" / "9002.json").write_text("{broken json")

        service = _service(editable_source, session)
        with pytest.raises(SourceError):
            service.run()

        summary = service.summary
        assert summary is not None
        assert summary.import_status == "failed"
        assert summary.matches_processed == 0
        assert summary.events_imported == 0
        assert summary.matches_listed == 2
        assert summary.manifest_id is not None
        assert "failure" in "\n".join(summary.as_lines())

    def test_database_failure_is_a_persistence_error_with_a_failed_manifest(
        self, v2_source_dir: Path, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_args: object, **_kwargs: object) -> None:
            raise OperationalError("INSERT INTO events ...", {}, Exception("connection lost"))

        monkeypatch.setattr(IngestionRepository, "upsert_events", explode)

        with pytest.raises(PersistenceError, match="Database write failed"):
            _service(v2_source_dir, session).run()
        session.rollback()

        (manifest,) = _manifests(session)
        assert manifest.import_status == "failed"
        assert "connection lost" in manifest.summary["failure"]
        assert "INSERT INTO" not in manifest.summary["failure"]
        assert _count(session, Match) == 0

    def test_a_failed_run_never_claims_success(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "events" / "9001.json").write_text("[1, 2]")

        with pytest.raises(SourceError):
            _service(editable_source, session).run()
        session.rollback()

        assert {m.import_status for m in _manifests(session)} == {"failed"}

    def test_a_later_successful_run_after_a_failure_imports_normally(
        self, editable_source: Path, session: Session
    ) -> None:
        events_file = editable_source / "events" / "9002.json"
        good = events_file.read_text()
        events_file.write_text("{broken json")
        with pytest.raises(SourceError):
            _service(editable_source, session).run()
        session.rollback()

        events_file.write_text(good)
        summary = _service(editable_source, session).run()
        session.commit()

        assert summary.matches_processed == 2
        assert [m.import_status for m in _manifests(session)] == ["failed", "partial"]


class TestNoManifestBeforeResolution:
    def test_unknown_season_creates_no_manifest(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        with pytest.raises(SourceError, match="1999/2000"):
            _service(v2_source_dir, session, "1999/2000").run()
        session.rollback()

        assert _manifests(session) == []
        assert _count(session, Competition) == 0

    def test_missing_source_directory_creates_no_manifest(
        self, tmp_path: Path, session: Session
    ) -> None:
        with pytest.raises(SourceError, match="does not exist"):
            _service(tmp_path / "absent", session).run()

        assert _manifests(session) == []

    def test_summary_is_unavailable_when_the_season_never_resolved(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        service = _service(v2_source_dir, session, "1999/2000")
        with pytest.raises(SourceError):
            service.run()

        assert service.summary is None
