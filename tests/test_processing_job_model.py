import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ACTIVE_STATUSES, TERMINAL_STATUSES, JobStatus, JobType, ProcessingJob


def _job(
    status: str = "pending",
    match: int | None = 18245,
    job_type: str = "ingest_match",
    **extra: object,
) -> ProcessingJob:
    return ProcessingJob(
        job_type=job_type,
        status=status,
        statsbomb_match_id=match,
        max_attempts=3,
        payload={"statsbomb_match_id": match},
        **extra,
    )


def test_defaults_are_populated(session: Session) -> None:
    job = _job()
    session.add(job)
    session.flush()
    session.refresh(job)

    assert isinstance(job.id, uuid.UUID)
    assert job.status == "pending"
    assert job.attempt_count == 0
    assert job.created_at is not None and job.updated_at is not None
    assert job.result is None and job.error_message is None and job.queue_task_id is None
    assert job.started_at is None and job.completed_at is None


def test_the_status_vocabulary_matches_the_spec() -> None:
    assert {s.value for s in JobStatus} == {"pending", "running", "completed", "failed", "retrying"}
    assert {t.value for t in JobType} == {"ingest_match", "compute_match_statistics"}
    assert set(ACTIVE_STATUSES) | set(TERMINAL_STATUSES) == set(JobStatus)
    assert not set(ACTIVE_STATUSES) & set(TERMINAL_STATUSES)


@pytest.mark.parametrize("status", [s.value for s in JobStatus])
def test_every_defined_status_is_accepted(session: Session, status: str) -> None:
    session.add(_job(status=status))
    session.flush()


@pytest.mark.parametrize("status", ["queued", "done", "PENDING", ""])
def test_any_other_status_is_rejected(session: Session, status: str) -> None:
    session.add(_job(status=status))

    with pytest.raises(IntegrityError):
        session.flush()


def test_attempt_counters_are_bounded(session: Session) -> None:
    session.add(_job(attempt_count=-1))

    with pytest.raises(IntegrityError):
        session.flush()


class TestActiveJobGuard:
    @pytest.mark.parametrize("status", [s.value for s in ACTIVE_STATUSES])
    def test_a_second_active_job_for_the_same_match_is_rejected(
        self, session: Session, status: str
    ) -> None:
        session.add(_job(status=status))
        session.flush()

        session.add(_job(status="pending"))
        with pytest.raises(IntegrityError):
            session.flush()

    @pytest.mark.parametrize("status", [s.value for s in TERMINAL_STATUSES])
    def test_finished_jobs_do_not_block_a_new_one(self, session: Session, status: str) -> None:
        session.add(_job(status=status))
        session.add(_job(status="pending"))
        session.flush()

        assert len(session.scalars(select(ProcessingJob)).all()) == 2

    def test_different_matches_can_be_active_at_once(self, session: Session) -> None:
        session.add_all([_job(match=1), _job(match=2), _job(match=3)])
        session.flush()

    def test_different_job_types_for_one_match_can_be_active_at_once(
        self, session: Session
    ) -> None:
        session.add(_job())
        session.add(_job(job_type="compute_match_statistics"))
        session.flush()

    def test_a_finished_job_frees_the_slot(self, session: Session) -> None:
        first = _job()
        session.add(first)
        session.flush()

        first.status = "completed"
        first.completed_at = datetime.now(UTC)
        session.add(_job())
        session.flush()


def test_json_payload_and_result_round_trip(session: Session) -> None:
    job = _job()
    job.result = {"events_imported": 3497, "teams": [{"id": 220, "xg": 1.68}]}
    session.add(job)
    session.flush()
    session.expire_all()

    stored = session.scalar(select(ProcessingJob))
    assert stored is not None
    assert stored.result == {"events_imported": 3497, "teams": [{"id": 220, "xg": 1.68}]}
    assert stored.payload == {"statsbomb_match_id": 18245}
