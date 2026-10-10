"""Season aggregates, computed from persisted events at request time.

Definitions (see the README for the user-facing wording):

* passes attempted  - ``Pass`` events
* passes completed  - ``Pass`` events whose ``pass_details.outcome_name`` is NULL
* shots             - ``Shot`` events
* goals             - shots whose ``shot_details.outcome_name`` is ``Goal``
* xG                - sum of non-null ``statsbomb_xg`` over shots, 0 when there are none
* appearances       - distinct matches with at least one lineup row for the player

``pass_details`` and ``shot_details`` share the event's primary key, so joining
them onto ``events`` is strictly one-to-one and cannot multiply rows. Lineups
are aggregated in their own subquery for the same reason.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Float,
    Row,
    Select,
    Subquery,
    and_,
    case,
    cast,
    func,
    select,
    union,
    union_all,
)
from sqlalchemy.orm import Session

from app.db.models import (
    Competition,
    Event,
    Lineup,
    Match,
    PassDetail,
    Player,
    Season,
    ShotDetail,
    Team,
)
from app.repositories.event_details import PASS_TYPE, SHOT_TYPE
from app.repositories.pagination import paginate_rows

GOAL_OUTCOME = "Goal"


class StatSortBy(StrEnum):
    xg = "xg"
    goals = "goals"
    shots = "shots"
    passes_completed = "passes_completed"
    pass_completion_pct = "pass_completion_pct"


class SortOrder(StrEnum):
    asc = "asc"
    desc = "desc"


def _scoped_matches(competition: Competition, season: Season) -> Select[Any]:
    """Only matches of this competition and season may contribute to a statistic."""
    return select(Match.id).where(
        Match.competition_id == competition.id, Match.season_id == season.id
    )


def _event_stats(scope: Select[Any], key: Any) -> Subquery:
    """Pass and shot counts per ``key`` (a player or team column) within ``scope``."""
    is_pass = Event.type_name == PASS_TYPE
    is_shot = Event.type_name == SHOT_TYPE
    return (
        select(
            key.label("key"),
            func.count(case((is_pass, 1))).label("passes_attempted"),
            func.count(case((and_(is_pass, PassDetail.outcome_name.is_(None)), 1))).label(
                "passes_completed"
            ),
            func.count(case((is_shot, 1))).label("shots"),
            func.count(case((and_(is_shot, ShotDetail.outcome_name == GOAL_OUTCOME), 1))).label(
                "goals"
            ),
            func.sum(case((is_shot, ShotDetail.statsbomb_xg))).label("xg"),
        )
        .select_from(Event)
        .outerjoin(PassDetail, PassDetail.event_id == Event.id)
        .outerjoin(ShotDetail, ShotDetail.event_id == Event.id)
        .where(
            Event.match_id.in_(scope),
            key.is_not(None),
            Event.type_name.in_((PASS_TYPE, SHOT_TYPE)),
        )
        .group_by(key)
        .subquery()
    )


def _metrics(event_stats: Subquery) -> dict[str, Any]:
    """Outer-query expressions: absent aggregates become 0, ratios become NULL."""
    attempted = func.coalesce(event_stats.c.passes_attempted, 0)
    completed = func.coalesce(event_stats.c.passes_completed, 0)
    shots = func.coalesce(event_stats.c.shots, 0)
    goals = func.coalesce(event_stats.c.goals, 0)
    xg = func.coalesce(event_stats.c.xg, 0.0)
    return {
        "passes_attempted": attempted,
        "passes_completed": completed,
        "pass_completion_pct": case(
            (attempted > 0, cast(completed, Float) * 100.0 / cast(attempted, Float))
        ),
        "shots": shots,
        "goals": goals,
        "xg": xg,
        "xg_per_shot": case((shots > 0, xg / cast(shots, Float))),
    }


def _ordering(
    metrics: dict[str, Any], key: Any, sort_by: StatSortBy, order: SortOrder
) -> list[Any]:
    """NULLs sort last in both directions; the identifier makes pages stable."""
    column = metrics[sort_by.value]
    primary = column.asc() if order is SortOrder.asc else column.desc()
    return [primary.nulls_last(), key.asc()]


def _labelled(metrics: dict[str, Any]) -> list[Any]:
    return [expression.label(name) for name, expression in metrics.items()]


def _player_statement(
    competition: Competition, season: Season
) -> tuple[Select[Any], dict[str, Any], Select[Any]]:
    scope = _scoped_matches(competition, season)
    events = _event_stats(scope, Event.player_id)
    appearances = (
        select(
            Lineup.player_id.label("key"),
            func.count(func.distinct(Lineup.match_id)).label("appearances"),
        )
        .where(Lineup.match_id.in_(scope))
        .group_by(Lineup.player_id)
        .subquery()
    )
    metrics = _metrics(events)
    metrics_with_appearances = {
        "appearances": func.coalesce(appearances.c.appearances, 0),
        **metrics,
    }
    statement = (
        select(
            Player.statsbomb_id.label("player_id"),
            Player.name.label("player_name"),
            *_labelled(metrics_with_appearances),
        )
        .select_from(Player)
        .outerjoin(appearances, appearances.c.key == Player.id)
        .outerjoin(events, events.c.key == Player.id)
    )
    in_scope = union(
        select(Lineup.player_id.label("player_id")).where(Lineup.match_id.in_(scope)),
        select(Event.player_id.label("player_id")).where(
            Event.match_id.in_(scope), Event.player_id.is_not(None)
        ),
    ).subquery()
    return statement, metrics_with_appearances, select(in_scope.c.player_id)


def get_player_stats(
    session: Session, player: Player, competition: Competition, season: Season
) -> Row[Any]:
    """One player's season line; a player with no activity yields zeros, not no row."""
    statement, _, _ = _player_statement(competition, season)
    return session.execute(statement.where(Player.id == player.id)).one()


