"""Read-only queries backing the API.

Collection helpers return ``(items, total)`` so routes can build the pagination
envelope without duplicating count queries.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Competition, Event, Lineup, Match, Player, Season, Team


def _paginate(
    session: Session, statement: Select[Any], *, limit: int, offset: int
) -> tuple[list[Any], int]:
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = list(session.scalars(statement.limit(limit).offset(offset)).unique())
    return items, total


def list_competitions(
    session: Session, *, limit: int, offset: int
) -> tuple[list[Competition], int]:
    statement = select(Competition).order_by(Competition.name, Competition.statsbomb_id)
    return _paginate(session, statement, limit=limit, offset=offset)


def get_competition(session: Session, competition_id: int) -> Competition | None:
    return session.scalar(select(Competition).where(Competition.statsbomb_id == competition_id))


def list_seasons(
    session: Session, competition: Competition, *, limit: int, offset: int
) -> tuple[list[Season], int]:
    statement = (
        select(Season)
        .where(Season.competition_id == competition.id)
        .order_by(Season.name.desc(), Season.statsbomb_id)
    )
    return _paginate(session, statement, limit=limit, offset=offset)


def list_teams(
    session: Session, *, q: str | None, limit: int, offset: int
) -> tuple[list[Team], int]:
    statement = select(Team).order_by(Team.name, Team.statsbomb_id)
    if q:
        statement = statement.where(Team.name.ilike(f"%{q}%"))
    return _paginate(session, statement, limit=limit, offset=offset)


def get_team(session: Session, team_id: int) -> Team | None:
    return session.scalar(select(Team).where(Team.statsbomb_id == team_id))


def list_players(
    session: Session, *, q: str | None, limit: int, offset: int
) -> tuple[list[Player], int]:
    statement = select(Player).order_by(Player.name, Player.statsbomb_id)
    if q:
        statement = statement.where(Player.name.ilike(f"%{q}%"))
    return _paginate(session, statement, limit=limit, offset=offset)


def get_player(session: Session, player_id: int) -> Player | None:
    return session.scalar(select(Player).where(Player.statsbomb_id == player_id))


def list_matches(
    session: Session,
    *,
    competition_id: int | None = None,
    season_id: int | None = None,
    team_id: int | None = None,
    match_date: date | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Match], int]:
    statement = (
        select(Match)
        .options(
            selectinload(Match.competition),
            selectinload(Match.season),
            selectinload(Match.home_team),
            selectinload(Match.away_team),
        )
        .order_by(Match.match_date.desc(), Match.statsbomb_id)
    )
    if competition_id is not None:
        statement = statement.join(Competition, Match.competition_id == Competition.id).where(
            Competition.statsbomb_id == competition_id
        )
    if season_id is not None:
        statement = statement.join(Season, Match.season_id == Season.id).where(
            Season.statsbomb_id == season_id
        )
    if team_id is not None:
        home = select(Team.id).where(Team.statsbomb_id == team_id).scalar_subquery()
        statement = statement.where((Match.home_team_id == home) | (Match.away_team_id == home))
    if match_date is not None:
        statement = statement.where(Match.match_date == match_date)
    return _paginate(session, statement, limit=limit, offset=offset)


def get_match(session: Session, match_id: int) -> Match | None:
    return session.scalar(
        select(Match)
        .options(
            selectinload(Match.competition),
            selectinload(Match.season),
            selectinload(Match.home_team),
            selectinload(Match.away_team),
        )
        .where(Match.statsbomb_id == match_id)
    )


def list_match_events(
    session: Session,
    match: Match,
    *,
    type_name: str | None = None,
    team_id: int | None = None,
    player_id: int | None = None,
    period: int | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Event], int]:
    statement = (
        select(Event)
        .options(
            selectinload(Event.team),
            selectinload(Event.possession_team),
            selectinload(Event.player),
        )
        .where(Event.match_id == match.id)
        .order_by(Event.index_in_match)
    )
    if type_name is not None:
        statement = statement.where(Event.type_name == type_name)
    if team_id is not None:
        statement = statement.where(
            Event.team_id == select(Team.id).where(Team.statsbomb_id == team_id).scalar_subquery()
        )
    if player_id is not None:
        statement = statement.where(
            Event.player_id
            == select(Player.id).where(Player.statsbomb_id == player_id).scalar_subquery()
        )
    if period is not None:
        statement = statement.where(Event.period == period)
    return _paginate(session, statement, limit=limit, offset=offset)


def list_match_lineups(
    session: Session, match: Match, *, limit: int, offset: int
) -> tuple[list[Lineup], int]:
    statement = (
        select(Lineup)
        .options(selectinload(Lineup.team), selectinload(Lineup.player))
        .where(Lineup.match_id == match.id)
        .order_by(Lineup.team_id, Lineup.jersey_number, Lineup.id)
    )
    return _paginate(session, statement, limit=limit, offset=offset)
