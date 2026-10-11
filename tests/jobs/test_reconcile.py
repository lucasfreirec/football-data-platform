from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from app.core.config import load_settings
from app.db.models import Event, ProcessingJob
from app.jobs import cli
from app.jobs.executor import execute_job
from app.jobs.reconcile import (
    NO_ATTEMPTS_LEFT_MESSAGE,
    WORKER_LOST_MESSAGE,
    reconcile_stale,
)
from tests.jobs.conftest import FakeQueue

OLD = timedelta(minutes=40)
THRESHOLD = timedelta(minutes=15)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def make(
    session: Session, status: str, *, age: timedelta = OLD, match: int = 9001, **fields: Any
) -> ProcessingJob:
    stamp = datetime.now(UTC) - age
    job = ProcessingJob(
        job_type="ingest_match",
        status=status,
        statsbomb_match_id=match,
        max_attempts=fields.pop("max_attempts", 3),
        attempt_count=fields.pop("attempt_count", 1 if status == "running" else 0),
        payload={"statsbomb_match_id": match},
        started_at=stamp if status == "running" else None,
        updated_at=stamp,
        **fields,
    )
    session.add(job)
    session.commit()
    return job


def reconcile(factory: sessionmaker[Session], queue: FakeQueue, **options: Any) -> Any:
    options.setdefault("older_than", THRESHOLD)
    return reconcile_stale(factory, queue, **options)


