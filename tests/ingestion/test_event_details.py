import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Event, Match, PassDetail, Player, ShotDetail
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService, IngestionSummary

FIRST_PASS = "90010000-0000-4000-8000-000000000003"
KEY_PASS = "90010000-0000-4000-8000-000000000005"
GOAL_SHOT = "90010000-0000-4000-8000-000000000006"


def _ingest(source_dir: Path, session: Session, season: str = "2017/2018") -> IngestionSummary:
    summary = IngestionService(SourceLoader(source_dir), session, season_name=season).run()
    session.commit()
    return summary


def _count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _event(session: Session, statsbomb_id: str) -> Event:
    event = session.scalar(select(Event).where(Event.statsbomb_id == statsbomb_id))
    assert event is not None
    return event


@pytest.fixture
def editable_source(tmp_path: Path, v2_source_dir: Path) -> Path:
    target = tmp_path / "raw"
    shutil.copytree(v2_source_dir, target)
    return target


def _edit_events(source: Path, match_id: int, edit: Any) -> None:
    path = source / "events" / f"{match_id}.json"
    events = json.loads(path.read_text())
    edit(events)
    path.write_text(json.dumps(events))


def _by_id(events: list[dict[str, Any]], statsbomb_id: str) -> dict[str, Any]:
    return next(event for event in events if event["id"] == statsbomb_id)


