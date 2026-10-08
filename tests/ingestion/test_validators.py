from datetime import UTC, date, datetime, time

import pytest

from app.core.exceptions import ValidationError
from app.ingestion.validators import (
    optional_int,
    optional_str,
    parse_date,
    parse_location,
    parse_optional_datetime,
    parse_optional_time,
    require_int,
    require_mapping,
    require_str,
)


class TestRequired:
    def test_mapping(self) -> None:
        assert require_mapping({"a": 1}, "x") == {"a": 1}

    @pytest.mark.parametrize("value", [None, [], "a", 1])
    def test_mapping_rejects_non_objects(self, value: object) -> None:
        with pytest.raises(ValidationError, match="Expected an object"):
            require_mapping(value, "x")

    def test_int(self) -> None:
        assert require_int(7, "x") == 7

    @pytest.mark.parametrize("value", [None, "7", 7.0, True])
    def test_int_rejects_non_integers(self, value: object) -> None:
        with pytest.raises(ValidationError, match="Expected an integer"):
            require_int(value, "x")

    def test_str_strips(self) -> None:
        assert require_str("  Real Madrid  ", "x") == "Real Madrid"

    @pytest.mark.parametrize("value", [None, "", "   ", 7])
    def test_str_rejects_empty(self, value: object) -> None:
        with pytest.raises(ValidationError, match="non-empty string"):
            require_str(value, "x")


class TestOptional:
    def test_none_passes_through(self) -> None:
        assert optional_int(None, "x") is None
        assert optional_str(None, "x") is None

    def test_blank_string_becomes_none(self) -> None:
        assert optional_str("   ", "x") is None

    def test_invalid_value_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            optional_int("7", "x")


class TestDateAndTime:
    def test_parse_date(self) -> None:
        assert parse_date("2018-05-26", "match_date") == date(2018, 5, 26)

    @pytest.mark.parametrize("value", ["26/05/2018", "2018-13-01", "not-a-date", None])
    def test_parse_date_rejects_malformed(self, value: object) -> None:
        with pytest.raises(ValidationError):
            parse_date(value, "match_date")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("21:45:00.000", time(21, 45)),
            ("21:45:00", time(21, 45)),
            ("21:45", time(21, 45)),
            ("00:06:33.100", time(0, 6, 33, 100000)),
            (None, None),
        ],
    )
    def test_parse_optional_time(self, value: object, expected: time | None) -> None:
        assert parse_optional_time(value, "kick_off") == expected

    @pytest.mark.parametrize("value", ["25:00:00", "abc", "", 2145])
    def test_parse_optional_time_rejects_malformed(self, value: object) -> None:
        with pytest.raises(ValidationError):
            parse_optional_time(value, "timestamp")

    def test_parse_optional_datetime(self) -> None:
        assert parse_optional_datetime("2020-07-29T05:00", "last_updated") == datetime(
            2020, 7, 29, 5, 0
        )

    def test_parse_optional_datetime_handles_zulu(self) -> None:
        assert parse_optional_datetime("2020-07-29T05:00:00Z", "last_updated") == datetime(
            2020, 7, 29, 5, 0, tzinfo=UTC
        )

    def test_parse_optional_datetime_rejects_malformed(self) -> None:
        with pytest.raises(ValidationError, match="Unparseable timestamp"):
            parse_optional_datetime("29-07-2020", "last_updated")


class TestLocation:
    def test_two_number_array(self) -> None:
        assert parse_location([60.0, 40.1]) == (60.0, 40.1)

    def test_integers_are_coerced_to_float(self) -> None:
        assert parse_location([60, 40]) == (60.0, 40.0)

    def test_absent_location_is_allowed(self) -> None:
        assert parse_location(None) == (None, None)

    @pytest.mark.parametrize(
        "value",
        [[], [60.0], [60.0, 40.0, 0.5], "60,40", {"x": 60}, [60.0, "40"], [60.0, None], [True, 40]],
    )
    def test_malformed_location_rejected(self, value: object) -> None:
        with pytest.raises(ValidationError):
            parse_location(value)