class TestStaleRunningJobs:
    def test_a_stale_running_job_with_attempts_left_is_requeued(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        job = make(session, "running", attempt_count=1)

        report = reconcile(session_factory, queue)

        session.refresh(job)
        assert job.status == "retrying"
        assert job.error_message == WORKER_LOST_MESSAGE
        assert job.completed_at is None
        assert (report.stale_running, report.requeued, report.failed) == (1, 1, 0)
        assert queue.job_ids == [job.id]

    def test_a_stale_running_job_without_attempts_left_is_failed(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        job = make(session, "running", attempt_count=3, max_attempts=3)

        report = reconcile(session_factory, queue)

        session.refresh(job)
        assert job.status == "failed"
        assert job.error_message == NO_ATTEMPTS_LEFT_MESSAGE
        assert job.completed_at is not None
        assert (report.requeued, report.failed) == (0, 1)
        assert queue.sent == []

    def test_a_recently_started_running_job_is_left_alone(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        job = make(session, "running", age=timedelta(minutes=2))

        report = reconcile(session_factory, queue)

        session.refresh(job)
        assert job.status == "running"
        assert report.stale_running == 0
        assert queue.sent == []

    @pytest.mark.parametrize("status", ["completed", "failed"])
    def test_finished_jobs_are_never_touched(
        self, session: Session, session_factory: sessionmaker[Session], status: str
    ) -> None:
        queue = FakeQueue()
        job = make(session, status)

        report = reconcile(session_factory, queue)

        session.refresh(job)
        assert job.status == status
        assert report.changed == 0 and queue.sent == []

    def test_the_threshold_decides_what_is_stale(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        job = make(session, "running", age=timedelta(minutes=30))

        assert reconcile(session_factory, FakeQueue(), older_than=timedelta(hours=1)).changed == 0
        session.refresh(job)
        assert job.status == "running"

        assert (
            reconcile(session_factory, FakeQueue(), older_than=timedelta(minutes=10)).changed == 1
        )


class TestOrphanedQueuedJobs:
    @pytest.mark.parametrize("status", ["pending", "retrying"])
    def test_an_old_queued_job_is_requeued_unchanged(
        self, session: Session, session_factory: sessionmaker[Session], status: str
    ) -> None:
        queue = FakeQueue()
        job = make(session, status)

        report = reconcile(session_factory, queue)

        session.refresh(job)
        assert job.status == status
        assert report.orphaned_requeued == 1
        assert queue.job_ids == [job.id]

    def test_a_second_sweep_does_not_requeue_it_again_straight_away(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        make(session, "pending")

        reconcile(session_factory, queue)
        reconcile(session_factory, queue)

        assert len(queue.sent) == 1

    def test_a_recent_queued_job_is_left_alone(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        make(session, "pending", age=timedelta(minutes=1))

        assert reconcile(session_factory, queue).changed == 0
        assert queue.sent == []

    def test_queued_jobs_can_be_excluded(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        make(session, "pending")
        make(session, "running", match=9002)

        report = reconcile(session_factory, queue, include_queued=False)

        assert (report.orphaned_requeued, report.requeued) == (0, 1)


class TestBehaviour:
    def test_a_dry_run_reports_without_changing_anything(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        running = make(session, "running", attempt_count=1)
        exhausted = make(session, "running", match=9002, attempt_count=3)
        make(session, "pending", match=9003)

        report = reconcile(session_factory, queue, dry_run=True)

        session.refresh(running)
        session.refresh(exhausted)
        assert (running.status, exhausted.status) == ("running", "running")
        assert (report.requeued, report.failed, report.orphaned_requeued) == (1, 1, 1)
        assert queue.sent == []

    def test_a_broker_outage_leaves_the_job_for_the_next_sweep(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        queue = FakeQueue()
        queue.fail_with = ConnectionError("redis down")
        job = make(session, "running", attempt_count=1)

        report = reconcile(session_factory, queue)

        session.refresh(job)
        assert job.status == "retrying"
        assert report.enqueue_failures == 1

        queue.fail_with = None
        job.updated_at = datetime.now(UTC) - OLD
        session.commit()
        again = reconcile(session_factory, queue)
        assert again.orphaned_requeued == 1 and queue.job_ids == [job.id]

    def test_a_job_that_finishes_during_the_sweep_is_not_overwritten(
        self,
        session: Session,
        session_factory: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = make(session, "running", attempt_count=1)
        from app.jobs import reconcile as module

        real = module._stale_running_ids

        def finish_first(s: Session, cutoff: datetime) -> list[Any]:
            ids = real(s, cutoff)
            with session_factory() as other:
                row = other.get(ProcessingJob, job.id)
                assert row is not None
                row.status = "completed"
                other.commit()
            return ids

        monkeypatch.setattr(module, "_stale_running_ids", finish_first)
        queue = FakeQueue()

        report = reconcile(session_factory, queue)

        session.refresh(job)
        assert job.status == "completed"
        assert report.changed == 0 and queue.sent == []


def test_a_job_orphaned_by_a_dead_worker_is_recovered_and_completed(
    session: Session,
    session_factory: sessionmaker[Session],
    v2_source_dir: Path,
    tmp_path: Path,
) -> None:
    """The point of recovery: the job finishes, and only once."""
    settings = load_settings(statsbomb_source_dir=v2_source_dir, _env_file=None)
    queue = FakeQueue()
    # A worker claimed this job 40 minutes ago and was never heard from again.
    job = make(session, "running", attempt_count=1)

    reconcile(session_factory, queue)
    assert job.id in queue.job_ids
    outcome = execute_job(job.id, session_factory=session_factory, queue=queue, settings=settings)

    session.refresh(job)
    assert outcome == "completed"
    assert job.status == "completed"
    assert job.attempt_count == 2
    assert job.error_message is None
    assert session.scalar(select(func.count()).select_from(Event)) == 10

    # The original message turning up late does nothing.
    assert (
        execute_job(job.id, session_factory=session_factory, queue=queue, settings=settings)
        == "skipped"
    )
    assert session.scalar(select(func.count()).select_from(Event)) == 10


class TestCommand:
    @pytest.fixture
    def wired(
        self, monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]
    ) -> FakeQueue:
        queue = FakeQueue()
        monkeypatch.setattr(cli, "get_session_factory", lambda: session_factory)
        monkeypatch.setattr(cli, "CeleryJobQueue", lambda: queue)
        monkeypatch.setattr(cli, "check_database_connection", lambda: True)
        return queue

    def test_it_reports_and_recovers(self, session: Session, wired: FakeQueue) -> None:
        job = make(session, "running", attempt_count=1)

        result = CliRunner().invoke(cli.app, ["reconcile-stale", "--older-than-minutes", "15"])

        assert result.exit_code == 0, result.output
        assert "re-queued" in result.output
        session.refresh(job)
        assert job.status == "retrying"
        assert wired.job_ids == [job.id]

    def test_dry_run_changes_nothing(self, session: Session, wired: FakeQueue) -> None:
        job = make(session, "running", attempt_count=1)

        result = CliRunner().invoke(cli.app, ["reconcile-stale", "--dry-run"])

        assert result.exit_code == 0
        assert "dry run" in result.output and "would be" in result.output
        session.refresh(job)
        assert job.status == "running" and wired.sent == []

    def test_the_default_threshold_comes_from_settings(
        self, session: Session, wired: FakeQueue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STALE_JOB_MINUTES", "7")
        make(session, "running", age=timedelta(minutes=10), attempt_count=1)

        result = CliRunner().invoke(cli.app, ["reconcile-stale"])

        assert "over 7 minute(s)" in result.output
        assert len(wired.sent) == 1

    @pytest.mark.parametrize("value", ["0", "-5", "soon"])
    def test_an_invalid_threshold_is_rejected(self, wired: FakeQueue, value: str) -> None:
        result = CliRunner().invoke(cli.app, ["reconcile-stale", "--older-than-minutes", value])

        assert result.exit_code != 0

    def test_a_broker_outage_exits_non_zero(self, session: Session, wired: FakeQueue) -> None:
        wired.fail_with = ConnectionError("down")
        make(session, "running", attempt_count=1)

        result = CliRunner().invoke(cli.app, ["reconcile-stale"])

        assert result.exit_code == cli.EXIT_REQUEUE_FAILED
        assert "could not be queued" in result.output

    def test_an_unreachable_database_exits_non_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "check_database_connection", lambda: False)

        result = CliRunner().invoke(cli.app, ["reconcile-stale"])

        assert result.exit_code == cli.EXIT_DATABASE
