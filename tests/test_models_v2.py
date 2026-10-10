from datetime import date

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Competition,
    Event,
    Match,
    PassDetail,
    Player,
    Season,
    ShotDetail,
    SourceManifest,
    Team,
)


@pytest.fixture
def match(session: Session) -> Match:
    competition = Competition(statsbomb_id=16, name="Champions League")
    home = Team(statsbomb_id=101, name="Alpha FC")
    away = Team(statsbomb_id=102, name="Beta FC")
    session.add_all([competition, home, away])
    session.flush()
    season = Season(competition_id=competition.id, statsbomb_id=1, name="2017/2018")
    session.add(season)
    session.flush()
    match = Match(
        statsbomb_id=9001,
        competition_id=competition.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        match_date=date(2018, 3, 1),
    )
    session.add(match)
    session.flush()
    return match


def _event(session: Session, match: Match, index: int, type_name: str) -> Event:
    event = Event(
        statsbomb_id=f"event-{index}", match_id=match.id, index_in_match=index, type_name=type_name
    )
    session.add(event)
    session.flush()
    return event


class TestPassDetail:
    def test_shares_the_primary_key_with_its_event(self, session: Session, match: Match) -> None:
        event = _event(session, match, 1, "Pass")
        session.add(PassDetail(event_id=event.id, length=12.5, outcome_name="Incomplete"))
        session.flush()

        assert event.pass_detail is not None
        assert event.pass_detail.event_id == event.id
        assert event.shot_detail is None

    def test_an_event_has_at_most_one_pass_detail(self, session: Session, match: Match) -> None:
        event = _event(session, match, 1, "Pass")
        session.add(PassDetail(event_id=event.id))
        session.flush()

        session.add(PassDetail(event_id=event.id))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_requires_an_existing_event(self, session: Session, match: Match) -> None:
        session.add(PassDetail(event_id=999_999))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_recipient_must_be_an_existing_player(self, session: Session, match: Match) -> None:
        event = _event(session, match, 1, "Pass")
        session.add(PassDetail(event_id=event.id, recipient_player_id=999_999))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_optional_fields_default_to_null(self, session: Session, match: Match) -> None:
        event = _event(session, match, 1, "Pass")
        session.add(PassDetail(event_id=event.id))
        session.flush()
        detail = session.get(PassDetail, event.id)

        assert detail is not None
        assert detail.recipient_player_id is None
        assert detail.outcome_name is None
        assert detail.is_cross is None
        assert detail.end_location_x is None

    def test_recipient_relationship(self, session: Session, match: Match) -> None:
        recipient = Player(statsbomb_id=1002, name="Ben Alpha")
        session.add(recipient)
        session.flush()
        event = _event(session, match, 1, "Pass")
        session.add(PassDetail(event_id=event.id, recipient_player_id=recipient.id))
        session.flush()

        assert event.pass_detail is not None
        assert event.pass_detail.recipient is recipient


class TestShotDetail:
    def test_shares_the_primary_key_with_its_event(self, session: Session, match: Match) -> None:
        event = _event(session, match, 1, "Shot")
        session.add(ShotDetail(event_id=event.id, statsbomb_xg=0.25, outcome_name="Goal"))
        session.flush()

        assert event.shot_detail is not None
        assert event.shot_detail.statsbomb_xg == 0.25
        assert event.pass_detail is None

    def test_key_pass_references_another_event(self, session: Session, match: Match) -> None:
        key_pass = _event(session, match, 1, "Pass")
        shot = _event(session, match, 2, "Shot")
        session.add(ShotDetail(event_id=shot.id, key_pass_event_id=key_pass.id))
        session.flush()

        assert shot.shot_detail is not None
        assert shot.shot_detail.key_pass_event is key_pass

    def test_deleting_the_key_pass_clears_the_reference(
        self, session: Session, match: Match
    ) -> None:
        key_pass = _event(session, match, 1, "Pass")
        shot = _event(session, match, 2, "Shot")
        session.add(ShotDetail(event_id=shot.id, key_pass_event_id=key_pass.id))
        session.flush()

        session.execute(delete(Event).where(Event.id == key_pass.id))
        session.expire_all()
        detail = session.get(ShotDetail, shot.id)

        assert detail is not None
        assert detail.key_pass_event_id is None


class TestCascade:
    def test_orm_delete_removes_the_detail_rows(self, session: Session, match: Match) -> None:
        passing = _event(session, match, 1, "Pass")
        shooting = _event(session, match, 2, "Shot")
        session.add_all([PassDetail(event_id=passing.id), ShotDetail(event_id=shooting.id)])
        session.flush()

        session.delete(passing)
        session.delete(shooting)
        session.flush()

        assert session.scalars(select(PassDetail)).all() == []
        assert session.scalars(select(ShotDetail)).all() == []

    def test_database_delete_cascades_to_detail_rows(self, session: Session, match: Match) -> None:
        event = _event(session, match, 1, "Pass")
        session.add(PassDetail(event_id=event.id))
        session.flush()

        session.execute(delete(Event).where(Event.id == event.id))

        assert session.scalars(select(PassDetail)).all() == []


class TestSourceManifest:
    @staticmethod
    def _manifest(match: Match, status: str) -> SourceManifest:
        return SourceManifest(
            competition_id=match.competition_id,
            season_id=match.season_id,
            source_path="/data/raw",
            match_count_expected=1,
            event_file_count_found=1,
            lineup_file_count_found=1,
            import_status=status,
        )

    @pytest.mark.parametrize("status", ["success", "partial", "failed"])
    def test_accepts_the_defined_statuses(
        self, session: Session, match: Match, status: str
    ) -> None:
        session.add(self._manifest(match, status))
        session.flush()

    @pytest.mark.parametrize("status", ["done", "SUCCESS", ""])
    def test_rejects_any_other_status(self, session: Session, match: Match, status: str) -> None:
        session.add(self._manifest(match, status))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_defaults_are_populated(self, session: Session, match: Match) -> None:
        manifest = self._manifest(match, "success")
        session.add(manifest)
        session.flush()
        session.refresh(manifest)

        assert manifest.imported_at is not None
        assert manifest.source_version is None
        assert manifest.summary == {}

    def test_requires_an_existing_competition_and_season(
        self, session: Session, match: Match
    ) -> None:
        manifest = self._manifest(match, "success")
        manifest.competition_id = 999_999
        session.add(manifest)

        with pytest.raises(IntegrityError):
            session.flush()

    def test_summary_round_trips_json(self, session: Session, match: Match) -> None:
        manifest = self._manifest(match, "partial")
        manifest.summary = {"events_skipped": 2, "missing_files": ["events/1.json"]}
        session.add(manifest)
        session.flush()
        session.expire_all()

        stored = session.scalar(select(SourceManifest))
        assert stored is not None
        assert stored.summary == {"events_skipped": 2, "missing_files": ["events/1.json"]}
