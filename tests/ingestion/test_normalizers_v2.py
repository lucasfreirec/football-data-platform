import json
from pathlib import Path
from typing import Any

import pytest

from app.core.exceptions import ValidationError
from app.ingestion.normalizers import (
    NormalizedPassDetail,
    NormalizedShotDetail,
    normalize_event,
    normalize_pass_detail,
    normalize_shot_detail,
)


@pytest.fixture
def events(v2_source_dir: Path) -> dict[str, dict[str, Any]]:
    """Events of match 9001, keyed by their source index."""
    raw = json.loads((v2_source_dir / "events" / "9001.json").read_text())
    return {str(event["index"]): event for event in raw}


def _pass(**detail: Any) -> dict[str, Any]:
    return {
        "id": "e-1",
        "index": 1,
        "type": {"id": 30, "name": "Pass"},
        "pass": detail,
    }


def _shot(**detail: Any) -> dict[str, Any]:
    return {
        "id": "e-1",
        "index": 1,
        "type": {"id": 16, "name": "Shot"},
        "shot": detail,
    }


class TestPassNormalization:
    def test_completed_pass_with_recipient(self, events: dict[str, dict[str, Any]]) -> None:
        event = normalize_event(events["3"])
        detail = event.pass_detail

        assert event.shot_detail is None
        assert detail is not None
        assert detail.recipient is not None
        assert detail.recipient.statsbomb_id == 1002
        assert detail.recipient.name == "Ben Alpha"
        assert detail.length == 12.5
        assert detail.angle == 0.3
        assert detail.height_name == "Ground Pass"
        assert (detail.end_location_x, detail.end_location_y) == (60.0, 40.0)
        assert detail.body_part_name == "Right Foot"
        assert detail.type_name == "Recovery"
        assert detail.outcome_name is None

    def test_incomplete_pass_without_recipient(self, events: dict[str, dict[str, Any]]) -> None:
        detail = normalize_event(events["4"]).pass_detail

        assert detail is not None
        assert detail.recipient is None
        assert detail.outcome_name == "Incomplete"
        assert detail.is_switch is True
        assert detail.height_name == "High Pass"

    def test_cross_goal_assist_and_assisted_shot_flags(
        self, events: dict[str, dict[str, Any]]
    ) -> None:
        detail = normalize_event(events["5"]).pass_detail

        assert detail is not None
        assert detail.is_cross is True
        assert detail.is_goal_assist is True
        assert detail.is_assisted_shot is True

    def test_absent_flags_stay_null_rather_than_false(
        self, events: dict[str, dict[str, Any]]
    ) -> None:
        detail = normalize_event(events["3"]).pass_detail

        assert detail is not None
        assert detail.is_cross is None
        assert detail.is_cut_back is None
        assert detail.is_switch is None
        assert detail.is_assisted_shot is None
        assert detail.is_goal_assist is None
        assert detail.technique_name is None

    def test_shot_assist_flag_alone_marks_an_assisted_shot(self) -> None:
        assert normalize_pass_detail(_pass(shot_assist=True)).is_assisted_shot is True

    def test_assisted_shot_id_alone_marks_an_assisted_shot(self) -> None:
        detail = normalize_pass_detail(_pass(assisted_shot_id="abc"))

        assert detail.is_assisted_shot is True

    def test_explicit_false_shot_assist_is_preserved(self) -> None:
        assert normalize_pass_detail(_pass(shot_assist=False)).is_assisted_shot is False

    def test_pass_event_without_a_pass_object_has_an_empty_detail(self) -> None:
        event = normalize_event({"id": "e", "index": 1, "type": {"name": "Pass"}})

        assert event.pass_detail == NormalizedPassDetail()

    def test_integer_numbers_are_coerced_to_floats(self) -> None:
        detail = normalize_pass_detail(_pass(length=10, angle=0, end_location=[1, 2]))

        assert (detail.length, detail.angle) == (10.0, 0.0)
        assert (detail.end_location_x, detail.end_location_y) == (1.0, 2.0)

    def test_non_pass_events_have_no_details(self, events: dict[str, dict[str, Any]]) -> None:
        for index in ("1", "10"):  # Starting XI, Carry
            event = normalize_event(events[index])

            assert event.pass_detail is None
            assert event.shot_detail is None


