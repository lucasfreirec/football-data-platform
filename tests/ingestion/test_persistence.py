from datetime import date, time
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Competition, Match, Player, Season, Team
from app.ingestion.normalizers import (
    NormalizedCompetition,
    NormalizedPlayer,
    NormalizedSeason,
    NormalizedTeam,
    normalize_competition_season,
    normalize_match,
)
from app.ingestion.persistence import IngestionRepository


@pytest.fixture
def repository(session: Session) -> IngestionRepository:
    return IngestionRepository(session)


def _count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


class TestCompetitionAndSeason:
    def test_insert_then_reuse(
        self, session: Session, repository: IngestionRepository, raw_competitions: list[Any]
    ) -> None:
        record = normalize_competition_season(raw_competitions[0])

        first = repository.upsert_competition(record.competition)
        season = repository.upsert_season(first, record.season)
        session.commit()

        assert first.id is not None
        assert season.competition_id == first.id
        assert _count(session, Competition) == 1
        assert _count(session, Season) == 1

    def test_two_seasons_share_one_competition(
        self, session: Session, repository: IngestionRepository, raw_competitions: list[Any]
    ) -> None:
        for raw in raw_competitions:
            record = normalize_competition_season(raw)
            competition = repository.upsert_competition(record.competition)
            repository.upsert_season(competition, record.season)
        session.commit()

        assert _count(session, Competition) == 1
        assert _count(session, Season) == 2

    def test_reimport_is_idempotent(
        self, session: Session, repository: IngestionRepository, raw_competitions: list[Any]
    ) -> None:
        record = normalize_competition_season(raw_competitions[0])
        for _ in range(3):
            competition = repository.upsert_competition(record.competition)
            repository.upsert_season(competition, record.season)
            session.commit()

        assert _count(session, Competition) == 1
        assert _count(session, Season) == 1

    def test_changed_values_are_updated(
        self, session: Session, repository: IngestionRepository
    ) -> None:
        repository.upsert_competition(NormalizedCompetition(statsbomb_id=16, name="Old Name"))
        session.commit()

        repository.upsert_competition(
            NormalizedCompetition(statsbomb_id=16, name="Champions League", gender="male")
        )
        session.commit()

        competition = session.scalar(select(Competition).where(Competition.statsbomb_id == 16))
        assert competition is not None
        assert competition.name == "Champions League"
        assert competition.gender == "male"
        assert _count(session, Competition) == 1

    def test_same_season_id_under_different_competitions(
        self, session: Session, repository: IngestionRepository
    ) -> None:
        first = repository.upsert_competition(NormalizedCompetition(statsbomb_id=16, name="UCL"))
        second = repository.upsert_competition(NormalizedCompetition(statsbomb_id=2, name="PL"))
        repository.upsert_season(first, NormalizedSeason(statsbomb_id=4, name="2017/2018"))
        repository.upsert_season(second, NormalizedSeason(statsbomb_id=4, name="2017/2018"))
        session.commit()

        assert _count(session, Season) == 2


class TestTeamAndPlayer:
    def test_team_upsert_is_idempotent(
        self, session: Session, repository: IngestionRepository
    ) -> None:
        record = NormalizedTeam(statsbomb_id=220, name="Real Madrid", country_name="Spain")
        repository.upsert_team(record)
        repository.upsert_team(record)
        session.commit()

        assert _count(session, Team) == 1

    def test_sparse_reference_does_not_erase_known_values(
        self, session: Session, repository: IngestionRepository
    ) -> None:
        repository.upsert_team(
            NormalizedTeam(
                statsbomb_id=220, name="Real Madrid", gender="male", country_name="Spain"
            )
        )
        session.commit()

        repository.upsert_team(NormalizedTeam(statsbomb_id=220, name="Real Madrid"))
        session.commit()

        team = session.scalar(select(Team).where(Team.statsbomb_id == 220))
        assert team is not None
        assert team.gender == "male"
        assert team.country_name == "Spain"

    def test_player_upsert_enriches_existing_row(
        self, session: Session, repository: IngestionRepository
    ) -> None:
        repository.upsert_player(NormalizedPlayer(statsbomb_id=3604, name="Karim Benzema"))
        session.commit()

        repository.upsert_player(
            NormalizedPlayer(
                statsbomb_id=3604,
                name="Karim Benzema",
                country_name="France",
                birth_date=date(1987, 12, 19),
                position_name="Center Forward",
            )
        )
        session.commit()

        player = session.scalar(select(Player).where(Player.statsbomb_id == 3604))
        assert player is not None
        assert player.country_name == "France"
        assert player.birth_date == date(1987, 12, 19)
        assert _count(session, Player) == 1


class TestMatch:
    def test_upsert_resolves_all_references(
        self, session: Session, repository: IngestionRepository, raw_matches: list[Any]
    ) -> None:
        match = repository.upsert_match(normalize_match(raw_matches[0]))
        session.commit()

        assert match.statsbomb_id == 7298
        assert match.match_date == date(2018, 5, 26)
        assert match.kick_off == time(21, 45)
        assert match.home_score == 3
        assert match.stage_name == "Final"
        assert match.home_team.name == "Real Madrid"
        assert match.away_team.name == "Liverpool"
        assert match.competition.statsbomb_id == 16
        assert match.season.statsbomb_id == 1

    def test_reimport_does_not_duplicate_anything(
        self, session: Session, repository: IngestionRepository, raw_matches: list[Any]
    ) -> None:
        for _ in range(2):
            for raw in raw_matches:
                repository.upsert_match(normalize_match(raw))
            session.commit()

        assert _count(session, Match) == 2
        assert _count(session, Competition) == 1
        assert _count(session, Season) == 1
        # Real Madrid, Liverpool, Barcelona
        assert _count(session, Team) == 3

    def test_reimport_in_a_fresh_repository_is_idempotent(
        self, session: Session, raw_matches: list[Any]
    ) -> None:
        for _ in range(2):
            repository = IngestionRepository(session)
            for raw in raw_matches:
                repository.upsert_match(normalize_match(raw))
            session.commit()

        assert _count(session, Match) == 2
        assert _count(session, Team) == 3

    def test_updated_score_is_persisted(
        self, session: Session, repository: IngestionRepository, raw_matches: list[Any]
    ) -> None:
        repository.upsert_match(normalize_match(raw_matches[0] | {"home_score": 0}))
        session.commit()

        repository.upsert_match(normalize_match(raw_matches[0]))
        session.commit()

        match = session.scalar(select(Match).where(Match.statsbomb_id == 7298))
        assert match is not None
        assert match.home_score == 3
        assert _count(session, Match) == 1
