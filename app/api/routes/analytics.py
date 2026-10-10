"""Season statistics endpoints, derived from imported pass and shot events."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import PaginationDep, SessionDep
from app.api.lookups import require_competition, require_player, require_season, require_team
from app.api.schemas.common import Page
from app.api.schemas.stats import PlayerSeasonStats, TeamSeasonStats
from app.repositories import stats
from app.repositories.stats import SortOrder, StatSortBy

router = APIRouter(tags=["analytics"])

_DISCLAIMER = (
    " Figures are computed from the imported source records and are not an official "
    "StatsBomb analytical product."
)

CompetitionQuery = Annotated[int, Query(description="StatsBomb competition identifier.")]
SeasonQuery = Annotated[int, Query(description="StatsBomb season identifier.")]
SortByQuery = Annotated[StatSortBy, Query(description="Statistic to rank by.")]
OrderQuery = Annotated[
    SortOrder, Query(description="Sort direction. Ties are broken by ascending identifier.")
]


@router.get(
    "/players/{player_id}/stats",
    response_model=PlayerSeasonStats,
    summary="Get a player's season statistics",
    description=(
        "Pass and shot totals for one player in one competition season. A player with no "
        "activity in the season gets zeros rather than an error." + _DISCLAIMER
    ),
    responses={404: {"description": "Player, competition, or season not found."}},
)
def get_player_stats(
    player_id: int,
    competition_id: CompetitionQuery,
    season_id: SeasonQuery,
    session: SessionDep,
) -> PlayerSeasonStats:
    player = require_player(session, player_id)
    competition = require_competition(session, competition_id)
    season = require_season(session, competition, season_id)
    row = stats.get_player_stats(session, player, competition, season)
    return PlayerSeasonStats(
        **row._asdict(), competition_id=competition.statsbomb_id, season_id=season.statsbomb_id
    )


@router.get(
    "/teams/{team_id}/stats",
    response_model=TeamSeasonStats,
    summary="Get a team's season statistics",
    description=(
        "Pass and shot totals for one team in one competition season. A team with no "
        "matches in the season gets zeros rather than an error." + _DISCLAIMER
    ),
    responses={404: {"description": "Team, competition, or season not found."}},
)
def get_team_stats(
    team_id: int,
    competition_id: CompetitionQuery,
    season_id: SeasonQuery,
    session: SessionDep,
) -> TeamSeasonStats:
    team = require_team(session, team_id)
    competition = require_competition(session, competition_id)
    season = require_season(session, competition, season_id)
    row = stats.get_team_stats(session, team, competition, season)
    return TeamSeasonStats(
        **row._asdict(), competition_id=competition.statsbomb_id, season_id=season.statsbomb_id
    )


@router.get(
    "/competitions/{competition_id}/seasons/{season_id}/player-stats",
    response_model=Page[PlayerSeasonStats],
    summary="Season player leaderboard",
    description=(
        "Paginated player statistics for a season, including every player with a lineup row "
        "or a pass or shot in it. Rows with a `null` ranking value sort last in either "
        "direction." + _DISCLAIMER
    ),
    responses={404: {"description": "Competition or season not found."}},
)
def list_player_stats(
    competition_id: int,
    season_id: int,
    session: SessionDep,
    pagination: PaginationDep,
    sort_by: SortByQuery = StatSortBy.xg,
    order: OrderQuery = SortOrder.desc,
) -> Page[PlayerSeasonStats]:
    competition = require_competition(session, competition_id)
    season = require_season(session, competition, season_id)
    rows, total = stats.list_player_stats(
        session,
        competition,
        season,
        sort_by=sort_by,
        order=order,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[PlayerSeasonStats](
        items=[
            PlayerSeasonStats(
                **row._asdict(),
                competition_id=competition.statsbomb_id,
                season_id=season.statsbomb_id,
            )
            for row in rows
        ],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.get(
    "/competitions/{competition_id}/seasons/{season_id}/team-stats",
    response_model=Page[TeamSeasonStats],
    summary="Season team leaderboard",
    description=(
        "Paginated team statistics for a season, covering every team that played an imported "
        "match in it. Rows with a `null` ranking value sort last in either direction." + _DISCLAIMER
    ),
    responses={404: {"description": "Competition or season not found."}},
)
def list_team_stats(
    competition_id: int,
    season_id: int,
    session: SessionDep,
    pagination: PaginationDep,
    sort_by: SortByQuery = StatSortBy.xg,
    order: OrderQuery = SortOrder.desc,
) -> Page[TeamSeasonStats]:
    competition = require_competition(session, competition_id)
    season = require_season(session, competition, season_id)
    rows, total = stats.list_team_stats(
        session,
        competition,
        season,
        sort_by=sort_by,
        order=order,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[TeamSeasonStats](
        items=[
            TeamSeasonStats(
                **row._asdict(),
                competition_id=competition.statsbomb_id,
                season_id=season.statsbomb_id,
            )
            for row in rows
        ],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )
