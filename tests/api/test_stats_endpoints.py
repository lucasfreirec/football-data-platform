import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService

CL = "/api/v1/competitions/16/seasons"
S1_PLAYERS = f"{CL}/1/player-stats"
S1_TEAMS = f"{CL}/1/team-stats"
S2_PLAYERS = f"{CL}/2/player-stats"
S2_TEAMS = f"{CL}/2/team-stats"


def _ids(body: dict[str, Any], key: str) -> list[int]:
    return [item[key] for item in body["items"]]


def _player(client: TestClient, player_id: int, competition: int = 16, season: int = 1) -> Any:
    return client.get(
        f"/api/v1/players/{player_id}/stats",
        params={"competition_id": competition, "season_id": season},
    )


def _team(client: TestClient, team_id: int, competition: int = 16, season: int = 1) -> Any:
    return client.get(
        f"/api/v1/teams/{team_id}/stats",
        params={"competition_id": competition, "season_id": season},
    )


class TestPlayerStatsCalculation:
    def test_every_field_for_a_player_with_passes_and_a_goal(self, v2_client: TestClient) -> None:
        body = _player(v2_client, 1001).json()

        assert body == {
            "player_id": 1001,
            "player_name": "Ana Alpha",
            "competition_id": 16,
            "season_id": 1,
            "appearances": 2,
            "passes_attempted": 2,
            "passes_completed": 1,
            "pass_completion_pct": 50.0,
            "shots": 1,
            "goals": 1,
            "xg": pytest.approx(0.25),
            "xg_per_shot": pytest.approx(0.25),
        }

    def test_a_perfect_passer_who_missed(self, v2_client: TestClient) -> None:
        body = _player(v2_client, 1002).json()

        assert (body["passes_attempted"], body["passes_completed"]) == (1, 1)
        assert body["pass_completion_pct"] == 100.0
        assert (body["shots"], body["goals"]) == (1, 0)
        assert body["xg"] == pytest.approx(0.10)

    def test_a_player_with_no_completed_passes_has_zero_percent_not_null(
        self, v2_client: TestClient
    ) -> None:
        body = _player(v2_client, 1003).json()

        assert (body["passes_attempted"], body["passes_completed"]) == (1, 0)
        assert body["pass_completion_pct"] == 0.0
        assert body["appearances"] == 1

    def test_a_shot_without_xg_counts_as_a_shot_but_adds_no_xg(self, v2_client: TestClient) -> None:
        body = _player(v2_client, 2001).json()

        assert body["shots"] == 2
        assert body["goals"] == 1
        assert body["xg"] == pytest.approx(0.5)
        assert body["xg_per_shot"] == pytest.approx(0.25)  # 0.5 / 2 shots, null-xG shot included

    def test_passes_attempted_and_completed_across_matches(self, v2_client: TestClient) -> None:
        body = _player(v2_client, 2001).json()

        assert (body["passes_attempted"], body["passes_completed"]) == (3, 3)
        assert body["pass_completion_pct"] == 100.0

    def test_rejected_records_do_not_count(self, v2_client: TestClient) -> None:
        # Eli's malformed pass (idx 10) and malformed shot (idx 13) were never imported.
        body = _player(v2_client, 2002).json()

        assert (body["passes_attempted"], body["shots"]) == (1, 1)
        assert body["xg"] == pytest.approx(0.5)

    def test_non_goal_outcomes_are_not_goals(self, v2_client: TestClient) -> None:
        assert _player(v2_client, 2002).json()["goals"] == 0  # Blocked
        assert _player(v2_client, 1003).json()["goals"] == 0  # Saved

    def test_appearances_count_distinct_matches_with_a_lineup_row(
        self, v2_client: TestClient
    ) -> None:
        assert _player(v2_client, 1001).json()["appearances"] == 2
        assert _player(v2_client, 1003).json()["appearances"] == 1