class TestDetailPersistence:
    def test_one_detail_row_per_pass_and_shot(self, v2_source_dir: Path, session: Session) -> None:
        summary = _ingest(v2_source_dir, session)

        assert summary.passes_imported == 8
        assert summary.shots_imported == 6
        assert _count(session, PassDetail) == 8
        assert _count(session, ShotDetail) == 6

    def test_detail_rows_exist_only_for_matching_event_types(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        _ingest(v2_source_dir, session)

        rows = session.execute(
            select(Event.type_name, PassDetail.event_id, ShotDetail.event_id)
            .outerjoin(PassDetail, PassDetail.event_id == Event.id)
            .outerjoin(ShotDetail, ShotDetail.event_id == Event.id)
        ).all()
        for type_name, pass_id, shot_id in rows:
            assert (pass_id is not None) == (type_name == "Pass")
            assert (shot_id is not None) == (type_name == "Shot")

    def test_pass_fields_are_persisted(self, v2_source_dir: Path, session: Session) -> None:
        _ingest(v2_source_dir, session)
        detail = session.get(PassDetail, _event(session, FIRST_PASS).id)

        assert detail is not None
        assert detail.recipient is not None
        assert detail.recipient.statsbomb_id == 1002
        assert detail.length == 12.5
        assert detail.angle == 0.3
        assert detail.height_name == "Ground Pass"
        assert (detail.end_location_x, detail.end_location_y) == (60.0, 40.0)
        assert detail.body_part_name == "Right Foot"
        assert detail.type_name == "Recovery"
        assert detail.outcome_name is None
        assert detail.is_cross is None

    def test_shot_fields_are_persisted(self, v2_source_dir: Path, session: Session) -> None:
        _ingest(v2_source_dir, session)
        detail = session.get(ShotDetail, _event(session, GOAL_SHOT).id)

        assert detail is not None
        assert detail.statsbomb_xg == 0.25
        assert detail.outcome_name == "Goal"
        assert (detail.end_location_x, detail.end_location_y) == (120.0, 40.0)
        assert detail.first_time is True
        assert detail.type_name == "Open Play"

    def test_recipient_is_created_as_a_player(self, v2_source_dir: Path, session: Session) -> None:
        _ingest(v2_source_dir, session)

        assert session.scalar(select(Player).where(Player.statsbomb_id == 1002)) is not None
        assert _count(session, Player) == 6  # five by lineups/events plus Gus

    def test_key_pass_is_linked_to_its_event(self, v2_source_dir: Path, session: Session) -> None:
        _ingest(v2_source_dir, session)
        detail = session.get(ShotDetail, _event(session, GOAL_SHOT).id)

        assert detail is not None
        assert detail.key_pass_event_id == _event(session, KEY_PASS).id

    def test_key_pass_defined_after_the_shot_still_resolves(
        self, editable_source: Path, session: Session
    ) -> None:
        def move_pass_after_shot(events: list[dict[str, Any]]) -> None:
            key_pass = _by_id(events, KEY_PASS)
            events.remove(key_pass)
            events.append(key_pass)
            key_pass["index"] = 99

        _edit_events(editable_source, 9001, move_pass_after_shot)
        _ingest(editable_source, session)
        detail = session.get(ShotDetail, _event(session, GOAL_SHOT).id)

        assert detail is not None
        assert detail.key_pass_event_id == _event(session, KEY_PASS).id

    def test_unresolvable_key_pass_keeps_the_shot_and_warns(
        self, editable_source: Path, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        def dangle(events: list[dict[str, Any]]) -> None:
            _by_id(events, GOAL_SHOT)["shot"]["key_pass_id"] = "no-such-event"

        _edit_events(editable_source, 9001, dangle)
        with caplog.at_level(logging.WARNING, logger="app.ingestion.persistence"):
            _ingest(editable_source, session)
        detail = session.get(ShotDetail, _event(session, GOAL_SHOT).id)

        assert detail is not None
        assert detail.key_pass_event_id is None
        assert detail.statsbomb_xg == 0.25
        assert "no-such-event" in caplog.text


class TestIdempotency:
    def test_reimport_creates_no_duplicates(self, v2_source_dir: Path, session: Session) -> None:
        counts = []
        for _ in range(3):
            _ingest(v2_source_dir, session)
            counts.append(
                (
                    _count(session, Event),
                    _count(session, PassDetail),
                    _count(session, ShotDetail),
                    _count(session, Match),
                )
            )

        assert counts[0] == counts[1] == counts[2]
        assert counts[0][1:3] == (8, 6)

    def test_reimport_updates_a_changed_detail(
        self, editable_source: Path, session: Session
    ) -> None:
        _ingest(editable_source, session)

        def change(events: list[dict[str, Any]]) -> None:
            _by_id(events, GOAL_SHOT)["shot"]["statsbomb_xg"] = 0.31
            _by_id(events, FIRST_PASS)["pass"]["outcome"] = {"id": 9, "name": "Incomplete"}

        _edit_events(editable_source, 9001, change)
        _ingest(editable_source, session)

        shot = session.get(ShotDetail, _event(session, GOAL_SHOT).id)
        passing = session.get(PassDetail, _event(session, FIRST_PASS).id)
        assert shot is not None and shot.statsbomb_xg == 0.31
        assert passing is not None and passing.outcome_name == "Incomplete"
        assert _count(session, ShotDetail) == 6

    def test_source_null_does_not_overwrite_an_existing_detail_value(
        self, editable_source: Path, session: Session
    ) -> None:
        _ingest(editable_source, session)

        def strip(events: list[dict[str, Any]]) -> None:
            detail = _by_id(events, FIRST_PASS)["pass"]
            for key in ("length", "recipient", "height"):
                del detail[key]

        _edit_events(editable_source, 9001, strip)
        _ingest(editable_source, session)
        detail = session.get(PassDetail, _event(session, FIRST_PASS).id)

        assert detail is not None
        assert detail.length == 12.5
        assert detail.height_name == "Ground Pass"
        assert detail.recipient is not None


class TestStaleDetailRemoval:
    def test_pass_that_becomes_a_carry_loses_its_detail(
        self, editable_source: Path, session: Session
    ) -> None:
        _ingest(editable_source, session)
        event_id = _event(session, FIRST_PASS).id

        def to_carry(events: list[dict[str, Any]]) -> None:
            event = _by_id(events, FIRST_PASS)
            event["type"] = {"id": 43, "name": "Carry"}
            event["carry"] = {"end_location": [60.0, 40.0]}
            del event["pass"]

        _edit_events(editable_source, 9001, to_carry)
        _ingest(editable_source, session)

        assert session.get(PassDetail, event_id) is None
        assert _event(session, FIRST_PASS).type_name == "Carry"
        assert _count(session, PassDetail) == 7
        assert _count(session, ShotDetail) == 6

    def test_pass_that_becomes_a_shot_swaps_its_detail(
        self, editable_source: Path, session: Session
    ) -> None:
        _ingest(editable_source, session)
        event_id = _event(session, FIRST_PASS).id

        def to_shot(events: list[dict[str, Any]]) -> None:
            event = _by_id(events, FIRST_PASS)
            event["type"] = {"id": 16, "name": "Shot"}
            event["shot"] = {"statsbomb_xg": 0.4, "outcome": {"id": 97, "name": "Goal"}}
            del event["pass"]

        _edit_events(editable_source, 9001, to_shot)
        _ingest(editable_source, session)

        shot = session.get(ShotDetail, event_id)
        assert session.get(PassDetail, event_id) is None
        assert shot is not None and shot.statsbomb_xg == 0.4
        assert _count(session, PassDetail) == 7
        assert _count(session, ShotDetail) == 7

    def test_shot_that_becomes_a_pass_swaps_its_detail(
        self, editable_source: Path, session: Session
    ) -> None:
        _ingest(editable_source, session)
        event_id = _event(session, GOAL_SHOT).id

        def to_pass(events: list[dict[str, Any]]) -> None:
            event = _by_id(events, GOAL_SHOT)
            event["type"] = {"id": 30, "name": "Pass"}
            event["pass"] = {"length": 5.0, "end_location": [70.0, 40.0]}
            del event["shot"]

        _edit_events(editable_source, 9001, to_pass)
        _ingest(editable_source, session)

        assert session.get(ShotDetail, event_id) is None
        passing = session.get(PassDetail, event_id)
        assert passing is not None and passing.length == 5.0
        assert _count(session, ShotDetail) == 5
        assert _count(session, PassDetail) == 9

    def test_unchanged_events_keep_their_details(
        self, editable_source: Path, session: Session
    ) -> None:
        _ingest(editable_source, session)
        before = session.get(PassDetail, _event(session, KEY_PASS).id)
        assert before is not None

        _edit_events(editable_source, 9001, lambda events: None)
        _ingest(editable_source, session)

        assert session.get(PassDetail, _event(session, KEY_PASS).id) is not None


class TestMalformedDetailReporting:
    def test_malformed_details_reject_only_their_event(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        summary = _ingest(v2_source_dir, session)

        assert summary.passes_skipped == 2
        assert summary.shots_skipped == 2
        assert summary.events_skipped == 4
        for index in (10, 11, 12, 13):
            statsbomb_id = f"90020000-0000-4000-8000-0000000000{index}"
            assert session.scalar(select(Event).where(Event.statsbomb_id == statsbomb_id)) is None

    def test_rejections_carry_file_match_event_and_reason(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        summary = _ingest(v2_source_dir, session)
        by_event = {record.record_external_id: record for record in summary.rejected}

        short_end = by_event["90020000-0000-4000-8000-000000000010"]
        assert short_end.match_external_id == 9002
        assert short_end.source_file.endswith("events/9002.json")
        assert "pass.end_location" in short_end.reason
        assert "shot.statsbomb_xg" in by_event["90020000-0000-4000-8000-000000000011"].reason
        assert "pass.recipient.id" in by_event["90020000-0000-4000-8000-000000000012"].reason
        assert "shot.end_location" in by_event["90020000-0000-4000-8000-000000000013"].reason

    def test_valid_events_around_a_rejected_one_are_still_imported(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        _ingest(v2_source_dir, session)
        match = session.scalar(select(Match).where(Match.statsbomb_id == 9002))
        assert match is not None

        imported = session.scalar(
            select(func.count()).select_from(Event).where(Event.match_id == match.id)
        )
        assert imported == 9  # 13 listed, 4 rejected


class TestSummaryCompleteness:
    def test_counts_for_the_full_season(self, v2_source_dir: Path, session: Session) -> None:
        summary = _ingest(v2_source_dir, session)

        assert summary.matches_listed == 2
        assert summary.matches_processed == 2
        assert (summary.event_files_found, summary.event_files_missing) == (2, 0)
        assert (summary.lineup_files_found, summary.lineup_files_missing) == (2, 0)
        assert summary.events_imported == 19
        assert summary.lineups_imported == 11  # 5 + 6 squad rows

    def test_summary_lines_list_every_required_figure(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        text = "\n".join(_ingest(v2_source_dir, session).as_lines())

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
            "source manifest id: ",
        ):
            assert expected in text

    def test_missing_files_are_listed_apart_from_rejected_records(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "events" / "9002.json").unlink()
        (editable_source / "lineups" / "9001.json").unlink()

        summary = _ingest(editable_source, session)
        text = "\n".join(summary.as_lines())

        assert (summary.event_files_found, summary.event_files_missing) == (1, 1)
        assert (summary.lineup_files_found, summary.lineup_files_missing) == (1, 1)
        assert summary.validation_failures == 0
        assert "unavailable files:" in text
        assert "rejected records:" not in text
        assert "events file for match 9002" in text

    def test_missing_file_is_not_treated_as_an_empty_file(
        self, editable_source: Path, session: Session
    ) -> None:
        (editable_source / "events" / "9002.json").unlink()

        summary = _ingest(editable_source, session)

        assert summary.events_imported == 10
        assert summary.event_files_missing == 1
        assert summary.import_status == "partial"


class TestSeasonScoping:
    def test_each_season_imports_only_its_own_matches(
        self, v2_source_dir: Path, session: Session
    ) -> None:
        first = _ingest(v2_source_dir, session, "2017/2018")
        second = _ingest(v2_source_dir, session, "2016/2017")

        assert (first.matches_processed, second.matches_processed) == (2, 1)
        assert _count(session, Match) == 3
        assert second.passes_imported == 2
        assert second.shots_imported == 2
        assert second.import_status == "success"
