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


class SourceReadError(SourceError):
    """The source exists but could not be read (an I/O problem), so retrying may help."""


class ValidationError(FootballDataError):
    """Raised when a source record fails normalization or validation."""

    def __init__(self, message: str, *, context: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.context = context or {}


class PersistenceError(FootballDataError):
    """Raised when normalized data cannot be written to the database."""


class JobSubmissionError(FootballDataError):
    """Raised when a job could not be handed to the queue."""

    def __init__(self, message: str, *, job_id: object | None = None) -> None:
        super().__init__(message)
        self.job_id = job_id


class UnsupportedJobTypeError(FootballDataError):
    """Raised for a job type that has no registered handler."""


class JobStateError(FootballDataError):
    """Raised when a job is not in a state that allows the requested action."""


class RetryableJobError(FootballDataError):
    """A transient failure; the same job may succeed if it is attempted again."""


class PermanentJobError(FootballDataError):
    """A failure that retrying cannot fix without changed input or data.

    The message is shown to API clients, so it must never carry paths,
    connection strings, or broker details.
    """
