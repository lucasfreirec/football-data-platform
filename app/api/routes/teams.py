"""Team endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import PaginationDep, SessionDep
from app.api.schemas.common import Page
from app.api.schemas.resources import TeamRead
from app.repositories import queries

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get(
    "",
    response_model=Page[TeamRead],
    summary="List teams",
    description="Paginated list of teams, optionally filtered by a case-insensitive name search.",
)
def list_teams(
    session: SessionDep,
    pagination: PaginationDep,
    q: Annotated[str | None, Query(description="Case-insensitive team name search.")] = None,
) -> Page[TeamRead]:
    items, total = queries.list_teams(
        session, q=q, limit=pagination.limit, offset=pagination.offset
    )
    return Page[TeamRead](
        items=[TeamRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{team_id}",
    response_model=TeamRead,
    summary="Get a team",
    description="Look up a single team by its StatsBomb identifier.",
    responses={404: {"description": "Team not found."}},
)
def get_team(team_id: int, session: SessionDep) -> TeamRead:
    team = queries.get_team(session, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Team not found")
    return TeamRead.model_validate(team)
