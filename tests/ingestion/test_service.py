import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import SourceError
from app.db.models import Competition, Event, Lineup, Match, Player, Season, Team
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService, IngestionSummary


@pytest.fixture
def service(raw_source_dir: Path, session: Session) -> IngestionService:
    return IngestionService(SourceLoader(raw_source_dir), session)


def _count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


class TestHappyPath:
    def test_imports_the_configured_season(
        self, service: IngestionService, session: Session
    ) -> None:
        summary = service.run()
        session.commit()

        assert summary.matches_processed == 2
        assert summary.matches_skipped == 0
        assert _count(session, Competition) == 1
        assert _count(session, Season) == 1
        assert _count(session, Match) == 2
        assert _count(session, Team) == 3

    def test_imports_events_and_lineups(self, service: IngestionService, session: Session) -> None:
        summary = service.run()
        session.commit()

        # 3 valid events for 7298, 2 of 5 valid for 7299
        assert summary.events_imported == 5
        assert summary.events_skipped == 3
        assert _count(session, Event) == 5

        # 5 entries for 7298, 1 of 2 valid for 7299
        assert summary.lineups_imported == 6
        assert summary.lineups_skipped == 2
        assert _count(session, Lineup) == 6

    def test_event_fields_are_persisted(self, service: IngestionService, session: Session) -> None:
        service.run()
        session.commit()

        event = session.scalar(select(Event).where(Event.type_name == "Shot"))
        assert event is not None
        assert event.outcome_name == "Goal"
        assert event.location_x == 112.0
        assert event.location_y == 38.0
        assert event.player is not None and event.player.name == "Karim Benzema"
        assert event.raw_data is not None and event.raw_data["detail_type"] == "shot"

    def test_lineup_fields_are_persisted(self, service: IngestionService, session: Session) -> None:
        service.run()
        session.commit()

        keeper = session.scalar(select(Lineup).join(Player).where(Player.statsbomb_id == 3509))
        assert keeper is not None
        assert keeper.jersey_number == 1
        assert keeper.position_name == "Goalkeeper"
        assert keeper.is_starter is True

    def test_players_are_shared_across_events_and_lineups(
        self, service: IngestionService, session: Session
    ) -> None:
        service.run()
        session.commit()

        benzema = session.scalars(select(Player).where(Player.statsbomb_id == 3604)).all()
        assert len(benzema) == 1


class TestInvalidRecordReporting:
    def test_malformed_location_is_skipped_and_reported(
        self, service: IngestionService, session: Session
    ) -> None:
        summary = service.run()
        session.commit()

        reasons = [record.reason for record in summary.rejected]
        assert any("two-number array" in reason for reason in reasons)

    def test_malformed_timestamp_is_skipped_and_reported(
        self, service: IngestionService, session: Session
    ) -> None:
        summary = service.run()

        assert any("Unparseable time" in record.reason for record in summary.rejected)

    def test_duplicate_event_index_is_skipped(
        self, service: IngestionService, session: Session
    ) -> None:
        summary = service.run()

        assert any("Duplicate event id or index" in record.reason for record in summary.rejected)

    def test_invalid_lineup_entry_does_not_discard_the_whole_team(
        self, service: IngestionService, session: Session
    ) -> None:
        service.run()
        session.commit()

        match = session.scalar(select(Match).where(Match.statsbomb_id == 7299))
        assert match is not None
        lineups = session.scalars(select(Lineup).where(Lineup.match_id == match.id)).all()
        assert len(lineups) == 1
        assert lineups[0].player.statsbomb_id == 3604

    def test_invalid_team_block_is_reported_with_context(
        self, service: IngestionService, session: Session
    ) -> None:
        summary = service.run()

        rejection = next(
            record for record in summary.rejected if "Expected an array" in record.reason
        )
        assert rejection.match_external_id == 7299
        assert rejection.record_external_id == "217"
        assert "7299.json" in rejection.source_file

    def test_rejected_records_render_with_context(
        self, service: IngestionService, session: Session
    ) -> None:
        summary = service.run()

        rendered = str(summary.rejected[0])
        assert "match 7299" in rendered


