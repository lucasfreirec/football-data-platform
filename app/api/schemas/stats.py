"""Response models for season aggregates."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_PCT = "`passes_completed / passes_attempted * 100`; `null` when no passes were attempted."
_XG = "Sum of non-null StatsBomb xG over the shots; `0` when none carry xG."
_XG_PER_SHOT = "`xg / shots`; `null` when there are no shots."


class PlayerSeasonStats(BaseModel):
    """A player's totals for one competition season, derived from imported events."""

    model_config = ConfigDict(from_attributes=True)

    player_id: int = Field(description="StatsBomb player identifier.")
    player_name: str
    competition_id: int = Field(description="StatsBomb competition identifier.")
    season_id: int = Field(description="StatsBomb season identifier.")
    appearances: int = Field(description="Distinct matches with at least one lineup row.")
    passes_attempted: int = Field(description="Number of `Pass` events.")
    passes_completed: int = Field(description="Passes with no outcome recorded.")
    pass_completion_pct: float | None = Field(description=_PCT)
    shots: int = Field(description="Number of `Shot` events.")
    goals: int = Field(description="Shots with the outcome `Goal`.")
    xg: float = Field(description=_XG)
    xg_per_shot: float | None = Field(description=_XG_PER_SHOT)


class TeamSeasonStats(BaseModel):
    """A team's totals for one competition season, derived from imported events."""

    model_config = ConfigDict(from_attributes=True)

    team_id: int = Field(description="StatsBomb team identifier.")
    team_name: str
    competition_id: int = Field(description="StatsBomb competition identifier.")
    season_id: int = Field(description="StatsBomb season identifier.")
    matches_played: int = Field(description="Distinct imported matches the team took part in.")
    passes_attempted: int = Field(description="Number of `Pass` events.")
    passes_completed: int = Field(description="Passes with no outcome recorded.")
    pass_completion_pct: float | None = Field(description=_PCT)
    shots: int = Field(description="Number of `Shot` events.")
    goals: int = Field(description="Shots with the outcome `Goal`.")
    xg: float = Field(description=_XG)
    xg_per_shot: float | None = Field(description=_XG_PER_SHOT)
