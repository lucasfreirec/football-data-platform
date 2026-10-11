"""The work each job type performs.

A handler runs inside a transaction owned by the executor. It returns a small,
JSON-safe result, or raises :class:`RetryableJobError` or
:class:`PermanentJobError`. Those messages reach API clients, so they never
contain paths, connection strings, or broker details; technical detail goes to
the server log.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    PermanentJobError,
    RetryableJobError,
    SourceError,
    SourceReadError,
    UnsupportedJobTypeError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.models import JobType, Match, ProcessingJob
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService
from app.jobs.paths import resolve_source_dir
from app.repositories import stats

logger = get_logger(__name__)

REJECTED_SAMPLE_SIZE = 10

Handler = Callable[[Session, ProcessingJob, Settings], dict[str, Any]]


def _advisory_key(job_type: str, statsbomb_match_id: int) -> int:
    """A stable signed 64-bit key identifying one match's work of one kind."""
    digest = hashlib.sha256(f"{job_type}:{statsbomb_match_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _require_match_id(job: ProcessingJob) -> int:
    if job.statsbomb_match_id is None:
        raise PermanentJobError("The job has no match to process.")
    return job.statsbomb_match_id


def _lock_match(session: Session, job_type: str, statsbomb_match_id: int) -> None:
    """Make one match's import exclusive across workers, for the transaction's duration.

    The unique index stops two *jobs* for a match; this stops two *executions* of
    one job, which can happen when stale-job recovery re-queues a job whose original
    worker is only slow. Advisory locks are PostgreSQL-specific, so tests on SQLite,
    which has a single writer anyway, skip it.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    key = _advisory_key(job_type, statsbomb_match_id)
    acquired = session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})
    if not acquired:
        raise RetryableJobError("This match is already being processed by another worker.")


def ingest_match(session: Session, job: ProcessingJob, settings: Settings) -> dict[str, Any]:
    statsbomb_match_id = _require_match_id(job)
    try:
        source_dir = resolve_source_dir(settings.statsbomb_source_dir, job.source_path)
    except ValueError as exc:
        raise PermanentJobError("The source path is not allowed.") from exc

    _lock_match(session, JobType.ingest_match.value, statsbomb_match_id)

    service = IngestionService(
        SourceLoader(source_dir),
        session,
        competition_name=job.payload.get("competition", settings.default_competition),
        season_name=job.payload.get("season", settings.default_season),
    )
    try:
        summary = service.ingest_match(statsbomb_match_id)
    except SourceReadError as exc:
        logger.warning("Transient source read failure for match %s: %s", statsbomb_match_id, exc)
        raise RetryableJobError("The match source data could not be read.") from exc
    except SourceError as exc:
        logger.warning("Unusable source for match %s: %s", statsbomb_match_id, exc)
        raise PermanentJobError("The match source data is missing or malformed.") from exc
    except ValidationError as exc:
        logger.warning("Invalid match record for match %s: %s", statsbomb_match_id, exc)
        raise PermanentJobError("The match record in the source data is invalid.") from exc

    match = session.scalar(select(Match).where(Match.statsbomb_id == statsbomb_match_id))
    if match is not None:
        job.match_id = match.id

    return {
        "import_status": summary.import_status,
        "events_imported": summary.events_imported,
        "events_skipped": summary.events_skipped,
        "passes_imported": summary.passes_imported,
        "passes_skipped": summary.passes_skipped,
        "shots_imported": summary.shots_imported,
        "shots_skipped": summary.shots_skipped,
        "lineups_imported": summary.lineups_imported,
        "lineups_skipped": summary.lineups_skipped,
        "validation_failures": summary.validation_failures,
        "unavailable_files": len(summary.missing_files),
        # Whole-season runs write manifests; a single-match job does not.
        "source_manifest_id": None,
        # Reasons only: the full records carry source file paths.
        "rejected_sample": [
            {"record": record.record_external_id, "reason": record.reason}
            for record in summary.rejected[:REJECTED_SAMPLE_SIZE]
        ],
    }


def compute_match_statistics(
    session: Session, job: ProcessingJob, settings: Settings
) -> dict[str, Any]:
    statsbomb_match_id = _require_match_id(job)
    match = session.scalar(select(Match).where(Match.statsbomb_id == statsbomb_match_id))
    if match is None:
        raise PermanentJobError("The match has not been imported.")

    job.match_id = match.id
    return {
        "match_id": statsbomb_match_id,
        "teams": [dict(row._mapping) for row in stats.get_match_team_stats(session, match)],
    }


HANDLERS: dict[str, Handler] = {
    JobType.ingest_match.value: ingest_match,
    JobType.compute_match_statistics.value: compute_match_statistics,
}


def get_handler(job_type: str) -> Handler:
    try:
        return HANDLERS[job_type]
    except KeyError:
        raise UnsupportedJobTypeError(f"No handler for job type {job_type!r}") from None
