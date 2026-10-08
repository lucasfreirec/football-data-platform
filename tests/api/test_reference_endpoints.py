import pytest
from fastapi.testclient import TestClient


class TestCompetitions:
    def test_list(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/competitions").json()

        assert body["total"] == 1
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert body["items"][0]["id"] == 16
        assert body["items"][0]["name"] == "Champions League"
        assert body["items"][0]["country_name"] == "Europe"

    def test_get_by_external_id(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/competitions/16").json()

        assert body["id"] == 16
        assert body["gender"] == "male"

    def test_unknown_competition_returns_404(self, seeded_client: TestClient) -> None:
        response = seeded_client.get("/api/v1/competitions/999")

        assert response.status_code == 404
        assert response.json() == {"detail": "Competition not found"}

    def test_seasons(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/competitions/16/seasons").json()

        assert body["total"] == 1
        assert body["items"][0]["id"] == 1
        assert body["items"][0]["name"] == "2017/2018"

    def test_seasons_for_unknown_competition_returns_404(self, seeded_client: TestClient) -> None:
        assert seeded_client.get("/api/v1/competitions/999/seasons").status_code == 404


class TestTeams:
    def test_list(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams").json()

        assert body["total"] == 3
        assert [item["name"] for item in body["items"]] == ["Barcelona", "Liverpool", "Real Madrid"]

    def test_name_search_is_case_insensitive(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams", params={"q": "rEaL"}).json()

        assert body["total"] == 1
        assert body["items"][0]["name"] == "Real Madrid"

    def test_name_search_with_no_match(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams", params={"q": "zzz"}).json()

        assert body == {"items": [], "limit": 50, "offset": 0, "total": 0}

    def test_get_by_external_id(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams/220").json()

        assert body["id"] == 220
        assert body["name"] == "Real Madrid"
        assert body["country_name"] == "Spain"

    def test_unknown_team_returns_404(self, seeded_client: TestClient) -> None:
        response = seeded_client.get("/api/v1/teams/999999")

        assert response.status_code == 404
        assert response.json() == {"detail": "Team not found"}

    def test_unrecognised_filter_is_ignored(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams", params={"gender": "male"}).json()

        assert body["total"] == 3


class TestPlayers:
    def test_list(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/players").json()

        assert body["total"] >= 5
        names = [item["name"] for item in body["items"]]
        assert names == sorted(names)

    def test_name_search(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/players", params={"q": "benzema"}).json()

        assert body["total"] == 1
        assert body["items"][0]["id"] == 3604

    def test_get_by_external_id(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/players/3509").json()

        assert body["name"] == "Keylor Navas Gamboa"
        assert body["nickname"] == "Keylor Navas"
        assert body["country_name"] == "Costa Rica"

    def test_unknown_player_returns_404(self, seeded_client: TestClient) -> None:
        response = seeded_client.get("/api/v1/players/999999")

        assert response.status_code == 404
        assert response.json() == {"detail": "Player not found"}


class TestPagination:
    def test_envelope_shape(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams").json()

        assert set(body) == {"items", "limit", "offset", "total"}

    def test_limit_and_offset_are_applied(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams", params={"limit": 1, "offset": 1}).json()

        assert body["total"] == 3
        assert body["limit"] == 1
        assert body["offset"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["name"] == "Liverpool"

    def test_offset_past_the_end_returns_no_items(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams", params={"offset": 500}).json()

        assert body["items"] == []
        assert body["total"] == 3

    @pytest.mark.parametrize(
        "params",
        [
            {"limit": 0},
            {"limit": -1},
            {"limit": 101},
            {"limit": "many"},
            {"offset": -1},
            {"offset": "first"},
        ],
    )
    def test_invalid_pagination_returns_422(
        self, seeded_client: TestClient, params: dict[str, object]
    ) -> None:
        assert seeded_client.get("/api/v1/teams", params=params).status_code == 422

    def test_maximum_limit_is_accepted(self, seeded_client: TestClient) -> None:
        body = seeded_client.get("/api/v1/teams", params={"limit": 100}).json()

        assert body["limit"] == 100
