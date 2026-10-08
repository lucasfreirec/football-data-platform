"""Competition and season endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import PaginationDep, SessionDep
from app.api.schemas.common import Page
from app.api.schemas.resources import CompetitionRead, SeasonRead
from app.repositories import queries

router = APIRouter(prefix="/competitions", tags=["competitions"])


@router.get(
    "",
    response_model=Page[CompetitionRead],
    summary="List competitions",
    description="Paginated list of imported competitions.",
)
def list_competitions(session: SessionDep, pagination: PaginationDep) -> Page[CompetitionRead]:
    items, total = queries.list_competitions(
        session, limit=pagination.limit, offset=pagination.offset
    )
    return Page[CompetitionRead](
        items=[CompetitionRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/{competition_id}",
    response_model=CompetitionRead,
    summary="Get a competition",
    description="Look up a single competition by its StatsBomb identifier.",
    responses={404: {"description": "Competition not found."}},
)
def get_competition(competition_id: int, session: SessionDep) -> CompetitionRead:
    competition = queries.get_competition(session, competition_id)
    if competition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Competition not found")
    return CompetitionRead.model_validate(competition)


@router.get(
    "/{competition_id}/seasons",
    response_model=Page[SeasonRead],
    summary="List a competition's seasons",
    description="Paginated list of seasons imported for the given competition.",
    responses={404: {"description": "Competition not found."}},
)
def list_seasons(
    competition_id: int, session: SessionDep, pagination: PaginationDep
) -> Page[SeasonRead]:
    competition = queries.get_competition(session, competition_id)
    if competition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Competition not found")

    items, total = queries.list_seasons(
        session, competition, limit=pagination.limit, offset=pagination.offset
    )
    return Page[SeasonRead](
        items=[SeasonRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )
