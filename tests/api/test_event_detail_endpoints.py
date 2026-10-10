import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event

PASS_ID = "90010000-0000-4000-8000-000000000003"
INCOMPLETE_PASS_ID = "90010000-0000-4000-8000-000000000004"
GOAL_SHOT_ID = "90010000-0000-4000-8000-000000000006"
KEY_PASS_ID = "90010000-0000-4000-8000-000000000005"
CARRY_ID = "90010000-0000-4000-8000-000000000010"
STARTING_XI_ID = "90010000-0000-4000-8000-000000000001"


def _ids(body: dict) -> list[str]:  # type: ignore[type-arg]
    return [item["id"] for item in body["items"]]


def _indexes(body: dict) -> list[int]:  # type: ignore[type-arg]
    return [item["index_in_match"] for item in body["items"]]


class TestPasses:
    def test_list_is_ordered_by_the_source_index(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/passes").json()

        assert body["total"] == 4
        assert _indexes(body) == [3, 4, 5, 8]

    def test_uses_the_v1_pagination_envelope(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/passes").json()

        assert set(body) == {"items", "limit", "offset", "total"}
        assert (body["limit"], body["offset"]) == (50, 0)

    def test_item_has_base_event_fields_and_a_pass_object(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/passes").json()["items"][0]

        assert item["id"] == PASS_ID
        assert item["type_name"] == "Pass"
        assert item["team"] == {"id": 101, "name": "Alpha FC"}
        assert item["player"] == {"id": 1001, "name": "Ana Alpha"}
        assert item["pass"]["recipient"] == {"id": 1002, "name": "Ben Alpha"}
        assert item["pass"]["length"] == 12.5
        assert item["pass"]["height_name"] == "Ground Pass"
        assert item["pass"]["end_location_x"] == 60.0
        assert item["pass"]["outcome_name"] is None

    def test_raw_data_is_not_exposed(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/passes").json()["items"][0]

        assert "raw_data" not in item
        assert "pass_detail" not in item
        assert "pass_" not in item

    def test_incomplete_pass_and_missing_recipient(self, v2_client: TestClient) -> None:
        items = v2_client.get("/api/v1/matches/9001/passes").json()["items"]
        incomplete = next(item for item in items if item["id"] == INCOMPLETE_PASS_ID)

        assert incomplete["pass"]["outcome_name"] == "Incomplete"
        assert incomplete["pass"]["recipient"] is None
        assert incomplete["pass"]["is_switch"] is True

    def test_malformed_passes_were_never_imported(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9002/passes").json()

        assert body["total"] == 4
        assert _indexes(body) == [3, 4, 5, 9]

    def test_pagination_applies(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/passes", params={"limit": 2, "offset": 1}).json()

        assert body["total"] == 4
        assert _indexes(body) == [4, 5]

    def test_offset_past_the_end_is_an_empty_page(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/passes", params={"offset": 99}).json()

        assert body["items"] == []
        assert body["total"] == 4

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_invalid_pagination_is_422(self, v2_client: TestClient, params: dict[str, int]) -> None:
        assert v2_client.get("/api/v1/matches/9001/passes", params=params).status_code == 422

    def test_filter_by_team(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/passes", params={"team_id": 102}).json()

        assert _indexes(body) == [8]

    def test_filter_by_player(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/passes", params={"player_id": 1001}).json()

        assert _indexes(body) == [3, 4]

    def test_filter_by_recipient(self, v2_client: TestClient) -> None:
        body = v2_client.get(
            "/api/v1/matches/9001/passes", params={"recipient_player_id": 1002}
        ).json()

        assert _indexes(body) == [3]

    def test_filter_by_outcome(self, v2_client: TestClient) -> None:
        body = v2_client.get(
            "/api/v1/matches/9001/passes", params={"outcome_name": "Incomplete"}
        ).json()

        assert _indexes(body) == [4]

    def test_filters_combine(self, v2_client: TestClient) -> None:
        body = v2_client.get(
            "/api/v1/matches/9002/passes", params={"team_id": 102, "recipient_player_id": 2002}
        ).json()

        assert _indexes(body) == [4, 5]

    def test_a_valid_filter_with_no_rows_is_an_empty_page_not_an_error(
        self, v2_client: TestClient
    ) -> None:
        response = v2_client.get("/api/v1/matches/9001/passes", params={"outcome_name": "Nope"})

        assert response.status_code == 200
        assert response.json() == {"items": [], "limit": 50, "offset": 0, "total": 0}

    def test_an_unknown_player_filter_is_an_empty_page(self, v2_client: TestClient) -> None:
        response = v2_client.get("/api/v1/matches/9001/passes", params={"player_id": 999999})

        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_non_integer_filter_is_422(self, v2_client: TestClient) -> None:
        response = v2_client.get("/api/v1/matches/9001/passes", params={"team_id": "alpha"})

        assert response.status_code == 422

    def test_unsupported_filters_are_ignored(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/passes", params={"period": 2}).json()

        assert body["total"] == 4

    def test_unknown_match_is_404(self, v2_client: TestClient) -> None:
        response = v2_client.get("/api/v1/matches/999999/passes")

        assert response.status_code == 404
        assert response.json() == {"detail": "Match not found"}

    def test_non_integer_match_id_is_422(self, v2_client: TestClient) -> None:
        assert v2_client.get("/api/v1/matches/abc/passes").status_code == 422


class TestShots:
    def test_list_is_ordered_by_the_source_index(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/shots").json()

        assert body["total"] == 3
        assert _indexes(body) == [6, 7, 9]
        assert set(body) == {"items", "limit", "offset", "total"}

    def test_goal_item_has_a_shot_object(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/shots").json()["items"][0]

        assert item["id"] == GOAL_SHOT_ID
        assert item["type_name"] == "Shot"
        assert item["player"] == {"id": 1001, "name": "Ana Alpha"}
        assert item["shot"]["statsbomb_xg"] == 0.25
        assert item["shot"]["outcome_name"] == "Goal"
        assert item["shot"]["end_location_x"] == 120.0
        assert item["shot"]["end_location_y"] == 40.0
        assert item["shot"]["first_time"] is True
        assert item["shot"]["key_pass_id"] == KEY_PASS_ID
        assert "pass" not in item
        assert "raw_data" not in item

    def test_shot_without_xg_is_null_not_zero(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/shots").json()["items"][1]

        assert item["shot"]["statsbomb_xg"] is None
        assert item["shot"]["key_pass_id"] is None
        assert item["shot"]["one_on_one"] is True

    def test_internal_key_pass_id_is_not_leaked(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/shots").json()["items"][0]

        assert "key_pass_event_id" not in item["shot"]
        assert "key_pass_statsbomb_id" not in item["shot"]

    def test_malformed_shots_were_never_imported(self, v2_client: TestClient) -> None:
        assert _indexes(v2_client.get("/api/v1/matches/9002/shots").json()) == [6, 7, 8]

    def test_pagination_applies(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/shots", params={"limit": 1, "offset": 2}).json()

        assert body["total"] == 3
        assert _indexes(body) == [9]

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
    def test_invalid_pagination_is_422(self, v2_client: TestClient, params: dict[str, int]) -> None:
        assert v2_client.get("/api/v1/matches/9001/shots", params=params).status_code == 422

    def test_filter_by_outcome(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/shots", params={"outcome_name": "Goal"}).json()

        assert _ids(body) == [GOAL_SHOT_ID]

    def test_filter_by_team(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/shots", params={"team_id": 102}).json()

        assert _indexes(body) == [7]

    def test_filter_by_player(self, v2_client: TestClient) -> None:
        body = v2_client.get("/api/v1/matches/9001/shots", params={"player_id": 1002}).json()

        assert _indexes(body) == [9]

    def test_filters_combine(self, v2_client: TestClient) -> None:
        body = v2_client.get(
            "/api/v1/matches/9002/shots", params={"team_id": 102, "outcome_name": "Blocked"}
        ).json()

        assert _indexes(body) == [7]

    def test_a_valid_filter_with_no_rows_is_an_empty_page_not_an_error(
        self, v2_client: TestClient
    ) -> None:
        response = v2_client.get("/api/v1/matches/9001/shots", params={"outcome_name": "Nope"})

        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_recipient_filter_is_not_a_shot_filter(self, v2_client: TestClient) -> None:
        body = v2_client.get(
            "/api/v1/matches/9001/shots", params={"recipient_player_id": 1002}
        ).json()

        assert body["total"] == 3

    def test_unknown_match_is_404(self, v2_client: TestClient) -> None:
        response = v2_client.get("/api/v1/matches/999999/shots")

        assert response.status_code == 404
        assert response.json() == {"detail": "Match not found"}


class TestEventDetail:
    def test_pass_event_has_a_pass_object_and_a_null_shot(self, v2_client: TestClient) -> None:
        body = v2_client.get(f"/api/v1/matches/9001/events/{PASS_ID}").json()

        assert body["id"] == PASS_ID
        assert body["type_name"] == "Pass"
        assert body["index_in_match"] == 3
        assert body["pass"]["length"] == 12.5
        assert body["pass"]["recipient"] == {"id": 1002, "name": "Ben Alpha"}
        assert body["shot"] is None

    def test_shot_event_has_a_shot_object_and_a_null_pass(self, v2_client: TestClient) -> None:
        body = v2_client.get(f"/api/v1/matches/9001/events/{GOAL_SHOT_ID}").json()

        assert body["type_name"] == "Shot"
        assert body["shot"]["statsbomb_xg"] == 0.25
        assert body["shot"]["key_pass_id"] == KEY_PASS_ID
        assert body["pass"] is None

    @pytest.mark.parametrize("event_id", [CARRY_ID, STARTING_XI_ID])
    def test_other_event_types_have_neither_detail(
        self, v2_client: TestClient, event_id: str
    ) -> None:
        body = v2_client.get(f"/api/v1/matches/9001/events/{event_id}").json()

        assert body["pass"] is None
        assert body["shot"] is None

    def test_base_event_fields_are_present(self, v2_client: TestClient) -> None:
        body = v2_client.get(f"/api/v1/matches/9001/events/{PASS_ID}").json()

        assert body["period"] == 1
        assert body["minute"] == 0
        assert body["second"] == 5
        assert body["possession"] == 2
        assert body["possession_team"] == {"id": 101, "name": "Alpha FC"}
        assert body["location_x"] == 50.0
        assert body["location_y"] == 40.0

    def test_raw_data_is_never_returned(self, v2_client: TestClient) -> None:
        for event_id in (PASS_ID, GOAL_SHOT_ID, CARRY_ID):
            body = v2_client.get(f"/api/v1/matches/9001/events/{event_id}").json()

            assert "raw_data" not in body

    def test_response_keys_use_the_public_names(self, v2_client: TestClient) -> None:
        body = v2_client.get(f"/api/v1/matches/9001/events/{PASS_ID}").json()

        assert "pass" in body
        assert "shot" in body
        assert "pass_" not in body
        assert "pass_detail" not in body
        assert "shot_detail" not in body

    def test_unknown_event_is_404(self, v2_client: TestClient) -> None:
        response = v2_client.get("/api/v1/matches/9001/events/not-an-event")

        assert response.status_code == 404
        assert response.json() == {"detail": "Event not found"}

    def test_an_event_of_another_match_is_404(self, v2_client: TestClient) -> None:
        response = v2_client.get(f"/api/v1/matches/9002/events/{PASS_ID}")

        assert response.status_code == 404
        assert response.json() == {"detail": "Event not found"}

    def test_unknown_match_is_404(self, v2_client: TestClient) -> None:
        response = v2_client.get(f"/api/v1/matches/999999/events/{PASS_ID}")

        assert response.status_code == 404
        assert response.json() == {"detail": "Match not found"}


class TestV1ContractIsUnchanged:
    def test_v1_event_list_still_returns_raw_data(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/events").json()["items"][0]

        assert "raw_data" in item

    def test_v1_event_list_has_no_detail_objects(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/events", params={"type_name": "Pass"}).json()[
            "items"
        ][0]

        assert "pass" not in item
        assert "shot" not in item

    def test_v1_event_list_fields_are_unchanged(self, v2_client: TestClient) -> None:
        item = v2_client.get("/api/v1/matches/9001/events").json()["items"][0]

        assert list(item) == [
            "id",
            "index_in_match",
            "type_name",
            "period",
            "timestamp",
            "minute",
            "second",
            "possession",
            "possession_team",
            "team",
            "player",
            "location_x",
            "location_y",
            "outcome_name",
            "raw_data",
        ]


class TestOpenApi:
    def test_new_routes_are_documented(self, v2_client: TestClient) -> None:
        paths = v2_client.get("/openapi.json").json()["paths"]

        assert (
            paths["/api/v1/matches/{match_id}/passes"]["get"]["summary"] == "List a match's passes"
        )
        assert paths["/api/v1/matches/{match_id}/shots"]["get"]["summary"] == "List a match's shots"
        assert (
            paths["/api/v1/matches/{match_id}/events/{event_id}"]["get"]["summary"]
            == "Get one event with its details"
        )

    def test_pass_filters_are_documented(self, v2_client: TestClient) -> None:
        operation = v2_client.get("/openapi.json").json()["paths"][
            "/api/v1/matches/{match_id}/passes"
        ]["get"]
        names = {parameter["name"] for parameter in operation["parameters"]}

        assert {"team_id", "player_id", "recipient_player_id", "outcome_name"} <= names
        assert {"limit", "offset"} <= names

    def test_event_detail_schema_uses_the_pass_key(self, v2_client: TestClient) -> None:
        schemas = v2_client.get("/openapi.json").json()["components"]["schemas"]

        assert "pass" in schemas["EventDetailRead"]["properties"]
        assert "pass_" not in schemas["EventDetailRead"]["properties"]


class TestQueryCount:
    """A page must cost a fixed number of queries, however many rows it holds."""

    @staticmethod
    def _statements(engine: Engine, client: TestClient, url: str, **params: int) -> int:
        seen: list[str] = []

        def record(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert client.get(url, params=params).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    @pytest.mark.parametrize("kind", ["passes", "shots"])
    def test_list_queries_do_not_grow_with_page_size(
        self, v2_client: TestClient, engine: Engine, kind: str
    ) -> None:
        url = f"/api/v1/matches/9001/{kind}"

        one = self._statements(engine, v2_client, url, limit=1)
        many = self._statements(engine, v2_client, url, limit=50)

        assert one == many
