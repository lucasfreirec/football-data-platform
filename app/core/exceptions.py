"""Application exception hierarchy.

Each failure mode maps to a distinct exception so callers can decide whether to
skip a single record, abort a file, or abort the whole command.
"""

from __future__ import annotations


class FootballDataError(Exception):
    """Base class for all application errors."""


class ConfigurationError(FootballDataError):
    """Raised when settings are missing or structurally invalid."""


class SourceError(FootballDataError):
    """Raised when StatsBomb source data cannot be located, read, or parsed."""


class ValidationError(FootballDataError):
    """Raised when a source record fails normalization or validation."""

    def __init__(self, message: str, *, context: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.context = context or {}


class PersistenceError(FootballDataError):
    """Raised when normalized data cannot be written to the database."""