class TestIdempotency:
    def test_running_twice_creates_no_duplicates(
        self, raw_source_dir: Path, session: Session
    ) -> None:
        counts = []
        for _ in range(2):
            IngestionService(SourceLoader(raw_source_dir), session).run()
            session.commit()
            counts.append(
                tuple(
                    _count(session, model)
                    for model in (Competition, Season, Team, Player, Match, Event, Lineup)
                )
            )

        assert counts[0] == counts[1]
        assert counts[0][4] == 2  # matches

    def test_rerun_updates_instead_of_inserting(
        self, raw_source_dir: Path, session: Session
    ) -> None:
        IngestionService(SourceLoader(raw_source_dir), session).run()
        session.commit()

        match = session.scalar(select(Match).where(Match.statsbomb_id == 7298))
        assert match is not None
        match.home_score = 0
        session.commit()

        IngestionService(SourceLoader(raw_source_dir), session).run()
        session.commit()
        session.refresh(match)

        assert match.home_score == 3
        assert _count(session, Match) == 2


class TestTargetSelection:
    def test_unknown_season_fails_the_command(self, raw_source_dir: Path, session: Session) -> None:
        service = IngestionService(SourceLoader(raw_source_dir), session, season_name="1999/2000")

        with pytest.raises(SourceError, match="Champions League 1999/2000"):
            service.run()

    def test_target_match_is_case_insensitive(self, raw_source_dir: Path, session: Session) -> None:
        service = IngestionService(
            SourceLoader(raw_source_dir), session, competition_name="champions league"
        )

        assert service.run().matches_processed == 2

    def test_missing_source_directory_fails_the_command(
        self, tmp_path: Path, session: Session
    ) -> None:
        service = IngestionService(SourceLoader(tmp_path / "absent"), session)

        with pytest.raises(SourceError, match="Source directory does not exist"):
            service.run()


class TestMissingAndMalformedFiles:
    def test_missing_event_and_lineup_files_are_reported(
        self, tmp_path: Path, raw_source_dir: Path, session: Session
    ) -> None:
        shutil.copytree(raw_source_dir, tmp_path / "raw")
        shutil.rmtree(tmp_path / "raw" / "events")
        shutil.rmtree(tmp_path / "raw" / "lineups")

        summary = IngestionService(SourceLoader(tmp_path / "raw"), session).run()
        session.commit()

        assert summary.matches_processed == 2
        assert summary.events_imported == 0
        assert summary.lineups_imported == 0
        reasons = [record.reason for record in summary.rejected]
        assert reasons.count("Event file not found") == 2
        assert reasons.count("Lineup file not found") == 2

    def test_malformed_match_is_skipped_and_the_rest_imported(
        self, tmp_path: Path, raw_source_dir: Path, session: Session
    ) -> None:
        shutil.copytree(raw_source_dir, tmp_path / "raw")
        matches_file = tmp_path / "raw" / "matches" / "16" / "1.json"
        records = json.loads(matches_file.read_text())
        records[0]["match_date"] = "26-05-2018"
        matches_file.write_text(json.dumps(records))

        summary = IngestionService(SourceLoader(tmp_path / "raw"), session).run()
        session.commit()

        assert summary.matches_skipped == 1
        assert summary.matches_processed == 1
        assert _count(session, Match) == 1
        rejection = next(r for r in summary.rejected if "match_date" in r.reason)
        assert rejection.match_external_id == 7298

    def test_malformed_competitions_entry_is_skipped(
        self, tmp_path: Path, raw_source_dir: Path, session: Session
    ) -> None:
        shutil.copytree(raw_source_dir, tmp_path / "raw")
        competitions_file = tmp_path / "raw" / "competitions.json"
        records = json.loads(competitions_file.read_text())
        competitions_file.write_text(json.dumps([{"competition_id": None}, *records]))

        summary = IngestionService(SourceLoader(tmp_path / "raw"), session).run()

        assert summary.matches_processed == 2

    def test_invalid_matches_file_structure_fails_the_command(
        self, tmp_path: Path, raw_source_dir: Path, session: Session
    ) -> None:
        shutil.copytree(raw_source_dir, tmp_path / "raw")
        (tmp_path / "raw" / "matches" / "16" / "1.json").write_text('{"not": "an array"}')

        with pytest.raises(SourceError, match="Expected a JSON array"):
            IngestionService(SourceLoader(tmp_path / "raw"), session).run()


class TestSummary:
    def test_summary_lines_include_every_counter(self) -> None:
        summary = IngestionSummary(matches_processed=2, events_imported=5)
        text = "\n".join(summary.as_lines())

        assert "matches processed : 2" in text
        assert "events imported   : 5" in text
        assert "validation failures: 0" in text
