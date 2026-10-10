import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, load_settings
from app.core.exceptions import (
    PermanentJobError,
    RetryableJobError,
    SourceError,
    SourceReadError,
    UnsupportedJobTypeError,
    ValidationError,
)
from app.db.models import Event, Lineup, Match, PassDetail, ProcessingJob, ShotDetail
from app.jobs import executor, handlers
from app.jobs.executor import claim_job, classify, execute_job
from tests.jobs.conftest import FakeQueue

WORKER = "test-worker:1"


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def settings(v2_source_dir: Path) -> Settings:
    return load_settings(statsbomb_source_dir=v2_source_dir, _env_file=None)


@pytest.fixture
def run(
    session: Session,
    session_factory: sessionmaker[Session],
    queue: FakeQueue,
    settings: Settings,
) -> Callable[[ProcessingJob], str]:
    def _run(job: ProcessingJob) -> str:
        outcome = execute_job(
            job.id,
            session_factory=session_factory,
            queue=queue,
            settings=settings,
            worker=WORKER,
        )
        session.expire_all()
        return outcome

    return _run


def make_job(
    session: Session, job_type: str = "ingest_match", match: int = 9001, **fields: Any
) -> ProcessingJob:
    job = ProcessingJob(
        job_type=job_type,
        status=fields.pop("status", "pending"),
        statsbomb_match_id=match,
        max_attempts=fields.pop("max_attempts", 3),
        payload=fields.pop("payload", {"statsbomb_match_id": match}),
        **fields,
    )
    session.add(job)
    session.commit()
    return job


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def failing_handler(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[int]]:
    """Replace the ingest handler with one that raises on the listed attempts."""

    def install(*errors: Exception | None) -> list[int]:
        calls: list[int] = []

        def handler(session: Session, job: ProcessingJob, settings: Settings) -> dict[str, Any]:
            error = errors[min(len(calls), len(errors) - 1)]
            calls.append(job.attempt_count)
            if error is not None:
                raise error
            return {"ok": True}

        monkeypatch.setitem(handlers.HANDLERS, "ingest_match", handler)
        return calls

    return install


