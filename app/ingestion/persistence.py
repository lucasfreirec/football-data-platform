"""Conflict-safe writes keyed on StatsBomb external IDs.

Every method is idempotent: re-importing the same source updates the existing
row instead of inserting a duplicate. Source values that are ``None`` never
overwrite data already stored, so a lean reference (an event's ``team``) cannot
downgrade a rich record (a match's ``home_team``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Competition, Event, Lineup, Match, Player, Season, Team
from app.ingestion.normalizers import (
    NormalizedCompetition,
    NormalizedEvent,
    NormalizedLineupEntry,
    NormalizedMatch,
    NormalizedPlayer,
    NormalizedSeason,
    NormalizedTeam,
)


class IngestionRepository:
    """Upserts reference data and matches within a single session."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._competitions: dict[int, Competition] = {}
        self._seasons: dict[tuple[int, int], Season] = {}
        self._teams: dict[int, Team] = {}
        self._players: dict[int, Player] = {}

    def upsert_competition(self, record: NormalizedCompetition) -> Competition:
        competition = self._competitions.get(record.statsbomb_id) or self.session.scalar(
            select(Competition).where(Competition.statsbomb_id == record.statsbomb_id)
        )
        values = {
            "name": record.name,
            "country_name": record.country_name,
            "gender": record.gender,
            "format": record.format,
        }
        if competition is None:
            competition = Competition(statsbomb_id=record.statsbomb_id, **_present(values))
            self.session.add(competition)
            self.session.flush()
        else:
            _apply(competition, values)
        self._competitions[record.statsbomb_id] = competition
        return competition

    def upsert_season(self, competition: Competition, record: NormalizedSeason) -> Season:
        key = (competition.id, record.statsbomb_id)
        season = self._seasons.get(key) or self.session.scalar(
            select(Season).where(
                Season.competition_id == competition.id,
                Season.statsbomb_id == record.statsbomb_id,
            )
        )
        if season is None:
            season = Season(
                competition_id=competition.id,
                statsbomb_id=record.statsbomb_id,
                name=record.name,
            )
            self.session.add(season)
            self.session.flush()
        else:
            _apply(season, {"name": record.name})
        self._seasons[key] = season
        return season

    def upsert_team(self, record: NormalizedTeam) -> Team:
        team = self._teams.get(record.statsbomb_id) or self.session.scalar(
            select(Team).where(Team.statsbomb_id == record.statsbomb_id)
        )
        values = {
            "name": record.name,
            "gender": record.gender,
            "country_name": record.country_name,
        }
        if team is None:
            team = Team(statsbomb_id=record.statsbomb_id, **_present(values))
            self.session.add(team)
            self.session.flush()
        else:
            _apply(team, values)
        self._teams[record.statsbomb_id] = team
        return team

    def upsert_player(self, record: NormalizedPlayer) -> Player:
        player = self._players.get(record.statsbomb_id) or self.session.scalar(
            select(Player).where(Player.statsbomb_id == record.statsbomb_id)
        )
        values = {
            "name": record.name,
            "nickname": record.nickname,
            "country_name": record.country_name,
            "birth_date": record.birth_date,
            "position_name": record.position_name,
        }
        if player is None:
            player = Player(statsbomb_id=record.statsbomb_id, **_present(values))
            self.session.add(player)
            self.session.flush()
        else:
            _apply(player, values)
        self._players[record.statsbomb_id] = player
        return player

    def upsert_match(self, record: NormalizedMatch) -> Match:
        """Upsert a match together with the references it depends on."""
        competition = self.upsert_competition(record.competition)
        season = self.upsert_season(competition, record.season)
        home_team = self.upsert_team(record.home_team)
        away_team = self.upsert_team(record.away_team)

        match = self.session.scalar(select(Match).where(Match.statsbomb_id == record.statsbomb_id))
        values: dict[str, Any] = {
            "competition_id": competition.id,
            "season_id": season.id,
            "home_team_id": home_team.id,
            "away_team_id": away_team.id,
            "match_date": record.match_date,
            "kick_off": record.kick_off,
            "home_score": record.home_score,
            "away_score": record.away_score,
            "stage_name": record.stage_name,
            "status": record.status,
            "last_updated": record.last_updated,
        }
        if match is None:
            match = Match(statsbomb_id=record.statsbomb_id, **_present(values))
            self.session.add(match)
            self.session.flush()
        else:
            _apply(match, values)
        return match

    def upsert_events(self, match: Match, records: Sequence[NormalizedEvent]) -> int:
        """Write a match's events, returning how many rows were inserted or updated."""
        existing = {
            event.statsbomb_id: event
            for event in self.session.scalars(select(Event).where(Event.match_id == match.id))
        }
        for record in records:
            values: dict[str, Any] = {
                "index_in_match": record.index_in_match,
                "type_name": record.type_name,
                "period": record.period,
                "timestamp": record.timestamp,
                "minute": record.minute,
                "second": record.second,
                "possession": record.possession,
                "possession_team_id": self._team_id(record.possession_team),
                "team_id": self._team_id(record.team),
                "player_id": self._player_id(record.player),
                "location_x": record.location_x,
                "location_y": record.location_y,
                "outcome_name": record.outcome_name,
                "raw_data": record.raw_data,
            }
            event = existing.get(record.statsbomb_id)
            if event is None:
                event = Event(
                    statsbomb_id=record.statsbomb_id, match_id=match.id, **_present(values)
                )
                self.session.add(event)
                existing[record.statsbomb_id] = event
            else:
                _apply(event, values)
        self.session.flush()
        return len(records)

    def upsert_lineups(self, match: Match, records: Sequence[NormalizedLineupEntry]) -> int:
        """Write a match's squad rows, returning how many were inserted or updated."""
        existing = {
            (lineup.team_id, lineup.player_id): lineup
            for lineup in self.session.scalars(select(Lineup).where(Lineup.match_id == match.id))
        }
        for record in records:
            team = self.upsert_team(record.team)
            player = self.upsert_player(record.player)
            values: dict[str, Any] = {
                "jersey_number": record.jersey_number,
                "position_name": record.position_name,
                "is_starter": record.is_starter,
                "raw_data": record.raw_data,
            }
            lineup = existing.get((team.id, player.id))
            if lineup is None:
                lineup = Lineup(
                    match_id=match.id, team_id=team.id, player_id=player.id, **_present(values)
                )
                self.session.add(lineup)
                existing[(team.id, player.id)] = lineup
            else:
                _apply(lineup, values)
        self.session.flush()
        return len(records)

    def _team_id(self, record: NormalizedTeam | None) -> int | None:
        return None if record is None else self.upsert_team(record).id

    def _player_id(self, record: NormalizedPlayer | None) -> int | None:
        return None if record is None else self.upsert_player(record).id


def _present(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _apply(instance: object, values: dict[str, Any]) -> bool:
    """Copy non-null values onto ``instance``; return whether anything changed."""
    changed = False
    for key, value in values.items():
        if value is None or getattr(instance, key) == value:
            continue
        setattr(instance, key, value)
        changed = True
    return changed
