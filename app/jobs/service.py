"""Job submission and retry: the API-side half of the job lifecycle.

The durable record is committed *before* the broker is touched. If queueing then
fails, the record is marked failed instead of the job silently vanishing, and
the caller is told, so a job that was never queued is never reported as accepted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import JobStateError, JobSubmissionError
from app.core.logging import get_logger
from app.db.models import JobStatus, JobType, ProcessingJob
from app.jobs import repository
from app.jobs.queue import JobQueue

logger = get_logger(__name__)

QUEUE_FAILURE_MESSAGE = "The job could not be queued."


@dataclass(frozen=True, slots=True)
class Submission:
    job: ProcessingJob
    created: bool  # False when an already active job for the same match was reused


class JobService:
    def __init__(self, session: Session, queue: JobQueue, settings: Settings) -> None:
        self.session = session
        self.queue = queue
        self.settings = settings

    def submit(
        self,
        job_type: JobType,
        statsbomb_match_id: int,
        *,
        source_path: str | None = None,
        competition: str | None = None,
        season: str | None = None,
    ) -> Submission:
        existing = repository.find_active_job(self.session, job_type.value, statsbomb_match_id)
        if existing is not None:
            return Submission(existing, created=False)

        payload: dict[str, Any] = {"statsbomb_match_id": statsbomb_match_id}
        for key, value in (("competition", competition), ("season", season)):
            if value:
                payload[key] = value

        job = ProcessingJob(
            job_type=job_type.value,
            status=JobStatus.pending.value,
            statsbomb_match_id=statsbomb_match_id,
            source_path=source_path or None,
            payload=payload,
            max_attempts=self.settings.job_max_attempts,
        )
        self.session.add(job)
        try:
            self.session.commit()
        except IntegrityError:
            # A concurrent request won the race for the same match; reuse its job.
            self.session.rollback()
            winner = repository.find_active_job(self.session, job_type.value, statsbomb_match_id)
            if winner is None:
                raise
            return Submission(winner, created=False)

        self._enqueue(job)
        return Submission(job, created=True)

    def retry(self, job_id: uuid.UUID) -> ProcessingJob:
        job = repository.get_job(self.session, job_id)
        if job is None:
            raise LookupError("Job not found")
        if job.status != JobStatus.failed.value:
            raise JobStateError(f"Only a failed job can be retried; this job is {job.status}.")
        if job.attempt_count >= job.max_attempts:
            raise JobStateError("This job has used all of its attempts.")

        job.status = JobStatus.retrying.value
        job.completed_at = None
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise JobStateError(
                "Another active job already exists for this match; wait for it or reuse it."
            ) from exc

        self._enqueue(job)
        return job

    def _enqueue(self, job: ProcessingJob) -> None:
        job_id = job.id
        try:
            task_id = self.queue.enqueue(job_id)
        except Exception as exc:
            # The cause (broker address, credentials, stack) stays in the server log.
            logger.error("Queueing job %s failed: %r", job_id, exc, exc_info=True)
            repository.mark_failed(self.session, job_id, QUEUE_FAILURE_MESSAGE)
            self.session.refresh(job)
            raise JobSubmissionError(QUEUE_FAILURE_MESSAGE, job_id=job_id) from exc

        repository.record_queue_task_id(self.session, job_id, task_id)
        self.session.refresh(job)
        logger.info("Queued job %s (%s) as task %s", job_id, job.job_type, task_id)