class TestShotNormalization:
    def test_goal_with_xg_and_three_value_end_location(
        self, events: dict[str, dict[str, Any]]
    ) -> None:
        event = normalize_event(events["6"])
        detail = event.shot_detail

        assert event.pass_detail is None
        assert detail is not None
        assert detail.statsbomb_xg == 0.25
        assert detail.outcome_name == "Goal"
        assert (detail.end_location_x, detail.end_location_y) == (120.0, 40.0)
        assert detail.key_pass_statsbomb_id == "90010000-0000-4000-8000-000000000005"
        assert detail.body_part_name == "Right Foot"
        assert detail.technique_name == "Normal"
        assert detail.type_name == "Open Play"
        assert detail.first_time is True
        assert detail.one_on_one is None
        assert detail.open_goal is None

    def test_shot_without_xg_or_key_pass(self, events: dict[str, dict[str, Any]]) -> None:
        detail = normalize_event(events["7"]).shot_detail

        assert detail is not None
        assert detail.statsbomb_xg is None
        assert detail.key_pass_statsbomb_id is None
        assert (detail.end_location_x, detail.end_location_y) == (118.0, 38.0)
        assert detail.outcome_name == "Saved"
        assert detail.one_on_one is True

    def test_free_kick_shot_type(self, events: dict[str, dict[str, Any]]) -> None:
        detail = normalize_event(events["9"]).shot_detail

        assert detail is not None
        assert detail.type_name == "Free Kick"
        assert detail.outcome_name == "Off T"

    def test_xg_of_zero_is_kept_not_treated_as_missing(self) -> None:
        assert normalize_shot_detail(_shot(statsbomb_xg=0)).statsbomb_xg == 0.0

    def test_shot_event_without_a_shot_object_has_an_empty_detail(self) -> None:
        event = normalize_event({"id": "e", "index": 1, "type": {"name": "Shot"}})

        assert event.shot_detail == NormalizedShotDetail()

    def test_event_outcome_still_comes_from_the_detail(
        self, events: dict[str, dict[str, Any]]
    ) -> None:
        assert normalize_event(events["6"]).outcome_name == "Goal"


class TestMalformedPassDetails:
    @pytest.mark.parametrize(
        ("detail", "reason"),
        [
            ({"end_location": [60.0]}, "pass.end_location"),
            ({"end_location": [60.0, 40.0, 1.0]}, "pass.end_location"),
            ({"end_location": "60,40"}, "pass.end_location"),
            ({"end_location": [60.0, "40"]}, "Non-numeric y"),
            ({"length": "long"}, "pass.length"),
            ({"length": float("inf")}, "finite"),
            ({"angle": float("nan")}, "finite"),
            ({"angle": True}, "pass.angle"),
            ({"recipient": {"name": "No Identifier"}}, "pass.recipient.id"),
            ({"recipient": {"id": 5}}, "pass.recipient.name"),
            ({"recipient": {"id": "5", "name": "X"}}, "pass.recipient.id"),
            ({"recipient": "Ben"}, "pass.recipient"),
            ({"height": "Ground Pass"}, "pass.height"),
            ({"outcome": {"id": 9, "name": 7}}, "pass.outcome.name"),
            ({"cross": "yes"}, "pass.cross"),
            ({"switch": 1}, "pass.switch"),
            ({"assisted_shot_id": 12}, "pass.assisted_shot_id"),
        ],
    )
    def test_event_is_rejected_with_a_precise_reason(
        self, detail: dict[str, Any], reason: str
    ) -> None:
        with pytest.raises(ValidationError, match=reason):
            normalize_event(_pass(**detail))

    @pytest.mark.parametrize("pass_object", ["Ground Pass", [1, 2], 7])
    def test_non_object_pass_payload_is_rejected(self, pass_object: Any) -> None:
        record = {"id": "e", "index": 1, "type": {"name": "Pass"}, "pass": pass_object}

        with pytest.raises(ValidationError, match="'pass'"):
            normalize_event(record)


class TestMalformedShotDetails:
    @pytest.mark.parametrize(
        ("detail", "reason"),
        [
            ({"statsbomb_xg": "high"}, "shot.statsbomb_xg"),
            ({"statsbomb_xg": True}, "shot.statsbomb_xg"),
            ({"statsbomb_xg": float("nan")}, "finite"),
            ({"statsbomb_xg": float("inf")}, "finite"),
            ({"statsbomb_xg": float("-inf")}, "finite"),
            ({"end_location": []}, "shot.end_location"),
            ({"end_location": [120.0]}, "shot.end_location"),
            ({"end_location": [120.0, 40.0, 1.0, 5.0]}, "shot.end_location"),
            ({"end_location": [120.0, None]}, "Non-numeric y"),
            ({"end_location": [120.0, 40.0, "high"]}, "Non-numeric z"),
            ({"key_pass_id": 7}, "shot.key_pass_id"),
            ({"first_time": "true"}, "shot.first_time"),
            ({"one_on_one": 1}, "shot.one_on_one"),
            ({"outcome": "Goal"}, "shot.outcome"),
            ({"technique": {"id": 1, "name": ["x"]}}, "shot.technique.name"),
        ],
    )
    def test_event_is_rejected_with_a_precise_reason(
        self, detail: dict[str, Any], reason: str
    ) -> None:
        with pytest.raises(ValidationError, match=reason):
            normalize_event(_shot(**detail))

    def test_non_object_shot_payload_is_rejected(self) -> None:
        record = {"id": "e", "index": 1, "type": {"name": "Shot"}, "shot": "Goal"}

        with pytest.raises(ValidationError, match="'shot'"):
            normalize_event(record)

    def test_valid_fixture_events_are_unaffected(self, v2_source_dir: Path) -> None:
        raw = json.loads((v2_source_dir / "events" / "9002.json").read_text())
        rejected = []
        for record in raw:
            try:
                normalize_event(record)
            except ValidationError as exc:
                rejected.append((record["index"], str(exc)))

        assert [index for index, _ in rejected] == [10, 11, 12, 13]
        assert "pass.end_location" in rejected[0][1]
        assert "shot.statsbomb_xg" in rejected[1][1]
        assert "pass.recipient.id" in rejected[2][1]
        assert "shot.end_location" in rejected[3][1]