class TestLifecycle:
    def test_pending_to_running_to_completed_with_a_result(
        self,
        session: Session,
        session_factory: sessionmaker[Session],
        run: Callable[[ProcessingJob], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = make_job(session)
        seen: dict[str, Any] = {}
        real = handlers.HANDLERS["ingest_match"]

        def spy(s: Session, j: ProcessingJob, settings: Settings) -> dict[str, Any]:
            with session_factory() as other:
                row = other.get(ProcessingJob, j.id)
                assert row is not None
                seen.update(status=row.status, attempt=row.attempt_count, started=row.started_at)
            return real(s, j, settings)

        monkeypatch.setitem(handlers.HANDLERS, "ingest_match", spy)
        assert job.status == "pending" and job.attempt_count == 0

        outcome = run(job)

        assert seen["status"] == "running"
        assert seen["attempt"] == 1 and seen["started"] is not None
        assert outcome == "completed"
        assert job.status == "completed"
        assert job.attempt_count == 1
        assert job.started_at is not None and job.completed_at is not None
        assert job.completed_at >= job.started_at
        assert job.error_message is None

    def test_the_result_summarises_the_import(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(session)
        run(job)

        assert job.result is not None
        assert job.result["import_status"] == "success"
        assert job.result["events_imported"] == 10
        assert job.result["passes_imported"] == 4
        assert job.result["shots_imported"] == 3
        assert job.result["lineups_imported"] == 5
        assert job.result["validation_failures"] == 0
        assert job.result["source_manifest_id"] is None
        assert job.result["worker"] == WORKER

    def test_the_job_is_linked_to_the_imported_match(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(session)
        run(job)

        match = session.scalar(select(Match).where(Match.statsbomb_id == 9001))
        assert match is not None
        assert job.match_id == match.id

    def test_malformed_records_are_reported_without_failing_the_job(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(session, match=9002)
        run(job)

        assert job.status == "completed"
        assert job.result is not None
        assert job.result["import_status"] == "partial"
        assert job.result["validation_failures"] == 4
        assert job.result["passes_skipped"] == 2 and job.result["shots_skipped"] == 2
        reasons = " ".join(item["reason"] for item in job.result["rejected_sample"])
        assert "pass.end_location" in reasons
        assert "events/9002.json" not in json.dumps(job.result)

    def test_a_job_can_target_another_season_through_its_payload(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(
            session, match=9003, payload={"statsbomb_match_id": 9003, "season": "2016/2017"}
        )

        assert run(job) == "completed"
        assert count(session, Match) == 1


class TestIdempotency:
    def test_redelivering_a_finished_job_does_nothing(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(session)
        run(job)
        before = (count(session, Event), count(session, Lineup), job.attempt_count)

        assert run(job) == "skipped"

        assert (count(session, Event), count(session, Lineup), job.attempt_count) == before
        assert job.status == "completed"

    def test_importing_the_same_match_twice_creates_no_duplicates(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        run(make_job(session))
        first = (
            count(session, Event),
            count(session, Lineup),
            count(session, PassDetail),
            count(session, ShotDetail),
            count(session, Match),
        )

        again = make_job(session)
        assert run(again) == "completed"

        assert (
            count(session, Event),
            count(session, Lineup),
            count(session, PassDetail),
            count(session, ShotDetail),
            count(session, Match),
        ) == first

    def test_per_match_jobs_equal_a_whole_season_import(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        for match in (9001, 9002):
            run(make_job(session, match=match))

        assert count(session, Match) == 2
        assert count(session, Event) == 19
        assert count(session, Lineup) == 11
        assert count(session, PassDetail) == 8
        assert count(session, ShotDetail) == 6

    def test_a_running_job_cannot_be_claimed_again(
        self, session: Session, session_factory: sessionmaker[Session]
    ) -> None:
        job = make_job(session)

        first = claim_job(session_factory, job.id)
        second = claim_job(session_factory, job.id)

        assert first is not None and first.attempt == 1
        assert second is None

    @pytest.mark.parametrize("status", ["running", "completed", "failed"])
    def test_only_pending_and_retrying_jobs_are_claimable(
        self, session: Session, session_factory: sessionmaker[Session], status: str
    ) -> None:
        job = make_job(session, status=status)

        assert claim_job(session_factory, job.id) is None

    def test_an_unknown_job_id_is_skipped(
        self, session_factory: sessionmaker[Session], queue: FakeQueue, settings: Settings
    ) -> None:
        outcome = execute_job(
            uuid.uuid4(), session_factory=session_factory, queue=queue, settings=settings
        )

        assert outcome == "skipped"


class TestRetries:
    def test_a_transient_failure_retries_then_completes(
        self,
        session: Session,
        run: Callable[[ProcessingJob], str],
        queue: FakeQueue,
        failing_handler: Callable[..., list[int]],
    ) -> None:
        failing_handler(RetryableJobError("The match source data could not be read."), None)
        job = make_job(session)

        assert run(job) == "retrying"
        assert job.status == "retrying"
        assert job.attempt_count == 1
        assert job.error_message == "The match source data could not be read."
        assert job.completed_at is None
        assert queue.sent == [(job.id, 5)]

        assert run(job) == "completed"
        assert job.status == "completed"
        assert job.attempt_count == 2
        assert job.error_message is None

    def test_retries_stop_at_the_limit_with_exponential_backoff(
        self,
        session: Session,
        run: Callable[[ProcessingJob], str],
        queue: FakeQueue,
        failing_handler: Callable[..., list[int]],
    ) -> None:
        calls = failing_handler(RetryableJobError("The match source data could not be read."))
        job = make_job(session)

        outcomes = [run(job), run(job), run(job)]

        assert outcomes == ["retrying", "retrying", "failed"]
        assert calls == [1, 2, 3]
        assert [countdown for _, countdown in queue.sent] == [5, 30]
        assert job.status == "failed"
        assert job.attempt_count == 3
        assert job.completed_at is not None
        assert "Gave up after 3 attempts" in (job.error_message or "")
        assert run(job) == "skipped"

    def test_a_longer_attempt_budget_uses_the_two_minute_delay(
        self,
        session: Session,
        run: Callable[[ProcessingJob], str],
        queue: FakeQueue,
        failing_handler: Callable[..., list[int]],
    ) -> None:
        failing_handler(RetryableJobError("x"))
        job = make_job(session, max_attempts=5)

        for _ in range(5):
            run(job)

        assert [countdown for _, countdown in queue.sent] == [5, 30, 120, 120]

    def test_a_permanent_failure_is_not_retried(
        self,
        session: Session,
        run: Callable[[ProcessingJob], str],
        queue: FakeQueue,
        failing_handler: Callable[..., list[int]],
    ) -> None:
        calls = failing_handler(
            PermanentJobError("The match record in the source data is invalid.")
        )
        job = make_job(session)

        assert run(job) == "failed"

        assert calls == [1]
        assert job.status == "failed"
        assert job.attempt_count == 1
        assert job.error_message == "The match record in the source data is invalid."
        assert job.completed_at is not None
        assert queue.sent == []

    def test_a_missing_match_in_the_source_fails_without_retrying(
        self, session: Session, run: Callable[[ProcessingJob], str], queue: FakeQueue
    ) -> None:
        job = make_job(session, match=424242)

        assert run(job) == "failed"

        assert job.attempt_count == 1
        assert job.error_message == "The match source data is missing or malformed."
        assert queue.sent == []
        assert count(session, Match) == 0

    def test_a_failed_attempt_leaves_no_partial_data_behind(
        self,
        session: Session,
        run: Callable[[ProcessingJob], str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = make_job(session)

        def explode(*_a: object, **_k: object) -> None:
            raise OperationalError("INSERT", {}, Exception("connection lost"))

        monkeypatch.setattr("app.ingestion.persistence.IngestionRepository.upsert_events", explode)

        assert run(job) == "retrying"
        assert count(session, Match) == 0
        assert count(session, Event) == 0

    def test_an_unavailable_broker_leaves_the_job_retrying_for_reconciliation(
        self,
        session: Session,
        run: Callable[[ProcessingJob], str],
        queue: FakeQueue,
        failing_handler: Callable[..., list[int]],
    ) -> None:
        failing_handler(RetryableJobError("x"))
        queue.fail_with = ConnectionError("redis down")
        job = make_job(session)

        assert run(job) == "retrying"
        assert job.status == "retrying"

    def test_an_unreachable_database_at_claim_time_defers_the_message(
        self, queue: FakeQueue, settings: Settings
    ) -> None:
        def broken() -> Session:
            raise OperationalError("connect", {}, Exception("refused"))

        job_id = uuid.uuid4()
        outcome = execute_job(job_id, session_factory=broken, queue=queue, settings=settings)

        assert outcome == "deferred"
        assert queue.sent == [(job_id, 5)]


class TestSafeErrors:
    @pytest.mark.parametrize(
        ("error", "retryable"),
        [
            (RetryableJobError("later"), True),
            (PermanentJobError("never"), False),
            (SourceReadError("disk"), True),
            (OSError("nfs timeout"), True),
            (TimeoutError("slow"), True),
            (OperationalError("SELECT 1", {}, Exception("deadlock detected")), True),
            (IntegrityError("INSERT", {}, Exception("duplicate key")), True),
            (SourceError("/srv/data/raw/matches/16/1.json is malformed"), False),
            (ValidationError("bad"), False),
            (UnsupportedJobTypeError("nope"), False),
            (ValueError("a bug"), False),
        ],
    )
    def test_classification(self, error: Exception, retryable: bool) -> None:
        assert classify(error)[0] is retryable

    @pytest.mark.parametrize(
        "error",
        [
            OperationalError("SELECT 1", {}, Exception("password=hunter2 host=10.0.0.5")),
            SourceError("/srv/secret/path/matches/1.json: invalid"),
            RuntimeError("postgresql://user:pw@db:5432/x refused"),
        ],
    )
    def test_the_client_message_never_carries_the_cause(self, error: Exception) -> None:
        message = classify(error)[1]

        for leak in ("hunter2", "10.0.0.5", "/srv", "secret", "pw@", "postgresql://"):
            assert leak not in message

    def test_a_missing_source_directory_is_reported_without_its_path(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(session, source_path="does/not/exist")

        assert run(job) == "failed"

        assert job.error_message == "The match source data is missing or malformed."
        assert "/" not in (job.error_message or "")

    def test_a_path_that_escapes_the_source_root_is_refused_at_execution_too(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(session, source_path="../outside")

        assert run(job) == "failed"
        assert job.error_message == "The source path is not allowed."

    def test_an_unsupported_job_type_fails_permanently(
        self, session: Session, run: Callable[[ProcessingJob], str], queue: FakeQueue
    ) -> None:
        job = make_job(session, job_type="mystery_job")

        assert run(job) == "failed"
        assert job.error_message == "This job type is not supported."
        assert queue.sent == []

    def test_an_unexpected_bug_is_failed_with_a_generic_message(
        self,
        session: Session,
        run: Callable[[ProcessingJob], str],
        failing_handler: Callable[..., list[int]],
    ) -> None:
        failing_handler(ZeroDivisionError("division by zero in /app/secret.py"))
        job = make_job(session)

        assert run(job) == "failed"
        assert job.error_message == "An unexpected error occurred."


class TestComputeMatchStatistics:
    @pytest.fixture(autouse=True)
    def _imported(self, session: Session, run: Callable[[ProcessingJob], str]) -> None:
        for match in (9001, 9002):
            run(make_job(session, match=match))

    def _teams(
        self, session: Session, run: Callable[[ProcessingJob], str], match: int
    ) -> dict[int, Any]:
        job = make_job(session, "compute_match_statistics", match)
        assert run(job) == "completed"
        assert job.result is not None
        return {team["team_id"]: team for team in job.result["teams"]}

    def test_totals_for_each_team(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        teams = self._teams(session, run, 9001)

        alpha, beta = teams[101], teams[102]
        assert (alpha["passes_attempted"], alpha["passes_completed"]) == (3, 2)
        assert alpha["pass_completion_pct"] == pytest.approx(200 / 3)
        assert (alpha["shots"], alpha["goals"]) == (2, 1)
        assert alpha["xg"] == pytest.approx(0.35)
        assert (beta["passes_attempted"], beta["passes_completed"]) == (1, 1)
        assert (beta["shots"], beta["goals"], beta["xg"]) == (1, 0, 0)

    def test_a_second_match_has_its_own_totals(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        teams = self._teams(session, run, 9002)

        assert (teams[102]["passes_attempted"], teams[102]["passes_completed"]) == (3, 2)
        assert (teams[102]["shots"], teams[102]["goals"]) == (2, 1)
        assert teams[102]["xg"] == pytest.approx(1.0)
        assert (teams[101]["passes_attempted"], teams[101]["passes_completed"]) == (1, 0)
        assert teams[101]["pass_completion_pct"] == 0.0

    def test_match_totals_add_up_to_the_season_totals(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        first, second = self._teams(session, run, 9001), self._teams(session, run, 9002)

        alpha_passes = first[101]["passes_attempted"] + second[101]["passes_attempted"]
        alpha_xg = first[101]["xg"] + second[101]["xg"]
        assert alpha_passes == 4
        assert alpha_xg == pytest.approx(0.40)

    def test_the_result_is_json_serialisable_and_stored(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        job = make_job(session, "compute_match_statistics", 9001)
        run(job)

        assert json.loads(json.dumps(job.result)) == job.result
        assert [t["team_id"] for t in job.result["teams"]] == [101, 102]  # type: ignore[index]

    def test_it_is_repeatable_and_does_not_modify_football_data(
        self, session: Session, run: Callable[[ProcessingJob], str]
    ) -> None:
        before = (count(session, Event), count(session, PassDetail), count(session, ShotDetail))

        first = self._teams(session, run, 9001)
        second = self._teams(session, run, 9001)

        assert first == second
        assert (
            count(session, Event),
            count(session, PassDetail),
            count(session, ShotDetail),
        ) == before

    def test_a_match_that_was_never_imported_fails_permanently(
        self, session: Session, run: Callable[[ProcessingJob], str], queue: FakeQueue
    ) -> None:
        job = make_job(session, "compute_match_statistics", 777)

        assert run(job) == "failed"
        assert job.error_message == "The match has not been imported."
        assert queue.sent == []


def test_a_retry_delay_repeats_the_last_value(settings: Settings) -> None:
    assert [executor.retry_delay(settings, n) for n in (1, 2, 3, 4, 9)] == [5, 30, 120, 120, 120]
