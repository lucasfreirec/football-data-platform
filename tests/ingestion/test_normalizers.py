from datetime import date, datetime, time
from typing import Any

import pytest

from app.core.exceptions import ValidationError
from app.ingestion.normalizers import (
    normalize_competition_season,
    normalize_event,
    normalize_lineup_team,
    normalize_match,
)


class TestCompetition:
    def test_valid_record(self, raw_competitions: list[dict[str, Any]]) -> None:
        result = normalize_competition_season(raw_competitions[0])

        assert result.competition.statsbomb_id == 16
        assert result.competition.name == "Champions League"
        assert result.competition.country_name == "Europe"
        assert result.competition.gender == "male"
        assert result.competition.format is None
        assert result.season.statsbomb_id == 4
        assert result.season.name == "2017/2018"

    def test_missing_competition_id_rejected(self, raw_competitions: list[dict[str, Any]]) -> None:
        record = raw_competitions[0] | {"competition_id": None}

        with pytest.raises(ValidationError, match="competition_id"):
            normalize_competition_season(record)

    def test_missing_season_name_rejected(self, raw_competitions: list[dict[str, Any]]) -> None:
        record = raw_competitions[0] | {"season_name": ""}

        with pytest.raises(ValidationError, match="season_name"):
            normalize_competition_season(record)


class TestMatch:
    def test_valid_record(self, raw_matches: list[dict[str, Any]]) -> None:
        match = normalize_match(raw_matches[0])

        assert match.statsbomb_id == 7298
        assert match.competition.statsbomb_id == 16
        assert match.season.statsbomb_id == 4
        assert match.home_team.statsbomb_id == 220
        assert match.home_team.name == "Real Madrid"
        assert match.home_team.country_name == "Spain"
        assert match.home_team.gender == "male"
        assert match.away_team.statsbomb_id == 24
        assert match.match_date == date(2018, 5, 26)
        assert match.kick_off == time(21, 45)
        assert match.home_score == 3
        assert match.away_score == 1
        assert match.stage_name == "Final"
        assert match.status == "available"
        assert match.last_updated == datetime(2020, 7, 29, 5, 0)

    def test_optional_fields_default_to_none(self, raw_matches: list[dict[str, Any]]) -> None:
        record = {
            key: value
            for key, value in raw_matches[0].items()
            if key not in {"kick_off", "home_score", "away_score", "competition_stage"}
        }
        match = normalize_match(record)

        assert match.kick_off is None
        assert match.home_score is None
        assert match.away_score is None
        assert match.stage_name is None

    def test_malformed_match_date_rejected(self, raw_matches: list[dict[str, Any]]) -> None:
        with pytest.raises(ValidationError, match="match_date"):
            normalize_match(raw_matches[0] | {"match_date": "26-05-2018"})

    def test_missing_home_team_rejected(self, raw_matches: list[dict[str, Any]]) -> None:
        record = {key: value for key, value in raw_matches[0].items() if key != "home_team"}

        with pytest.raises(ValidationError, match="home_team"):
            normalize_match(record)


