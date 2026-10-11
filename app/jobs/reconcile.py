"""Recovery of jobs the queue or a worker lost.

Run it on demand or from a scheduler (a Kubernetes CronJob); there is no scheduler here.

* A job still ``running`` long after it started belongs to a worker that died. It is
  moved to ``retrying`` and re-queued while attempts remain, otherwise failed.
* A job left ``pending`` or ``retrying`` for a long time has probably lost its queue
  message (the broker restarted, or the re-queue after a failure never arrived). It is
  re-queued as it is. If the original message does turn up later, the duplicate is
  harmless: the executor's claim is atomic, so only one delivery does any work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import JobStatus, ProcessingJob
from app.jobs import repository
from app.jobs.executor import SessionFactory
from app.jobs.queue import JobQueue

logger = get_logger(__name__)

WORKER_LOST_MESSAGE = "The worker stopped before finishing, so the job was queued again."
NO_ATTEMPTS_LEFT_MESSAGE = "The worker stopped before finishing and no attempts remain."


@dataclass
class ReconcileReport:
    stale_running: int = 0
    requeued: int = 0
    failed: int = 0
    orphaned_requeued: int = 0
    enqueue_failures: int = 0

    @property
    def changed(self) -> int:
        return self.requeued + self.failed + self.orphaned_requeued

    def lines(self, *, dry_run: bool) -> list[str]:
        verb = "would be" if dry_run else "were"
        return [
            f"stale running jobs found     : {self.stale_running}",
            f"  {verb} re-queued           : {self.requeued}",
            f"  {verb} failed (no attempts): {self.failed}",
            f"orphaned queued jobs {verb} re-queued: {self.orphaned_requeued}",
            f"jobs that could not be queued: {self.enqueue_failures}",
        ]


def _stale_running_ids(session: Session, cutoff: datetime) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(ProcessingJob.id).where(
                ProcessingJob.status == JobStatus.running.value,
                ProcessingJob.started_at < cutoff,
            )
        )
    )


def _orphaned_ids(session: Session, cutoff: datetime) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(ProcessingJob.id).where(
                ProcessingJob.status.in_([JobStatus.pending.value, JobStatus.retrying.value]),
                ProcessingJob.updated_at < cutoff,
            )
        )
    )


def _recover_running(session: Session, job_id: uuid.UUID, cutoff: datetime) -> str | None:
    """Move one stale running job on; ``None`` if it is no longer stale and running."""
    now = repository.utcnow()
    guard = (
        ProcessingJob.id == job_id,
        ProcessingJob.status == JobStatus.running.value,
        ProcessingJob.started_at < cutoff,
    )
    attempts_left = ProcessingJob.attempt_count < ProcessingJob.max_attempts

    requeue = cast(
        CursorResult[Any],
        session.execute(
            update(ProcessingJob)
            .where(*guard, attempts_left)
            .values(
                status=JobStatus.retrying.value,
                error_message=WORKER_LOST_MESSAGE,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    if requeue.rowcount == 1:
        return JobStatus.retrying.value

    fail = cast(
        CursorResult[Any],
        session.execute(
            update(ProcessingJob)
            .where(*guard)
            .values(
                status=JobStatus.failed.value,
                error_message=NO_ATTEMPTS_LEFT_MESSAGE,
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    return JobStatus.failed.value if fail.rowcount == 1 else None


def reconcile_stale(
    session_factory: SessionFactory,
    queue: JobQueue,
    *,
    older_than: timedelta,
    include_queued: bool = True,
    dry_run: bool = False,
) -> ReconcileReport:
    report = ReconcileReport()
    cutoff = repository.utcnow() - older_than

    with session_factory() as session:
        stale = _stale_running_ids(session, cutoff)
        orphaned = _orphaned_ids(session, cutoff) if include_queued else []
        report.stale_running = len(stale)

        if dry_run:
            attempts_left = {
                row.id: row.attempt_count < row.max_attempts
                for row in session.execute(
                    select(
                        ProcessingJob.id, ProcessingJob.attempt_count, ProcessingJob.max_attempts
                    ).where(ProcessingJob.id.in_(stale))
                )
            }
            report.requeued = sum(1 for left in attempts_left.values() if left)
            report.failed = report.stale_running - report.requeued
            report.orphaned_requeued = len(orphaned)
            return report

        to_queue: list[uuid.UUID] = []
        for job_id in stale:
            outcome = _recover_running(session, job_id, cutoff)
            session.commit()
            if outcome == JobStatus.retrying.value:
                report.requeued += 1
                to_queue.append(job_id)
            elif outcome == JobStatus.failed.value:
                report.failed += 1
            logger.warning("job_reconciled job_id=%s outcome=%s", job_id, outcome or "unchanged")

        for job_id in orphaned:
            # Touching updated_at keeps a second sweep from re-queueing it straight away.
            session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.status.in_([JobStatus.pending.value, JobStatus.retrying.value]),
                )
                .values(updated_at=repository.utcnow())
                .execution_options(synchronize_session=False)
            )
            session.commit()
            report.orphaned_requeued += 1
            to_queue.append(job_id)
            logger.warning("job_requeued_orphan job_id=%s", job_id)

    for job_id in to_queue:
        try:
            queue.enqueue(job_id)
        except Exception as exc:
            # Left retrying/pending in PostgreSQL; the next sweep tries again.
            report.enqueue_failures += 1
            logger.error("job_requeue_failed job_id=%s error=%r", job_id, exc)
    return report
