"""Resolve path identifiers to rows, or raise the API's 404."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import Competition, Match, Player, Season, Team
from app.repositories import queries


def require_match(session: Session, match_id: int) -> Match:
    match = queries.get_match(session, match_id)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Match not found")
    return match


def require_competition(session: Session, competition_id: int) -> Competition:
    competition = queries.get_competition(session, competition_id)
    if competition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Competition not found")
    return competition


def require_season(session: Session, competition: Competition, season_id: int) -> Season:
    season = queries.get_season(session, competition, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Season not found")
    return season


def require_team(session: Session, team_id: int) -> Team:
    team = queries.get_team(session, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Team not found")
    return team


def require_player(session: Session, player_id: int) -> Player:
    player = queries.get_player(session, player_id)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Player not found")
    return player
