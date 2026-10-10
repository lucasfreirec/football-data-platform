"""Field-level parsing and validation helpers for StatsBomb source records.

Each helper raises :class:`ValidationError` on malformed input so callers can
skip a single record instead of aborting the import.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Any

from app.core.exceptions import ValidationError

_TIME_FORMATS = ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M")


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"Expected an object for {field!r}, got {type(value).__name__}")
    return value


def require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"Expected an integer for {field!r}, got {value!r}")
    return value


def require_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Expected a non-empty string for {field!r}, got {value!r}")
    return value.strip()


def optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    return require_int(value, field)


def optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"Expected a string for {field!r}, got {value!r}")
    stripped = value.strip()
    return stripped or None


def optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValidationError(f"Expected a boolean for {field!r}, got {value!r}")
    return value


def optional_finite_float(value: Any, field: str) -> float | None:
    """Accept a real number; reject booleans, strings, NaN, and infinities."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"Expected a number for {field!r}, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"Expected a finite number for {field!r}, got {value!r}")
    return number


def parse_date(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = require_str(value, field)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"Unparseable date for {field!r}: {value!r}") from exc


def parse_optional_date(value: Any, field: str) -> date | None:
    return None if value is None else parse_date(value, field)


def parse_optional_time(value: Any, field: str) -> time | None:
    """Parse StatsBomb clock values such as ``"21:45:00.000"``."""
    if value is None:
        return None
    if isinstance(value, time):
        return value
    text = require_str(value, field)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValidationError(f"Unparseable time for {field!r}: {value!r}")


def parse_optional_datetime(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = require_str(value, field)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"Unparseable timestamp for {field!r}: {value!r}") from exc


def parse_location(
    value: Any, field: str = "location", *, allow_height: bool = False
) -> tuple[float | None, float | None]:
    """Return ``(x, y)`` for a two-number array, ``(None, None)`` when absent.

    With ``allow_height`` a third value is accepted and discarded: StatsBomb adds the
    ball height to the end location of on-target shots, and no column stores it.
    """
    if value is None:
        return None, None
    allowed = (2, 3) if allow_height else (2,)
    if not isinstance(value, (list, tuple)) or len(value) not in allowed:
        expected = "a two- or three-number array" if allow_height else "a two-number array"
        raise ValidationError(f"Expected {expected} for {field!r}, got {value!r}")
    coordinates: list[float] = []
    for axis, item in zip(("x", "y", "z"), value, strict=False):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValidationError(f"Non-numeric {axis} coordinate for {field!r}: {value!r}")
        coordinates.append(float(item))
    return coordinates[0], coordinates[1]