class TestZeroAndNullEdgeCases:
    def test_a_squad_player_with_no_events_has_zeros_and_null_ratios(
        self, v2_client: TestClient
    ) -> None:
        body = _player(v2_client, 2003).json()

        assert body["appearances"] == 2
        assert body["passes_attempted"] == 0
        assert body["passes_completed"] == 0
        assert body["pass_completion_pct"] is None
        assert body["shots"] == 0
        assert body["goals"] == 0
        assert body["xg"] == 0
        assert body["xg_per_shot"] is None

    def test_a_player_absent_from_the_season_gets_zeros_not_an_error(
        self, v2_client: TestClient
    ) -> None:
        response = _player(v2_client, 3001)  # Fay only played in season 2

        assert response.status_code == 200
        body = response.json()
        assert body["appearances"] == 0
        assert body["shots"] == 0
        assert body["pass_completion_pct"] is None
        assert body["xg_per_shot"] is None

    def test_a_team_absent_from_the_season_gets_zeros_not_an_error(
        self, v2_client: TestClient
    ) -> None:
        response = _team(v2_client, 102, season=2)  # Beta only played in season 1

        assert response.status_code == 200
        body = response.json()
        assert body["matches_played"] == 0
        assert body["passes_attempted"] == 0
        assert body["pass_completion_pct"] is None
        assert body["xg"] == 0
        assert body["xg_per_shot"] is None


class TestTeamStatsCalculation:
    def test_every_field_for_a_team(self, v2_client: TestClient) -> None:
        body = _team(v2_client, 101).json()

        assert body == {
            "team_id": 101,
            "team_name": "Alpha FC",
            "competition_id": 16,
            "season_id": 1,
            "matches_played": 2,
            "passes_attempted": 4,
            "passes_completed": 2,
            "pass_completion_pct": 50.0,
            "shots": 3,
            "goals": 1,
            "xg": pytest.approx(0.40),
            "xg_per_shot": pytest.approx(0.40 / 3),
        }

    def test_a_team_whose_shot_has_no_xg(self, v2_client: TestClient) -> None:
        body = _team(v2_client, 102).json()

        assert body["matches_played"] == 2
        assert (body["passes_attempted"], body["passes_completed"]) == (4, 3)
        assert body["pass_completion_pct"] == 75.0
        assert (body["shots"], body["goals"]) == (3, 1)
        assert body["xg"] == pytest.approx(1.0)
        assert body["xg_per_shot"] == pytest.approx(1.0 / 3)

    def test_a_team_with_shots_but_no_passes(self, v2_client: TestClient) -> None:
        body = _team(v2_client, 103, season=2).json()

        assert body["matches_played"] == 1
        assert body["passes_attempted"] == 0
        assert body["pass_completion_pct"] is None
        assert (body["shots"], body["goals"]) == (1, 0)
        assert body["xg_per_shot"] == pytest.approx(0.2)

    def test_home_and_away_matches_both_count_as_played(self, v2_client: TestClient) -> None:
        # Alpha is home in 9001 and away in 9002; Beta is the reverse.
        assert _team(v2_client, 101).json()["matches_played"] == 2
        assert _team(v2_client, 102).json()["matches_played"] == 2


class TestSeasonScoping:
    def test_a_players_stats_exclude_other_seasons(self, v2_client: TestClient) -> None:
        season1 = _player(v2_client, 1001, season=1).json()
        season2 = _player(v2_client, 1001, season=2).json()

        assert season1["shots"] == 1
        assert season1["xg"] == pytest.approx(0.25)
        assert season1["appearances"] == 2
        assert season2["shots"] == 1
        assert season2["xg"] == pytest.approx(0.9)
        assert season2["appearances"] == 1
        assert (season2["passes_attempted"], season2["passes_completed"]) == (2, 2)

    def test_a_teams_stats_exclude_other_seasons(self, v2_client: TestClient) -> None:
        season1 = _team(v2_client, 101, season=1).json()
        season2 = _team(v2_client, 101, season=2).json()

        assert season1["matches_played"] == 2
        assert season2["matches_played"] == 1
        assert season2["xg"] == pytest.approx(0.9)
        assert season2["goals"] == 1

    def test_leaderboards_list_only_participants_of_that_season(
        self, v2_client: TestClient
    ) -> None:
        season1 = set(_ids(v2_client.get(S1_PLAYERS).json(), "player_id"))
        season2 = set(_ids(v2_client.get(S2_PLAYERS).json(), "player_id"))

        assert season1 == {1001, 1002, 1003, 2001, 2002, 2003}
        assert season2 == {1001, 1002, 3001}

    def test_team_leaderboards_list_only_teams_of_that_season(self, v2_client: TestClient) -> None:
        assert set(_ids(v2_client.get(S1_TEAMS).json(), "team_id")) == {101, 102}
        assert set(_ids(v2_client.get(S2_TEAMS).json(), "team_id")) == {101, 103}

    def test_leaderboard_figures_match_the_single_resource_endpoints(
        self, v2_client: TestClient
    ) -> None:
        for item in v2_client.get(S1_PLAYERS).json()["items"]:
            assert item == _player(v2_client, item["player_id"]).json()
        for item in v2_client.get(S1_TEAMS).json()["items"]:
            assert item == _team(v2_client, item["team_id"]).json()


