"""Mapping from raw StatsBomb JSON into normalized, validated records.

Everything source-specific lives here; the persistence layer only sees the
``Normalized*`` dataclasses below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from app.core.exceptions import ValidationError
from app.ingestion.validators import (
    optional_bool,
    optional_int,
    optional_str,
    parse_date,
    parse_location,
    parse_optional_date,
    parse_optional_datetime,
    parse_optional_time,
    require_int,
    require_mapping,
    require_str,
)

# StatsBomb nests type-specific attributes under one of these keys. "tactics" is
# deliberately excluded: its payload duplicates the lineup data.
_EVENT_DETAIL_KEYS = (
    "pass",
    "shot",
    "dribble",
    "duel",
    "interception",
    "ball_receipt",
    "ball_recovery",
    "block",
    "carry",
    "clearance",
    "foul_committed",
    "foul_won",
    "goalkeeper",
    "miscontrol",
    "substitution",
    "50_50",
    "bad_behaviour",
    "player_off",
    "injury_stoppage",
)


@dataclass(frozen=True, slots=True)
class NormalizedCompetition:
    statsbomb_id: int
    name: str
    country_name: str | None = None
    gender: str | None = None
    format: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedSeason:
    statsbomb_id: int
    name: str


@dataclass(frozen=True, slots=True)
class NormalizedCompetitionSeason:
    competition: NormalizedCompetition
    season: NormalizedSeason


@dataclass(frozen=True, slots=True)
class NormalizedTeam:
    statsbomb_id: int
    name: str
    gender: str | None = None
    country_name: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedPlayer:
    statsbomb_id: int
    name: str
    nickname: str | None = None
    country_name: str | None = None
    birth_date: date | None = None
    position_name: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedMatch:
    statsbomb_id: int
    competition: NormalizedCompetition
    season: NormalizedSeason
    home_team: NormalizedTeam
    away_team: NormalizedTeam
    match_date: date
    kick_off: time | None = None
    home_score: int | None = None
    away_score: int | None = None
    stage_name: str | None = None
    status: str | None = None
    last_updated: datetime | None = None


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    statsbomb_id: str
    index_in_match: int
    type_name: str
    period: int | None = None
    timestamp: time | None = None
    minute: int | None = None
    second: int | None = None
    possession: int | None = None
    possession_team: NormalizedTeam | None = None
    team: NormalizedTeam | None = None
    player: NormalizedPlayer | None = None
    location_x: float | None = None
    location_y: float | None = None
    outcome_name: str | None = None
    raw_data: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class NormalizedLineupEntry:
    team: NormalizedTeam
    player: NormalizedPlayer
    jersey_number: int | None = None
    position_name: str | None = None
    is_starter: bool | None = None
    raw_data: dict[str, Any] | None = None


def normalize_competition_season(raw: Any) -> NormalizedCompetitionSeason:
    """Normalize one entry of ``competitions.json``."""
    record = require_mapping(raw, "competition")
    competition = NormalizedCompetition(
        statsbomb_id=require_int(record.get("competition_id"), "competition_id"),
        name=require_str(record.get("competition_name"), "competition_name"),
        country_name=optional_str(record.get("country_name"), "country_name"),
        gender=optional_str(record.get("competition_gender"), "competition_gender"),
        format=optional_str(record.get("competition_format"), "competition_format"),
    )
    season = NormalizedSeason(
        statsbomb_id=require_int(record.get("season_id"), "season_id"),
        name=require_str(record.get("season_name"), "season_name"),
    )
    return NormalizedCompetitionSeason(competition=competition, season=season)


def normalize_match(raw: Any) -> NormalizedMatch:
    """Normalize one entry of ``matches/<competition_id>/<season_id>.json``."""
    record = require_mapping(raw, "match")
    competition_raw = require_mapping(record.get("competition"), "match.competition")
    season_raw = require_mapping(record.get("season"), "match.season")

    competition = NormalizedCompetition(
        statsbomb_id=require_int(competition_raw.get("competition_id"), "competition.id"),
        name=require_str(competition_raw.get("competition_name"), "competition.name"),
        country_name=optional_str(competition_raw.get("country_name"), "competition.country"),
    )
    season = NormalizedSeason(
        statsbomb_id=require_int(season_raw.get("season_id"), "season.id"),
        name=require_str(season_raw.get("season_name"), "season.name"),
    )
    stage_raw = record.get("competition_stage")
    stage_name = (
        optional_str(require_mapping(stage_raw, "competition_stage").get("name"), "stage.name")
        if stage_raw is not None
        else None
    )

    return NormalizedMatch(
        statsbomb_id=require_int(record.get("match_id"), "match_id"),
        competition=competition,
        season=season,
        home_team=_normalize_match_team(record, "home"),
        away_team=_normalize_match_team(record, "away"),
        match_date=parse_date(record.get("match_date"), "match_date"),
        kick_off=parse_optional_time(record.get("kick_off"), "kick_off"),
        home_score=optional_int(record.get("home_score"), "home_score"),
        away_score=optional_int(record.get("away_score"), "away_score"),
        stage_name=stage_name,
        status=optional_str(record.get("match_status"), "match_status"),
        last_updated=parse_optional_datetime(record.get("last_updated"), "last_updated"),
    )


def normalize_event(raw: Any) -> NormalizedEvent:
    """Normalize one entry of ``events/<match_id>.json``."""
    record = require_mapping(raw, "event")
    type_raw = require_mapping(record.get("type"), "event.type")
    location_x, location_y = parse_location(record.get("location"))
    detail_key, detail = _event_detail(record)

    return NormalizedEvent(
        statsbomb_id=require_str(record.get("id"), "event.id"),
        index_in_match=require_int(record.get("index"), "event.index"),
        type_name=require_str(type_raw.get("name"), "event.type.name"),
        period=optional_int(record.get("period"), "event.period"),
        timestamp=parse_optional_time(record.get("timestamp"), "event.timestamp"),
        minute=optional_int(record.get("minute"), "event.minute"),
        second=optional_int(record.get("second"), "event.second"),
        possession=optional_int(record.get("possession"), "event.possession"),
        possession_team=_normalize_reference_team(
            record.get("possession_team"), "event.possession_team"
        ),
        team=_normalize_reference_team(record.get("team"), "event.team"),
        player=_normalize_event_player(record),
        location_x=location_x,
        location_y=location_y,
        outcome_name=_detail_outcome(detail),
        raw_data=_event_raw_data(record, detail_key, detail),
    )


def normalize_lineup_team(raw: Any) -> list[NormalizedLineupEntry]:
    """Normalize one team block of ``lineups/<match_id>.json``.

    Raises :class:`ValidationError` for an invalid team block; individual player
    entries that fail validation propagate so the caller can skip just that row.
    """
    record = require_mapping(raw, "lineup")
    team = NormalizedTeam(
        statsbomb_id=require_int(record.get("team_id"), "lineup.team_id"),
        name=require_str(record.get("team_name"), "lineup.team_name"),
    )
    players = record.get("lineup")
    if not isinstance(players, list):
        raise ValidationError(f"Expected an array for 'lineup.lineup' of team {team.statsbomb_id}")

    return [normalize_lineup_entry(team, entry) for entry in players]


def normalize_lineup_entry(team: NormalizedTeam, raw: Any) -> NormalizedLineupEntry:
    record = require_mapping(raw, "lineup.player")
    positions = record.get("positions")
    if positions is not None and not isinstance(positions, list):
        raise ValidationError("Expected an array for 'lineup.player.positions'")
    first_position = (
        require_mapping(positions[0], "lineup.player.positions[0]") if positions else None
    )

    player = NormalizedPlayer(
        statsbomb_id=require_int(record.get("player_id"), "lineup.player_id"),
        name=require_str(record.get("player_name"), "lineup.player_name"),
        nickname=optional_str(record.get("player_nickname"), "lineup.player_nickname"),
        country_name=_country_name(record.get("country")),
        birth_date=parse_optional_date(record.get("birth_date"), "lineup.birth_date"),
        position_name=optional_str(
            first_position.get("position") if first_position else None, "lineup.position"
        ),
    )
    raw_data = _compact(
        {
            "cards": record.get("cards") or None,
            "positions": positions or None,
        }
    )

    return NormalizedLineupEntry(
        team=team,
        player=player,
        jersey_number=optional_int(record.get("jersey_number"), "lineup.jersey_number"),
        position_name=player.position_name,
        is_starter=_is_starter(first_position),
        raw_data=raw_data,
    )


def _normalize_match_team(record: dict[str, Any], side: str) -> NormalizedTeam:
    team_raw = require_mapping(record.get(f"{side}_team"), f"match.{side}_team")
    return NormalizedTeam(
        statsbomb_id=require_int(team_raw.get(f"{side}_team_id"), f"{side}_team_id"),
        name=require_str(team_raw.get(f"{side}_team_name"), f"{side}_team_name"),
        gender=optional_str(team_raw.get(f"{side}_team_gender"), f"{side}_team_gender"),
        country_name=_country_name(team_raw.get("country")),
    )


def _normalize_reference_team(raw: Any, field: str) -> NormalizedTeam | None:
    if raw is None:
        return None
    team_raw = require_mapping(raw, field)
    return NormalizedTeam(
        statsbomb_id=require_int(team_raw.get("id"), f"{field}.id"),
        name=require_str(team_raw.get("name"), f"{field}.name"),
    )


def _normalize_event_player(record: dict[str, Any]) -> NormalizedPlayer | None:
    raw = record.get("player")
    if raw is None:
        return None
    player_raw = require_mapping(raw, "event.player")
    position_raw = record.get("position")
    return NormalizedPlayer(
        statsbomb_id=require_int(player_raw.get("id"), "event.player.id"),
        name=require_str(player_raw.get("name"), "event.player.name"),
        position_name=optional_str(
            require_mapping(position_raw, "event.position").get("name")
            if position_raw is not None
            else None,
            "event.position.name",
        ),
    )


def _event_detail(record: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    for key in _EVENT_DETAIL_KEYS:
        value = record.get(key)
        if isinstance(value, dict):
            return key, value
    return None, None


def _detail_outcome(detail: dict[str, Any] | None) -> str | None:
    if not detail:
        return None
    outcome = detail.get("outcome")
    if not isinstance(outcome, dict):
        return None
    return optional_str(outcome.get("name"), "outcome.name")


def _event_raw_data(
    record: dict[str, Any], detail_key: str | None, detail: dict[str, Any] | None
) -> dict[str, Any] | None:
    play_pattern = record.get("play_pattern")
    position = record.get("position")
    return _compact(
        {
            "play_pattern": play_pattern.get("name") if isinstance(play_pattern, dict) else None,
            "position": position.get("name") if isinstance(position, dict) else None,
            "duration": record.get("duration"),
            "under_pressure": record.get("under_pressure"),
            "out": record.get("out"),
            "off_camera": record.get("off_camera"),
            "detail_type": detail_key,
            "detail": detail,
        }
    )


def _is_starter(first_position: dict[str, Any] | None) -> bool | None:
    if first_position is None:
        return None
    start_reason = optional_str(first_position.get("start_reason"), "positions[0].start_reason")
    if start_reason is not None:
        return start_reason == "Starting XI"
    return optional_bool(first_position.get("start_reason"), "positions[0].start_reason")


def _country_name(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return optional_str(raw, "country")
    return optional_str(require_mapping(raw, "country").get("name"), "country.name")


def _compact(values: dict[str, Any]) -> dict[str, Any] | None:
    cleaned = {key: value for key, value in values.items() if value is not None}
    return cleaned or None
