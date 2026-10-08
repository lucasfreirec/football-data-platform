"""Player endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import PaginationDep, SessionDep
from app.api.schemas.common import Page
from app.api.schemas.resources import PlayerRead
from app.repositories import queries

router = APIRouter(prefix="/players", tags=["players"])


@router.get(
    "",
    response_model=Page[PlayerRead],
    summary="List players",
    description="Paginated list of players, optionally filtered by a case-insensitive name search.",
)
def list_players(
    session: SessionDep,
    pagination: PaginationDep,
    q: Annotated[str | None, Query(description="Case-insensitive player name search.")] = None,
) -> Page[PlayerRead]:
    items, total = queries.list_players(
        session, q=q, limit=pagination.limit, offset=pagination.offset
    )
    return Page[PlayerRead](
        items=[PlayerRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{player_id}",
    response_model=PlayerRead,
    summary="Get a player",
    description="Look up a single player by their StatsBomb identifier.",
    responses={404: {"description": "Player not found."}},
)
def get_player(player_id: int, session: SessionDep) -> PlayerRead:
    player = queries.get_player(session, player_id)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Player not found")
    return PlayerRead.model_validate(player)
