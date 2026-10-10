"""Runs one job through its lifecycle: claim, work, then complete, retry, or fail.

State transitions are driven by the job row in PostgreSQL, never by the queue:

* **Claim.** ``UPDATE ... WHERE status IN ('pending', 'retrying')`` moves a job to
  ``running`` atomically and counts the attempt. A second delivery of the same
  message finds nothing to claim and does nothing, which is what makes duplicate
  delivery safe.
* **Work and complete** share one transaction. A crash part-way rolls the data back
  with the status, leaving a ``running`` job that stale-job recovery can retry; the
  application logic is idempotent, so the retry is safe.
* **Failure** is recorded in a new transaction, as ``retrying`` (with a delayed
  re-queue) while attempts remain and the error is transient, otherwise ``failed``.

Every session here is created for this one execution and never shared.
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    PermanentJobError,
    RetryableJobError,
    SourceReadError,
    UnsupportedJobTypeError,
)
from app.core.logging import get_logger
from app.db.models import JobStatus, ProcessingJob
from app.jobs import repository
from app.jobs.handlers import get_handler
from app.jobs.queue import JobQueue

logger = get_logger(__name__)

SessionFactory = Callable[[], Session]

COMPLETED = "completed"
RETRYING = "retrying"
FAILED = "failed"
SKIPPED = "skipped"
DEFERRED = "deferred"

# Delay before re-delivering a job whose claim could not even reach the database.
CLAIM_RETRY_SECONDS = 5

TRANSIENT_DATABASE_MESSAGE = "A temporary database problem occurred."


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True, slots=True)
class Claim:
    job_type: str
    statsbomb_match_id: int | None
    attempt: int
    max_attempts: int


def claim_job(session_factory: SessionFactory, job_id: uuid.UUID) -> Claim | None:
    """Atomically move a pending or retrying job to running; ``None`` if it is not claimable."""
    with session_factory() as session:
        now = repository.utcnow()
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.status.in_([JobStatus.pending.value, JobStatus.retrying.value]),
                )
                .values(
                    status=JobStatus.running.value,
                    started_at=now,
                    completed_at=None,
                    updated_at=now,
                    attempt_count=ProcessingJob.attempt_count + 1,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            session.rollback()
            return None
        session.commit()
        row = session.execute(
            select(
                ProcessingJob.job_type,
                ProcessingJob.statsbomb_match_id,
                ProcessingJob.attempt_count,
                ProcessingJob.max_attempts,
            ).where(ProcessingJob.id == job_id)
        ).one()
        return Claim(row.job_type, row.statsbomb_match_id, row.attempt_count, row.max_attempts)


def classify(exc: Exception) -> tuple[bool, str]:
    """Return ``(retryable, client-safe message)``; the cause itself is only logged."""
    if isinstance(exc, RetryableJobError):
        return True, str(exc)
    if isinstance(exc, PermanentJobError):
        return False, str(exc)
    if isinstance(exc, UnsupportedJobTypeError):
        return False, "This job type is not supported."
    if isinstance(exc, SourceReadError | OSError):
        return True, "The match source data could not be read."
    # Connection loss, deadlocks, and lock races clear up on their own.
    if isinstance(exc, OperationalError | InterfaceError | IntegrityError):
        return True, TRANSIENT_DATABASE_MESSAGE
    if isinstance(exc, SQLAlchemyError):
        return False, "A database error occurred."
    return False, "An unexpected error occurred."


def retry_delay(settings: Settings, attempt: int) -> int:
    delays = settings.retry_delays
    return delays[min(attempt, len(delays)) - 1]


def execute_job(
    job_id: uuid.UUID,
    *,
    session_factory: SessionFactory,
    queue: JobQueue,
    settings: Settings,
    worker: str | None = None,
) -> str:
    """Run the job once and return what happened: completed, retrying, failed, or skipped."""
    worker = worker or worker_identity()
    started = time.perf_counter()

    try:
        claim = claim_job(session_factory, job_id)
    except SQLAlchemyError as exc:
        logger.error("job_claim_failed job_id=%s worker=%s error=%r", job_id, worker, exc)
        _redeliver(queue, job_id, CLAIM_RETRY_SECONDS)
        return DEFERRED

    if claim is None:
        logger.info("job_skipped job_id=%s worker=%s reason=not_claimable", job_id, worker)
        return SKIPPED

    outcome = FAILED
    try:
        handler = get_handler(claim.job_type)
        with session_factory() as session:
            try:
                job = session.get(ProcessingJob, job_id)
                if job is None:
                    raise PermanentJobError("The job no longer exists.")
                result = handler(session, job, settings)
                result["worker"] = worker
                now = repository.utcnow()
                job.status = JobStatus.completed.value
                job.result = result
                job.error_message = None
                job.completed_at = now
                job.updated_at = now
                session.commit()
            except BaseException:
                session.rollback()
                raise
        outcome = COMPLETED
    except Exception as exc:
        outcome = _record_failure(session_factory, queue, settings, job_id, claim, exc)

    logger.info(
        "job_finished job_id=%s job_type=%s match_id=%s attempt=%d/%d worker=%s "
        "duration_s=%.3f outcome=%s",
        job_id,
        claim.job_type,
        claim.statsbomb_match_id,
        claim.attempt,
        claim.max_attempts,
        worker,
        time.perf_counter() - started,
        outcome,
    )
    return outcome


def _record_failure(
    session_factory: SessionFactory,
    queue: JobQueue,
    settings: Settings,
    job_id: uuid.UUID,
    claim: Claim,
    exc: Exception,
) -> str:
    retryable, message = classify(exc)
    will_retry = retryable and claim.attempt < claim.max_attempts
    if retryable and not will_retry:
        message = f"{message} Gave up after {claim.attempt} attempts."

    # Unexpected failures need a traceback; anticipated ones are already logged by the handler.
    unexpected = not isinstance(exc, RetryableJobError | PermanentJobError | SourceReadError)
    logger.warning(
        "job_attempt_failed job_id=%s attempt=%d retryable=%s error=%r",
        job_id,
        claim.attempt,
        retryable,
        exc,
        exc_info=unexpected,
    )

    now = repository.utcnow()
    status = JobStatus.retrying if will_retry else JobStatus.failed
    try:
        with session_factory() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(ProcessingJob)
                    .where(
                        ProcessingJob.id == job_id,
                        ProcessingJob.status == JobStatus.running.value,
                    )
                    .values(
                        status=status.value,
                        error_message=message[:500],
                        completed_at=None if will_retry else now,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                ),
            )
            session.commit()
    except SQLAlchemyError as db_exc:
        # The job stays "running"; stale-job recovery will pick it up.
        logger.error("job_failure_not_recorded job_id=%s error=%r", job_id, db_exc)
        return FAILED

    if result.rowcount != 1:
        logger.warning("job_state_changed job_id=%s detail=no_longer_running", job_id)
        return FAILED
    if will_retry:
        _redeliver(queue, job_id, retry_delay(settings, claim.attempt))
        return RETRYING
    return FAILED


def _redeliver(queue: JobQueue, job_id: uuid.UUID, delay_seconds: int) -> None:
    try:
        queue.enqueue(job_id, countdown=delay_seconds)
    except Exception as exc:
        # The job stays retrying/pending in PostgreSQL, so reconciliation can re-queue it.
        logger.error("job_requeue_failed job_id=%s error=%r", job_id, exc)