@pytest.fixture
def two_competitions(
    client: TestClient, session: Session, tmp_path: Path, v2_source_dir: Path
) -> TestClient:
    """Adds a second competition that reuses season id 1, to catch season-only filtering."""
    source = tmp_path / "raw"
    shutil.copytree(v2_source_dir, source)

    competitions_file = source / "competitions.json"
    competitions = json.loads(competitions_file.read_text())
    competitions.append(
        {
            "competition_id": 2,
            "season_id": 1,
            "country_name": "England",
            "competition_name": "Premier League",
            "competition_gender": "male",
            "season_name": "2017/2018",
        }
    )
    competitions_file.write_text(json.dumps(competitions))

    (source / "matches" / "2").mkdir(parents=True)
    (source / "matches" / "2" / "1.json").write_text(
        json.dumps(
            [
                {
                    "match_id": 9004,
                    "match_date": "2018-04-01",
                    "competition": {
                        "competition_id": 2,
                        "country_name": "England",
                        "competition_name": "Premier League",
                    },
                    "season": {"season_id": 1, "season_name": "2017/2018"},
                    "home_team": {"home_team_id": 101, "home_team_name": "Alpha FC"},
                    "away_team": {"away_team_id": 102, "away_team_name": "Beta FC"},
                }
            ]
        )
    )
    (source / "events" / "9004.json").write_text(
        json.dumps(
            [
                {
                    "id": "90040000-0000-4000-8000-000000000001",
                    "index": 1,
                    "type": {"name": "Pass"},
                    "team": {"id": 101, "name": "Alpha FC"},
                    "player": {"id": 1001, "name": "Ana Alpha"},
                    "pass": {"length": 5.0},
                },
                {
                    "id": "90040000-0000-4000-8000-000000000002",
                    "index": 2,
                    "type": {"name": "Shot"},
                    "team": {"id": 101, "name": "Alpha FC"},
                    "player": {"id": 1001, "name": "Ana Alpha"},
                    "shot": {"statsbomb_xg": 0.7, "outcome": {"name": "Goal"}},
                },
                {
                    "id": "90040000-0000-4000-8000-000000000003",
                    "index": 3,
                    "type": {"name": "Shot"},
                    "team": {"id": 101, "name": "Alpha FC"},
                    "player": {"id": 4001, "name": "Hal Alpha"},
                    "shot": {"outcome": {"name": "Off T"}},
                },
            ]
        )
    )
    (source / "lineups" / "9004.json").write_text(
        json.dumps(
            [
                {
                    "team_id": 101,
                    "team_name": "Alpha FC",
                    "lineup": [
                        {"player_id": 1001, "player_name": "Ana Alpha"},
                        {"player_id": 4001, "player_name": "Hal Alpha"},
                    ],
                }
            ]
        )
    )

    IngestionService(SourceLoader(source), session).run()
    IngestionService(SourceLoader(source), session, season_name="2016/2017").run()
    IngestionService(SourceLoader(source), session, competition_name="Premier League").run()
    session.commit()
    return client


