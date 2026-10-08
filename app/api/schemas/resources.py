"""Resource response models."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, Field

from app.api.schemas.common import ORM_CONFIG, ExternalIdModel


class TeamRef(ExternalIdModel):
    """Minimal team reference embedded in other resources."""

    name: str


class PlayerRef(ExternalIdModel):
    """Minimal player reference embedded in other resources."""

    name: str


class CompetitionRead(ExternalIdModel):
    name: str
    country_name: str | None = None
    gender: str | None = None
    format: str | None = None


class SeasonRead(ExternalIdModel):
    name: str


class TeamRead(ExternalIdModel):
    name: str
    gender: str | None = None
    country_name: str | None = None


class PlayerRead(ExternalIdModel):
    name: str
    nickname: str | None = None
    country_name: str | None = None
    birth_date: date | None = None
    position_name: str | None = None


class MatchRead(ExternalIdModel):
    competition: CompetitionRead
    season: SeasonRead
    home_team: TeamRef
    away_team: TeamRef
    match_date: date
    kick_off: time | None = None
    home_score: int | None = None
    away_score: int | None = None
    stage_name: str | None = None
    status: str | None = None
    last_updated: datetime | None = None


class EventRead(BaseModel):
    model_config = ORM_CONFIG

    id: str = Field(validation_alias="statsbomb_id", description="StatsBomb event UUID.")
    index_in_match: int
    type_name: str
    period: int | None = None
    timestamp: time | None = None
    minute: int | None = None
    second: int | None = None
    possession: int | None = None
    possession_team: TeamRef | None = None
    team: TeamRef | None = None
    player: PlayerRef | None = None
    location_x: float | None = None
    location_y: float | None = None
    outcome_name: str | None = None
    raw_data: dict[str, Any] | None = None


class LineupRead(BaseModel):
    """A squad row, identified by its match, team, and player."""

    model_config = ORM_CONFIG

    team: TeamRef
    player: PlayerRef
    jersey_number: int | None = None
    position_name: str | None = None
    is_starter: bool | None = None
