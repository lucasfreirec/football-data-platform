"""Read-only queries for pass and shot details."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Event, Match, PassDetail, Player, ShotDetail, Team
from app.repositories.pagination import paginate

PASS_TYPE = "Pass"
SHOT_TYPE = "Shot"


def _event_query() -> Select[Event]:
    return select(Event).options(
        selectinload(Event.team),
        selectinload(Event.possession_team),
        selectinload(Event.player),
        selectinload(Event.pass_detail).selectinload(PassDetail.recipient),
        selectinload(Event.shot_detail).selectinload(ShotDetail.key_pass_event),
    )


def _team_filter(team_id: int) -> Any:
    return Event.team_id == select(Team.id).where(Team.statsbomb_id == team_id).scalar_subquery()


def _player_filter(column: Any, player_id: int) -> Any:
    return column == select(Player.id).where(Player.statsbomb_id == player_id).scalar_subquery()


def get_match_event(session: Session, match: Match, event_id: str) -> Event | None:
    """One event of the match by its StatsBomb UUID, with whichever detail applies."""
    return session.scalar(
        _event_query().where(Event.match_id == match.id, Event.statsbomb_id == event_id)
    )


def list_match_passes(
    session: Session,
    match: Match,
    *,
    team_id: int | None = None,
    player_id: int | None = None,
    recipient_player_id: int | None = None,
    outcome_name: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Event], int]:
    statement = (
        _event_query()
        .join(PassDetail, PassDetail.event_id == Event.id)
        .where(Event.match_id == match.id, Event.type_name == PASS_TYPE)
        .order_by(Event.index_in_match)
    )
    if team_id is not None:
        statement = statement.where(_team_filter(team_id))
    if player_id is not None:
        statement = statement.where(_player_filter(Event.player_id, player_id))
    if recipient_player_id is not None:
        statement = statement.where(
            _player_filter(PassDetail.recipient_player_id, recipient_player_id)
        )
    if outcome_name is not None:
        statement = statement.where(PassDetail.outcome_name == outcome_name)
    return paginate(session, statement, limit=limit, offset=offset)


def list_match_shots(
    session: Session,
    match: Match,
    *,
    team_id: int | None = None,
    player_id: int | None = None,
    outcome_name: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Event], int]:
    statement = (
        _event_query()
        .join(ShotDetail, ShotDetail.event_id == Event.id)
        .where(Event.match_id == match.id, Event.type_name == SHOT_TYPE)
        .order_by(Event.index_in_match)
    )
    if team_id is not None:
        statement = statement.where(_team_filter(team_id))
    if player_id is not None:
        statement = statement.where(_player_filter(Event.player_id, player_id))
    if outcome_name is not None:
        statement = statement.where(ShotDetail.outcome_name == outcome_name)
    return paginate(session, statement, limit=limit, offset=offset)
