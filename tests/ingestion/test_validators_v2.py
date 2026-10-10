import pytest

from app.core.exceptions import ValidationError
from app.ingestion.validators import optional_finite_float, parse_location


class TestOptionalFiniteFloat:
    @pytest.mark.parametrize(
        ("value", "expected"), [(0, 0.0), (3, 3.0), (0.25, 0.25), (-1.5, -1.5)]
    )
    def test_accepts_real_numbers(self, value: float, expected: float) -> None:
        assert optional_finite_float(value, "x") == expected

    def test_none_passes_through(self) -> None:
        assert optional_finite_float(None, "x") is None

    @pytest.mark.parametrize("value", ["0.25", "", [1], {"v": 1}, True, False])
    def test_rejects_non_numbers(self, value: object) -> None:
        with pytest.raises(ValidationError, match="Expected a number"):
            optional_finite_float(value, "x")

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_non_finite_numbers(self, value: float) -> None:
        with pytest.raises(ValidationError, match="finite"):
            optional_finite_float(value, "x")


class TestLocationHeight:
    def test_three_values_are_accepted_when_allowed(self) -> None:
        assert parse_location([120.0, 40.0, 1.2], "end", allow_height=True) == (120.0, 40.0)

    def test_two_values_are_still_accepted_when_allowed(self) -> None:
        assert parse_location([118.0, 38.0], "end", allow_height=True) == (118.0, 38.0)

    def test_three_values_are_rejected_by_default(self) -> None:
        with pytest.raises(ValidationError, match="two-number array"):
            parse_location([120.0, 40.0, 1.2], "end")

    @pytest.mark.parametrize("value", [[120.0], [1.0, 2.0, 3.0, 4.0], [], "1,2,3"])
    def test_other_lengths_are_rejected_even_when_allowed(self, value: object) -> None:
        with pytest.raises(ValidationError, match="two- or three-number array"):
            parse_location(value, "end", allow_height=True)

    def test_height_must_be_numeric(self) -> None:
        with pytest.raises(ValidationError, match="Non-numeric z"):
            parse_location([120.0, 40.0, None], "end", allow_height=True)
