"""Pass, shot, and single-event detail endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.dependencies import PaginationDep, SessionDep
from app.api.lookups import require_match
from app.api.schemas.common import Page
from app.api.schemas.event_details import EventDetailRead, PassEventRead, ShotEventRead
from app.repositories import event_details

router = APIRouter(prefix="/matches", tags=["event details"])


@router.get(
    "/{match_id}/passes",
    response_model=Page[PassEventRead],
    summary="List a match's passes",
    description=(
        "Paginated pass events with their pass details, ordered by the source event index. "
        "Filters use StatsBomb identifiers. A completed pass has a `null` `outcome_name`."
    ),
    responses={404: {"description": "Match not found."}},
)
def list_match_passes(
    match_id: int,
    session: SessionDep,
    pagination: PaginationDep,
    team_id: Annotated[int | None, Query(description="StatsBomb team identifier.")] = None,
    player_id: Annotated[
        int | None, Query(description="StatsBomb identifier of the passing player.")
    ] = None,
    recipient_player_id: Annotated[
        int | None, Query(description="StatsBomb identifier of the receiving player.")
    ] = None,
    outcome_name: Annotated[
        str | None, Query(description="Exact pass outcome, such as `Incomplete` or `Out`.")
    ] = None,
) -> Page[PassEventRead]:
    match = require_match(session, match_id)
    items, total = event_details.list_match_passes(
        session,
        match,
        team_id=team_id,
        player_id=player_id,
        recipient_player_id=recipient_player_id,
        outcome_name=outcome_name,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[PassEventRead](
        items=[PassEventRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{match_id}/shots",
    response_model=Page[ShotEventRead],
    summary="List a match's shots",
    description=(
        "Paginated shot events with their shot details, ordered by the source event index. "
        "Filters use StatsBomb identifiers."
    ),
    responses={404: {"description": "Match not found."}},
)
def list_match_shots(
    match_id: int,
    session: SessionDep,
    pagination: PaginationDep,
    team_id: Annotated[int | None, Query(description="StatsBomb team identifier.")] = None,
    player_id: Annotated[
        int | None, Query(description="StatsBomb identifier of the shooting player.")
    ] = None,
    outcome_name: Annotated[
        str | None, Query(description="Exact shot outcome, such as `Goal` or `Saved`.")
    ] = None,
) -> Page[ShotEventRead]:
    match = require_match(session, match_id)
    items, total = event_details.list_match_shots(
        session,
        match,
        team_id=team_id,
        player_id=player_id,
        outcome_name=outcome_name,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[ShotEventRead](
        items=[ShotEventRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{match_id}/events/{event_id}",
    response_model=EventDetailRead,
    summary="Get one event with its details",
    description=(
        "A single event of the match, addressed by its StatsBomb UUID. `pass` is populated "
        "for pass events and `shot` for shot events; the other is `null`. The unmodelled "
        "`raw_data` fragment is not exposed here."
    ),
    responses={404: {"description": "Match or event not found."}},
)
def get_match_event(
    match_id: int,
    event_id: Annotated[str, Path(description="StatsBomb event UUID.")],
    session: SessionDep,
) -> EventDetailRead:
    match = require_match(session, match_id)
    event = event_details.get_match_event(session, match, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Event not found")
    return EventDetailRead.model_validate(event)
