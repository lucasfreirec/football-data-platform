"""Match, event, and lineup endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import PaginationDep, SessionDep
from app.api.schemas.common import Page
from app.api.schemas.resources import EventRead, LineupRead, MatchRead
from app.db.models import Match
from app.repositories import queries

router = APIRouter(prefix="/matches", tags=["matches"])


def _require_match(session: Session, match_id: int) -> Match:
    match = queries.get_match(session, match_id)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Match not found")
    return match


@router.get(
    "",
    response_model=Page[MatchRead],
    summary="List matches",
    description=(
        "Paginated list of matches. Filters use StatsBomb identifiers; `team_id` matches "
        "either the home or the away team."
    ),
)
def list_matches(
    session: SessionDep,
    pagination: PaginationDep,
    competition_id: Annotated[
        int | None, Query(description="StatsBomb competition identifier.")
    ] = None,
    season_id: Annotated[int | None, Query(description="StatsBomb season identifier.")] = None,
    team_id: Annotated[
        int | None, Query(description="StatsBomb team identifier, home or away.")
    ] = None,
    match_date: Annotated[date | None, Query(description="Exact match date (YYYY-MM-DD).")] = None,
) -> Page[MatchRead]:
    items, total = queries.list_matches(
        session,
        competition_id=competition_id,
        season_id=season_id,
        team_id=team_id,
        match_date=match_date,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[MatchRead](
        items=[MatchRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{match_id}",
    response_model=MatchRead,
    summary="Get a match",
    description="Look up a single match by its StatsBomb identifier.",
    responses={404: {"description": "Match not found."}},
)
def get_match(match_id: int, session: SessionDep) -> MatchRead:
    return MatchRead.model_validate(_require_match(session, match_id))


@router.get(
    "/{match_id}/events",
    response_model=Page[EventRead],
    summary="List a match's events",
    description="Paginated event feed for one match, ordered by the source event index.",
    responses={404: {"description": "Match not found."}},
)
def list_match_events(
    match_id: int,
    session: SessionDep,
    pagination: PaginationDep,
    type_name: Annotated[
        str | None, Query(description="Exact StatsBomb event type, e.g. `Pass`.")
    ] = None,
    team_id: Annotated[int | None, Query(description="StatsBomb team identifier.")] = None,
    player_id: Annotated[int | None, Query(description="StatsBomb player identifier.")] = None,
    period: Annotated[int | None, Query(description="Match period number.")] = None,
) -> Page[EventRead]:
    match = _require_match(session, match_id)
    items, total = queries.list_match_events(
        session,
        match,
        type_name=type_name,
        team_id=team_id,
        player_id=player_id,
        period=period,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[EventRead](
        items=[EventRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{match_id}/lineups",
    response_model=Page[LineupRead],
    summary="List a match's lineups",
    description="Paginated squad rows for one match, one row per player.",
    responses={404: {"description": "Match not found."}},
)
def list_match_lineups(
    match_id: int, session: SessionDep, pagination: PaginationDep
) -> Page[LineupRead]:
    match = _require_match(session, match_id)
    items, total = queries.list_match_lineups(
        session, match, limit=pagination.limit, offset=pagination.offset
    )
    return Page[LineupRead](
        items=[LineupRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )
