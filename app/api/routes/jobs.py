"""Job submission and status endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import PaginationDep, SessionDep
from app.api.schemas.common import Page
from app.api.schemas.jobs import JobCreate, JobRead
from app.core.config import Settings, get_settings
from app.core.exceptions import JobStateError, JobSubmissionError
from app.db.models import JobStatus, JobType
from app.jobs import repository
from app.jobs.queue import CeleryJobQueue, JobQueue
from app.jobs.service import JobService
from app.repositories import queries

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_job_queue() -> JobQueue:
    return CeleryJobQueue()


QueueDep = Annotated[JobQueue, Depends(get_job_queue)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unavailable(error: JobSubmissionError) -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{error} The job was recorded as failed (job {error.job_id}); retry it later.",
    )


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a processing job",
    description=(
        "Records the job in PostgreSQL, queues it, and returns `202 Accepted`. "
        "If an active job of the same type already exists for the match, that job is "
        "returned with `200 OK` instead of queueing duplicate work. "
        "A `compute_match_statistics` job needs the match to be imported already."
    ),
    responses={
        200: {"description": "An active job for this match already exists; it is returned."},
        404: {"description": "Match not found (statistics jobs only)."},
        503: {"description": "The job could not be queued; it was recorded as failed."},
    },
)
def submit_job(
    body: JobCreate,
    response: Response,
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
) -> JobRead:
    if body.job_type is JobType.compute_match_statistics and (
        queries.get_match(session, body.match_id) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Match not found")

    try:
        submission = JobService(session, queue, settings).submit(
            body.job_type,
            body.match_id,
            source_path=body.source_path,
            competition=body.competition,
            season=body.season,
        )
    except JobSubmissionError as exc:
        raise _unavailable(exc) from exc

    if not submission.created:
        response.status_code = status.HTTP_200_OK
    return JobRead.model_validate(submission.job)


@router.get(
    "/{job_id}",
    response_model=JobRead,
    summary="Get a job",
    description="Status, attempts, result, and a safe failure summary for one job.",
    responses={404: {"description": "Job not found."}},
)
def get_job(job_id: uuid.UUID, session: SessionDep) -> JobRead:
    job = repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobRead.model_validate(job)


@router.get(
    "",
    response_model=Page[JobRead],
    summary="List jobs",
    description="Paginated, newest first. `match_id` is a StatsBomb match identifier.",
)
def list_jobs(
    session: SessionDep,
    pagination: PaginationDep,
    status_filter: Annotated[
        JobStatus | None, Query(alias="status", description="Only jobs in this status.")
    ] = None,
    job_type: Annotated[JobType | None, Query(description="Only jobs of this type.")] = None,
    match_id: Annotated[int | None, Query(description="StatsBomb match identifier.")] = None,
) -> Page[JobRead]:
    items, total = repository.list_jobs(
        session,
        status=status_filter.value if status_filter else None,
        job_type=job_type.value if job_type else None,
        statsbomb_match_id=match_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[JobRead](
        items=[JobRead.model_validate(item) for item in items],
        limit=pagination.limit,
        offset=pagination.offset,
        total=total,
    )


@router.post(
    "/{job_id}/retry",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed job",
    description="Only a failed job with attempts remaining can be retried.",
    responses={
        404: {"description": "Job not found."},
        409: {"description": "The job is not failed, has no attempts left, or conflicts."},
        503: {"description": "The job could not be queued; it was recorded as failed."},
    },
)
def retry_job(
    job_id: uuid.UUID, session: SessionDep, queue: QueueDep, settings: SettingsDep
) -> JobRead:
    try:
        job = JobService(session, queue, settings).retry(job_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found") from exc
    except JobStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except JobSubmissionError as exc:
        raise _unavailable(exc) from exc
    return JobRead.model_validate(job)
