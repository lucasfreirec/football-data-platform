"""The boundary between the API and the task queue."""

from __future__ import annotations

import uuid
from typing import Protocol

from app.core.config import get_settings
from app.jobs.celery_app import RUN_JOB_TASK, celery_app


class JobQueue(Protocol):
    """Hands a job id to the queue. The job's data stays in PostgreSQL."""

    def enqueue(self, job_id: uuid.UUID, *, countdown: int = 0) -> str:
        """Queue the job and return the broker's task id."""
        ...


class CeleryJobQueue:
    def enqueue(self, job_id: uuid.UUID, *, countdown: int = 0) -> str:
        result = celery_app.send_task(
            RUN_JOB_TASK,
            args=[str(job_id)],
            queue=get_settings().job_queue_name,
            countdown=countdown or None,
        )
        return str(result.id)