class TestCompetitionScoping:
    def test_a_season_id_shared_by_two_competitions_is_not_mixed(
        self, two_competitions: TestClient
    ) -> None:
        champions = _player(two_competitions, 1001, competition=16, season=1).json()
        premier = _player(two_competitions, 1001, competition=2, season=1).json()

        assert champions["shots"] == 1
        assert champions["xg"] == pytest.approx(0.25)
        assert champions["appearances"] == 2
        assert premier["shots"] == 1
        assert premier["xg"] == pytest.approx(0.7)
        assert premier["appearances"] == 1
        assert premier["competition_id"] == 2

    def test_team_stats_are_scoped_to_the_competition(self, two_competitions: TestClient) -> None:
        champions = _team(two_competitions, 101, competition=16, season=1).json()
        premier = _team(two_competitions, 101, competition=2, season=1).json()

        assert champions["matches_played"] == 2
        assert champions["shots"] == 3
        assert premier["matches_played"] == 1
        assert premier["shots"] == 2
        assert premier["goals"] == 1
        assert premier["xg"] == pytest.approx(0.7)

    def test_leaderboards_do_not_leak_players_across_competitions(
        self, two_competitions: TestClient
    ) -> None:
        champions = set(_ids(two_competitions.get(S1_PLAYERS).json(), "player_id"))
        premier = set(
            _ids(
                two_competitions.get("/api/v1/competitions/2/seasons/1/player-stats").json(),
                "player_id",
            )
        )

        assert 4001 not in champions
        assert premier == {1001, 4001}

    def test_a_shot_without_xg_is_zero_xg_and_zero_xg_per_shot(
        self, two_competitions: TestClient
    ) -> None:
        body = _player(two_competitions, 4001, competition=2, season=1).json()

        assert body["shots"] == 1
        assert body["xg"] == 0
        assert body["xg_per_shot"] == 0.0
        assert body["pass_completion_pct"] is None


class TestPlayerLeaderboardOrdering:
    @pytest.mark.parametrize(
        ("sort_by", "order", "expected"),
        [
            ("xg", "desc", [2001, 2002, 1001, 1002, 1003, 2003]),
            ("xg", "asc", [2003, 1003, 1002, 1001, 2001, 2002]),
            ("goals", "desc", [1001, 2001, 1002, 1003, 2002, 2003]),
            ("goals", "asc", [1002, 1003, 2002, 2003, 1001, 2001]),
            ("shots", "desc", [2001, 1001, 1002, 1003, 2002, 2003]),
            ("shots", "asc", [2003, 1001, 1002, 1003, 2002, 2001]),
            ("passes_completed", "desc", [2001, 1001, 1002, 1003, 2002, 2003]),
            ("passes_completed", "asc", [1003, 2002, 2003, 1001, 1002, 2001]),
            ("pass_completion_pct", "desc", [1002, 2001, 1001, 1003, 2002, 2003]),
            ("pass_completion_pct", "asc", [1003, 2002, 1001, 1002, 2001, 2003]),
        ],
    )
    def test_ordering_with_stable_tie_breaks(
        self, v2_client: TestClient, sort_by: str, order: str, expected: list[int]
    ) -> None:
        body = v2_client.get(S1_PLAYERS, params={"sort_by": sort_by, "order": order}).json()

        assert _ids(body, "player_id") == expected

    def test_default_order_is_xg_descending(self, v2_client: TestClient) -> None:
        default = v2_client.get(S1_PLAYERS).json()
        explicit = v2_client.get(S1_PLAYERS, params={"sort_by": "xg", "order": "desc"}).json()

        assert default == explicit

    def test_null_percentages_sort_last_in_both_directions(self, v2_client: TestClient) -> None:
        for order in ("asc", "desc"):
            body = v2_client.get(
                S1_PLAYERS, params={"sort_by": "pass_completion_pct", "order": order}
            ).json()

            assert body["items"][-1]["player_id"] == 2003
            assert body["items"][-1]["pass_completion_pct"] is None

    def test_ties_are_broken_by_ascending_id_even_when_descending(
        self, v2_client: TestClient
    ) -> None:
        body = v2_client.get(S1_PLAYERS, params={"sort_by": "xg", "order": "desc"}).json()

        # Dan (2001) and Eli (2002) both have 0.5 xG.
        assert _ids(body, "player_id")[:2] == [2001, 2002]


