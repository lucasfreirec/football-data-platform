import pytest
from fastapi.testclient import TestClient


class TestMatches:
    def test_list(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/matches").json()

        assert body["total"] == 2
        assert [item["id"] for item in body["items"]] == [7298, 7299]

    def test_match_payload(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/matches/7298").json()

        assert body["id"] == 7298
        assert body["match_date"] == "2018-05-26"
        assert body["kick_off"] == "21:45:00"
        assert body["home_score"] == 3
        assert body["away_score"] == 1
        assert body["stage_name"] == "Final"
        assert body["competition"]["id"] == 16
        assert body["season"]["id"] == 1
        assert body["home_team"] == {"id": 220, "name": "Real Madrid"}
        assert body["away_team"] == {"id": 24, "name": "Liverpool"}

    def test_unknown_match_returns_404(self, seeded_client: TestClient) -> None:
        response = seeded_client.get("/api/v1/matches/999999")

        assert response.status_code == 404
        assert response.json() == {"detail": "Match not found"}

    def test_filter_by_competition(self, seeded_client: TestClient) -> None:
        assert (
            seeded_client.get("/api/v1/matches", params={"competition_id": 16}).json()["total"] == 2
        )
        assert (
            seeded_client.get("/api/v1/matches", params={"competition_id": 2}).json()["total"] == 0
        )

    def test_filter_by_season(self, seeded_client: TestClient) -> None:
        assert seeded_client.get("/api/v1/matches", params={"season_id": 1}).json()["total"] == 2
        assert seeded_client.get("/api/v1/matches", params={"season_id": 4}).json()["total"] == 0

    def test_filter_by_team_matches_home_or_away(self, seeded_client: TestClient) -> None:
        # Real Madrid plays both fixtures, Liverpool only the final.
        assert seeded_client.get("/api/v1/matches", params={"team_id": 220}).json()["total"] == 2
        assert seeded_client.get("/api/v1/matches", params={"team_id": 24}).json()["total"] == 1
        assert seeded_client.get("/api/v1/matches", params={"team_id": 217}).json()["total"] == 1

    def test_filter_by_date(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/matches", params={"match_date": "2018-05-26"}).json()

        assert body["total"] == 1
        assert body["items"][0]["id"] == 7298

    def test_combined_filters(self, seeded_client: TestClient) -> None:
        body = seeded_client.get(
            "/api/v1/matches", params={"competition_id": 16, "season_id": 1, "team_id": 24}
        ).json()

        assert body["total"] == 1

    def test_malformed_date_returns_422(self, seeded_client: TestClient) -> None:
        assert (
            seeded_client.get("/api/v1/matches", params={"match_date": "26-05-2018"}).status_code
            == 422
        )

    def test_non_integer_path_id_returns_422(self, seeded_client: TestClient) -> None:
        assert seeded_client.get("/api/v1/matches/abc").status_code == 422


class TestMatchEvents:
    def test_list_is_ordered_by_source_index(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/matches/7298/events").json()

        assert body["total"] == 3
        assert [item["index_in_match"] for item in body["items"]] == [1, 2, 3]

    def test_event_payload(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/matches/7298/events", params={"type_name": "Shot"}).json()
        event = body["items"][0]

        assert event["id"] == "0a1b2c3d-0000-4000-8000-000000000003"
        assert event["type_name"] == "Shot"
        assert event["outcome_name"] == "Goal"
        assert event["period"] == 2
        assert event["minute"] == 51
        assert event["location_x"] == 112.0
        assert event["location_y"] == 38.0
        assert event["team"] == {"id": 220, "name": "Real Madrid"}
        assert event["player"] == {"id": 3604, "name": "Karim Benzema"}

    def test_filter_by_type_name(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/matches/7298/events", params={"type_name": "Pass"}).json()

        assert body["total"] == 1

    def test_filter_by_team(self, seeded_client: TestClient) -> None:
        assert (
            seeded_client.get("/api/v1/matches/7298/events", params={"team_id": 24}).json()["total"]
            == 1
        )

    def test_filter_by_player(self, seeded_client: TestClient) -> None:
        assert (
            seeded_client.get("/api/v1/matches/7298/events", params={"player_id": 3604}).json()[
                "total"
            ]
            == 1
        )

    def test_filter_by_period(self, seeded_client: TestClient) -> None:
        assert (
            seeded_client.get("/api/v1/matches/7298/events", params={"period": 1}).json()["total"]
            == 2
        )

    def test_unknown_filter_value_returns_empty_page(self, seeded_client: TestClient) -> None:
        body = seeded_client.get(
            "/api/v1/matches/7298/events", params={"type_name": "Nonexistent"}
        ).json()

        assert body["items"] == []
        assert body["total"] == 0

    def test_pagination(self, seeded_client: TestClient) -> None:
        body = seeded_client.get(
            "/api/v1/matches/7298/events", params={"limit": 2, "offset": 1}
        ).json()

        assert body["total"] == 3
        assert [item["index_in_match"] for item in body["items"]] == [2, 3]

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"period": "first"}])
    def test_invalid_parameters_return_422(
        self, seeded_client: TestClient, params: dict[str, object]
    ) -> None:
        assert seeded_client.get("/api/v1/matches/7298/events", params=params).status_code == 422

    def test_events_for_unknown_match_returns_404(self, seeded_client: TestClient) -> None:
        assert seeded_client.get("/api/v1/matches/999999/events").status_code == 404


class TestMatchLineups:
    def test_list(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/matches/7298/lineups").json()

        assert body["total"] == 5
        assert set(body) == {"items", "limit", "offset", "total"}

    def test_lineup_payload(self, seeded_client: TestClient) -> None:
        items = seeded_client.get("/api/v1/matches/7298/lineups").json()["items"]
        keeper = next(item for item in items if item["player"]["id"] == 3509)

        assert keeper["team"] == {"id": 220, "name": "Real Madrid"}
        assert keeper["jersey_number"] == 1
        assert keeper["position_name"] == "Goalkeeper"
        assert keeper["is_starter"] is True

    def test_lineups_for_unknown_match_returns_404(self, seeded_client: TestClient) -> None:
        response = seeded_client.get("/api/v1/matches/999999/lineups")

        assert response.status_code == 404
        assert response.json() == {"detail": "Match not found"}


class TestReadOnly:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/matches"),
            ("put", "/api/v1/matches/7298"),
            ("patch", "/api/v1/teams/220"),
            ("delete", "/api/v1/players/3604"),
        ],
    )
    def test_write_methods_are_not_exposed(
        self, seeded_client: TestClient, method: str, path: str
    ) -> None:
        assert getattr(seeded_client, method)(path).status_code == 405
