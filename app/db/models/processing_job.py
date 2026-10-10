"""Durable record of a user-requested unit of background work."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JsonDict, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.match import Match


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    retrying = "retrying"


class JobType(StrEnum):
    ingest_match = "ingest_match"
    compute_match_statistics = "compute_match_statistics"


ACTIVE_STATUSES = (JobStatus.pending, JobStatus.running, JobStatus.retrying)
TERMINAL_STATUSES = (JobStatus.completed, JobStatus.failed)

_ACTIVE_SQL = "status IN ('pending', 'running', 'retrying')"
_STATUS_SQL = "status IN ('pending', 'running', 'completed', 'failed', 'retrying')"


class ProcessingJob(TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint(_STATUS_SQL, name="status"),
        CheckConstraint("attempt_count >= 0 AND max_attempts >= 1", name="attempts"),
        # At most one active job per type and match, so the same match cannot be
        # processed twice at once. Finished jobs leave the index and may be repeated.
        Index(
            "uq_processing_jobs_active_match",
            "job_type",
            "statsbomb_match_id",
            unique=True,
            postgresql_where=text(_ACTIVE_SQL),
            sqlite_where=text(_ACTIVE_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobStatus.pending.value, index=True
    )
    # The StatsBomb id is known at submission; the internal FK only once the match is imported.
    statsbomb_match_id: Mapped[int | None] = mapped_column(Integer)
    match_id: Mapped[int | None] = mapped_column(
        ForeignKey("matches.id", ondelete="SET NULL"), index=True
    )
    source_path: Mapped[str | None] = mapped_column(String(1024))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDict, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonDict)
    error_message: Mapped[str | None] = mapped_column(String(500))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_task_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    match: Mapped[Match | None] = relationship()

    __mapper_args__ = {"eager_defaults": True}


# Listing and recovery both filter on recency.
Index("ix_processing_jobs_created_at", ProcessingJob.created_at)