class TestTeamLeaderboardOrdering:
    @pytest.mark.parametrize(
        ("sort_by", "order", "expected"),
        [
            ("xg", "desc", [102, 101]),
            ("xg", "asc", [101, 102]),
            ("goals", "desc", [101, 102]),  # 1-1 tie, id ascending
            ("goals", "asc", [101, 102]),
            ("shots", "desc", [101, 102]),  # 3-3 tie
            ("passes_completed", "desc", [102, 101]),
            ("passes_completed", "asc", [101, 102]),
            ("pass_completion_pct", "desc", [102, 101]),
            ("pass_completion_pct", "asc", [101, 102]),
        ],
    )
    def test_ordering(
        self, v2_client: TestClient, sort_by: str, order: str, expected: list[int]
    ) -> None:
        body = v2_client.get(S1_TEAMS, params={"sort_by": sort_by, "order": order}).json()

        assert _ids(body, "team_id") == expected

    @pytest.mark.parametrize("order", ["asc", "desc"])
    def test_a_null_percentage_sorts_last_in_both_directions(
        self, v2_client: TestClient, order: str
    ) -> None:
        body = v2_client.get(
            S2_TEAMS, params={"sort_by": "pass_completion_pct", "order": order}
        ).json()

        assert _ids(body, "team_id") == [101, 103]  # Gamma attempted no passes
        assert body["items"][-1]["pass_completion_pct"] is None


class TestLeaderboardPagination:
    def test_envelope(self, v2_client: TestClient) -> None:
        body = v2_client.get(S1_PLAYERS).json()

        assert set(body) == {"items", "limit", "offset", "total"}
        assert (body["limit"], body["offset"], body["total"]) == (50, 0, 6)

    def test_pages_are_a_partition_with_no_duplicates_or_gaps(self, v2_client: TestClient) -> None:
        for sort_by in ("xg", "goals", "shots", "passes_completed", "pass_completion_pct"):
            full = _ids(v2_client.get(S1_PLAYERS, params={"sort_by": sort_by}).json(), "player_id")
            paged: list[int] = []
            for offset in (0, 2, 4):
                page = v2_client.get(
                    S1_PLAYERS, params={"sort_by": sort_by, "limit": 2, "offset": offset}
                ).json()
                assert page["total"] == 6
                paged.extend(_ids(page, "player_id"))

            assert paged == full
            assert len(set(paged)) == 6

    def test_team_pages_partition_too(self, v2_client: TestClient) -> None:
        first = v2_client.get(S1_TEAMS, params={"limit": 1, "offset": 0}).json()
        second = v2_client.get(S1_TEAMS, params={"limit": 1, "offset": 1}).json()

        assert _ids(first, "team_id") + _ids(second, "team_id") == [102, 101]

    def test_offset_past_the_end_is_an_empty_page_not_an_error(self, v2_client: TestClient) -> None:
        response = v2_client.get(S1_PLAYERS, params={"offset": 99})

        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 6

    def test_a_season_with_no_matches_is_an_empty_page(
        self, client: TestClient, session: Session, tmp_path: Path, v2_source_dir: Path
    ) -> None:
        source = tmp_path / "raw"
        shutil.copytree(v2_source_dir, source)
        (source / "matches" / "16" / "1.json").write_text("[]")
        IngestionService(SourceLoader(source), session).run()
        session.commit()

        players = client.get(S1_PLAYERS)
        teams = client.get(S1_TEAMS)

        assert players.status_code == teams.status_code == 200
        assert players.json() == {"items": [], "limit": 50, "offset": 0, "total": 0}
        assert teams.json() == {"items": [], "limit": 50, "offset": 0, "total": 0}

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_invalid_pagination_is_422(self, v2_client: TestClient, params: dict[str, int]) -> None:
        assert v2_client.get(S1_PLAYERS, params=params).status_code == 422
        assert v2_client.get(S1_TEAMS, params=params).status_code == 422

    @pytest.mark.parametrize(
        "params", [{"sort_by": "assists"}, {"sort_by": "XG"}, {"order": "up"}, {"order": ""}]
    )
    def test_invalid_sort_options_are_422(
        self, v2_client: TestClient, params: dict[str, str]
    ) -> None:
        assert v2_client.get(S1_PLAYERS, params=params).status_code == 422
        assert v2_client.get(S1_TEAMS, params=params).status_code == 422

    def test_xg_per_shot_is_not_a_sort_option(self, v2_client: TestClient) -> None:
        response = v2_client.get(S1_PLAYERS, params={"sort_by": "xg_per_shot"})

        assert response.status_code == 422