def list_player_stats(
    session: Session,
    competition: Competition,
    season: Season,
    *,
    sort_by: StatSortBy,
    order: SortOrder,
    limit: int,
    offset: int,
) -> tuple[list[Row[Any]], int]:
    statement, metrics, in_scope = _player_statement(competition, season)
    statement = statement.where(Player.id.in_(in_scope)).order_by(
        *_ordering(metrics, Player.statsbomb_id, sort_by, order)
    )
    return paginate_rows(session, statement, limit=limit, offset=offset)


def _team_statement(
    competition: Competition, season: Season
) -> tuple[Select[Any], dict[str, Any], Select[Any]]:
    scope = _scoped_matches(competition, season)
    events = _event_stats(scope, Event.team_id)
    participation = union_all(
        select(Match.home_team_id.label("team_id"), Match.id.label("match_id")).where(
            Match.id.in_(scope)
        ),
        select(Match.away_team_id.label("team_id"), Match.id.label("match_id")).where(
            Match.id.in_(scope)
        ),
    ).subquery()
    played = (
        select(
            participation.c.team_id.label("key"),
            func.count(func.distinct(participation.c.match_id)).label("matches_played"),
        )
        .group_by(participation.c.team_id)
        .subquery()
    )
    metrics = _metrics(events)
    metrics_with_matches = {"matches_played": func.coalesce(played.c.matches_played, 0), **metrics}
    statement = (
        select(
            Team.statsbomb_id.label("team_id"),
            Team.name.label("team_name"),
            *_labelled(metrics_with_matches),
        )
        .select_from(Team)
        .outerjoin(played, played.c.key == Team.id)
        .outerjoin(events, events.c.key == Team.id)
    )
    return statement, metrics_with_matches, select(participation.c.team_id)


def get_team_stats(
    session: Session, team: Team, competition: Competition, season: Season
) -> Row[Any]:
    """One team's season line; a team with no matches yields zeros, not no row."""
    statement, _, _ = _team_statement(competition, season)
    return session.execute(statement.where(Team.id == team.id)).one()


def list_team_stats(
    session: Session,
    competition: Competition,
    season: Season,
    *,
    sort_by: StatSortBy,
    order: SortOrder,
    limit: int,
    offset: int,
) -> tuple[list[Row[Any]], int]:
    statement, metrics, in_scope = _team_statement(competition, season)
    statement = statement.where(Team.id.in_(in_scope)).order_by(
        *_ordering(metrics, Team.statsbomb_id, sort_by, order)
    )
    return paginate_rows(session, statement, limit=limit, offset=offset)


def get_match_team_stats(session: Session, match: Match) -> list[Row[Any]]:
    """Per-team pass and shot totals for one match, using the season definitions.

    Both sides are always present, with zeros when a team has no pass or shot events.
    """
    scope = select(Match.id).where(Match.id == match.id)
    events = _event_stats(scope, Event.team_id)
    statement = (
        select(
            Team.statsbomb_id.label("team_id"),
            Team.name.label("team_name"),
            *_labelled(_metrics(events)),
        )
        .select_from(Team)
        .outerjoin(events, events.c.key == Team.id)
        .where(Team.id.in_((match.home_team_id, match.away_team_id)))
        .order_by(Team.statsbomb_id)
    )
    return list(session.execute(statement).all())
