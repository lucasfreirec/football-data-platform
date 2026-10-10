"""Database access for processing jobs, shared by the API, workers, and reconciliation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import ACTIVE_STATUSES, JobStatus, ProcessingJob
from app.repositories.pagination import paginate


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_job(session: Session, job_id: uuid.UUID) -> ProcessingJob | None:
    return session.get(ProcessingJob, job_id)


def find_active_job(
    session: Session, job_type: str, statsbomb_match_id: int
) -> ProcessingJob | None:
    return session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.job_type == job_type,
            ProcessingJob.statsbomb_match_id == statsbomb_match_id,
            ProcessingJob.status.in_([s.value for s in ACTIVE_STATUSES]),
        )
    )


def list_jobs(
    session: Session,
    *,
    status: str | None = None,
    job_type: str | None = None,
    statsbomb_match_id: int | None = None,
    limit: int,
    offset: int,
) -> tuple[list[ProcessingJob], int]:
    statement = select(ProcessingJob).order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
    if status is not None:
        statement = statement.where(ProcessingJob.status == status)
    if job_type is not None:
        statement = statement.where(ProcessingJob.job_type == job_type)
    if statsbomb_match_id is not None:
        statement = statement.where(ProcessingJob.statsbomb_match_id == statsbomb_match_id)
    return paginate(session, statement, limit=limit, offset=offset)


def record_queue_task_id(session: Session, job_id: uuid.UUID, task_id: str) -> None:
    """Targeted update, so a worker that already moved the job on is not overwritten."""
    session.execute(
        update(ProcessingJob).where(ProcessingJob.id == job_id).values(queue_task_id=task_id)
    )
    session.commit()


def mark_failed(session: Session, job_id: uuid.UUID, message: str) -> None:
    """Terminal failure for a job whose work never ran (for example, it was never queued)."""
    now = utcnow()
    session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(
            status=JobStatus.failed.value,
            error_message=message[:500],
            completed_at=now,
            updated_at=now,
        )
    )
    session.commit()