class TestNotFoundAndValidation:
    def test_unknown_player_is_404(self, v2_client: TestClient) -> None:
        response = _player(v2_client, 999999)

        assert response.status_code == 404
        assert response.json() == {"detail": "Player not found"}

    def test_unknown_team_is_404(self, v2_client: TestClient) -> None:
        response = _team(v2_client, 999999)

        assert response.status_code == 404
        assert response.json() == {"detail": "Team not found"}

    def test_unknown_competition_is_404_for_every_route(self, v2_client: TestClient) -> None:
        responses = [
            _player(v2_client, 1001, competition=999),
            _team(v2_client, 101, competition=999),
            v2_client.get("/api/v1/competitions/999/seasons/1/player-stats"),
            v2_client.get("/api/v1/competitions/999/seasons/1/team-stats"),
        ]

        for response in responses:
            assert response.status_code == 404
            assert response.json() == {"detail": "Competition not found"}

    def test_unknown_season_is_404_for_every_route(self, v2_client: TestClient) -> None:
        responses = [
            _player(v2_client, 1001, season=999),
            _team(v2_client, 101, season=999),
            v2_client.get(f"{CL}/999/player-stats"),
            v2_client.get(f"{CL}/999/team-stats"),
        ]

        for response in responses:
            assert response.status_code == 404
            assert response.json() == {"detail": "Season not found"}

    def test_a_season_of_a_different_competition_is_404(self, two_competitions: TestClient) -> None:
        # Competition 2 only has season 1; season 2 belongs to competition 16.
        assert _player(two_competitions, 1001, competition=2, season=2).status_code == 404

    @pytest.mark.parametrize(
        "params",
        [{}, {"competition_id": 16}, {"season_id": 1}, {"competition_id": "x", "season_id": 1}],
    )
    def test_competition_and_season_are_required_for_resource_stats(
        self, v2_client: TestClient, params: dict[str, object]
    ) -> None:
        assert v2_client.get("/api/v1/players/1001/stats", params=params).status_code == 422
        assert v2_client.get("/api/v1/teams/101/stats", params=params).status_code == 422

    def test_non_integer_path_ids_are_422(self, v2_client: TestClient) -> None:
        assert v2_client.get("/api/v1/players/abc/stats").status_code == 422
        assert v2_client.get(f"{CL}/abc/player-stats").status_code == 422


class TestDocumentation:
    def test_routes_and_enums_are_in_the_openapi_document(self, v2_client: TestClient) -> None:
        document = v2_client.get("/openapi.json").json()
        paths = document["paths"]

        assert paths["/api/v1/players/{player_id}/stats"]["get"]["summary"] == (
            "Get a player's season statistics"
        )
        assert "/api/v1/teams/{team_id}/stats" in paths
        assert "/api/v1/competitions/{competition_id}/seasons/{season_id}/player-stats" in paths
        assert "/api/v1/competitions/{competition_id}/seasons/{season_id}/team-stats" in paths
        assert document["components"]["schemas"]["StatSortBy"]["enum"] == [
            "xg",
            "goals",
            "shots",
            "passes_completed",
            "pass_completion_pct",
        ]
        assert document["components"]["schemas"]["SortOrder"]["enum"] == ["asc", "desc"]

    def test_the_unofficial_disclaimer_is_documented(self, v2_client: TestClient) -> None:
        paths = v2_client.get("/openapi.json").json()["paths"]
        description = paths["/api/v1/players/{player_id}/stats"]["get"]["description"]

        assert "not an official StatsBomb analytical product" in description


class TestQueryCount:
    @staticmethod
    def _statements(engine: Engine, client: TestClient, url: str, **params: object) -> int:
        seen: list[str] = []

        def record(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert client.get(url, params=params).status_code == 200  # type: ignore[arg-type]
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    @pytest.mark.parametrize("url", [S1_PLAYERS, S1_TEAMS])
    def test_a_leaderboard_page_costs_a_fixed_number_of_queries(
        self, v2_client: TestClient, engine: Engine, url: str
    ) -> None:
        one = self._statements(engine, v2_client, url, limit=1)
        many = self._statements(engine, v2_client, url, limit=50)

        assert one == many
        assert many <= 5  # two lookups, a count, and the page