class TestEvent:
    def test_event_without_player_or_location(self, raw_events: list[dict[str, Any]]) -> None:
        event = normalize_event(raw_events[0])

        assert event.statsbomb_id == "0a1b2c3d-0000-4000-8000-000000000001"
        assert event.index_in_match == 1
        assert event.type_name == "Starting XI"
        assert event.period == 1
        assert event.timestamp == time(0, 0)
        assert event.team is not None and event.team.statsbomb_id == 220
        assert event.player is None
        assert event.location_x is None
        assert event.location_y is None
        assert event.outcome_name is None

    def test_pass_event_is_fully_normalized(self, raw_events: list[dict[str, Any]]) -> None:
        event = normalize_event(raw_events[1])

        assert event.type_name == "Pass"
        assert event.minute == 0
        assert event.second == 0
        assert event.possession == 2
        assert event.possession_team is not None
        assert event.possession_team.statsbomb_id == 24
        assert event.player is not None
        assert event.player.statsbomb_id == 3532
        assert event.player.position_name == "Right Center Forward"
        assert event.location_x == 60.0
        assert event.location_y == 40.1
        assert event.outcome_name == "Incomplete"

    def test_shot_outcome_is_extracted(self, raw_events: list[dict[str, Any]]) -> None:
        event = normalize_event(raw_events[2])

        assert event.type_name == "Shot"
        assert event.outcome_name == "Goal"
        assert event.timestamp == time(0, 6, 33, 100000)
        assert event.minute == 51

    def test_raw_data_keeps_a_small_useful_fragment(self, raw_events: list[dict[str, Any]]) -> None:
        event = normalize_event(raw_events[1])

        assert event.raw_data is not None
        assert event.raw_data["play_pattern"] == "From Kick Off"
        assert event.raw_data["under_pressure"] is True
        assert event.raw_data["detail_type"] == "pass"
        assert event.raw_data["detail"]["length"] == 10.5

    def test_tactics_payload_is_not_duplicated_into_raw_data(
        self, raw_events: list[dict[str, Any]]
    ) -> None:
        event = normalize_event(raw_events[0])

        assert event.raw_data is not None
        assert "detail" not in event.raw_data

    def test_malformed_location_rejected(self, raw_events: list[dict[str, Any]]) -> None:
        with pytest.raises(ValidationError, match="location"):
            normalize_event(raw_events[1] | {"location": [60.0, 40.0, 0.5]})

    def test_malformed_timestamp_rejected(self, raw_events: list[dict[str, Any]]) -> None:
        with pytest.raises(ValidationError, match="timestamp"):
            normalize_event(raw_events[1] | {"timestamp": "nope"})

    def test_missing_id_rejected(self, raw_events: list[dict[str, Any]]) -> None:
        with pytest.raises(ValidationError, match="event.id"):
            normalize_event(raw_events[1] | {"id": None})

    def test_missing_index_rejected(self, raw_events: list[dict[str, Any]]) -> None:
        record = {key: value for key, value in raw_events[1].items() if key != "index"}

        with pytest.raises(ValidationError, match="event.index"):
            normalize_event(record)


class TestLineup:
    def test_valid_team_block(self, raw_lineups: list[dict[str, Any]]) -> None:
        entries = normalize_lineup_team(raw_lineups[0])

        assert len(entries) == 3
        assert all(entry.team.statsbomb_id == 220 for entry in entries)

        keeper = entries[0]
        assert keeper.player.statsbomb_id == 3509
        assert keeper.player.nickname == "Keylor Navas"
        assert keeper.player.country_name == "Costa Rica"
        assert keeper.jersey_number == 1
        assert keeper.position_name == "Goalkeeper"
        assert keeper.is_starter is True

    def test_substitute_is_not_a_starter(self, raw_lineups: list[dict[str, Any]]) -> None:
        entries = normalize_lineup_team(raw_lineups[0])

        assert entries[2].player.statsbomb_id == 3639
        assert entries[2].is_starter is False

    def test_cards_and_positions_kept_in_raw_data(self, raw_lineups: list[dict[str, Any]]) -> None:
        entries = normalize_lineup_team(raw_lineups[1])
        mane = entries[1]

        assert mane.raw_data is not None
        assert mane.raw_data["cards"][0]["card_type"] == "Yellow Card"
        assert mane.raw_data["positions"][0]["position"] == "Left Center Forward"

    def test_player_without_positions_has_no_starter_flag(
        self, raw_lineups: list[dict[str, Any]]
    ) -> None:
        block = {
            "team_id": 220,
            "team_name": "Real Madrid",
            "lineup": [{"player_id": 1, "player_name": "Unused Sub", "jersey_number": 25}],
        }
        entries = normalize_lineup_team(block)

        assert entries[0].is_starter is None
        assert entries[0].position_name is None

    def test_missing_team_id_rejected(self, raw_lineups: list[dict[str, Any]]) -> None:
        with pytest.raises(ValidationError, match="team_id"):
            normalize_lineup_team(raw_lineups[0] | {"team_id": None})

    def test_non_array_lineup_rejected(self, raw_lineups: list[dict[str, Any]]) -> None:
        with pytest.raises(ValidationError, match="Expected an array"):
            normalize_lineup_team(raw_lineups[0] | {"lineup": {}})

    def test_missing_player_id_rejected(self) -> None:
        block = {
            "team_id": 220,
            "team_name": "Real Madrid",
            "lineup": [{"player_name": "No Id", "jersey_number": 25}],
        }

        with pytest.raises(ValidationError, match="player_id"):
            normalize_lineup_team(block)
