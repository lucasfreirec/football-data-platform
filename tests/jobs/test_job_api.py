import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ProcessingJob
from app.ingestion.loader import SourceLoader
from app.ingestion.service import IngestionService
from app.jobs import repository
from app.jobs.service import JobService
from tests.jobs.conftest import FakeQueue

JOBS = "/api/v1/jobs"
INGEST = {"job_type": "ingest_match", "match_id": 9001}


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ProcessingJob)) or 0


def _make(session: Session, status: str, match: int = 9001, **fields: Any) -> ProcessingJob:
    job = ProcessingJob(
        job_type=fields.pop("job_type", "ingest_match"),
        status=status,
        statsbomb_match_id=match,
        max_attempts=fields.pop("max_attempts", 3),
        payload={"statsbomb_match_id": match},
        **fields,
    )
    session.add(job)
    session.commit()
    return job


class TestSubmission:
    def test_returns_202_persists_a_pending_job_and_enqueues_it(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        response = jobs_client.post(JOBS, json=INGEST)

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert body["job_type"] == "ingest_match"
        assert body["match_id"] == 9001
        assert body["attempt_count"] == 0
        assert body["max_attempts"] == get_settings().job_max_attempts
        assert body["result"] is None and body["error_message"] is None
        assert body["started_at"] is None and body["completed_at"] is None

        stored = session.get(ProcessingJob, uuid.UUID(body["id"]))
        assert stored is not None and stored.status == "pending"
        assert queue.job_ids == [stored.id]
        assert stored.queue_task_id == "task-1"

    def test_the_job_is_recorded_before_it_is_queued(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        seen: list[int] = []
        original = queue.enqueue

        def spy(job_id: uuid.UUID, *, countdown: int = 0) -> str:
            seen.append(_count(session))
            return original(job_id, countdown=countdown)

        queue.enqueue = spy  # type: ignore[method-assign]
        jobs_client.post(JOBS, json=INGEST)

        assert seen == [1]

    def test_the_response_never_exposes_broker_or_filesystem_details(
        self, jobs_client: TestClient
    ) -> None:
        body = jobs_client.post(JOBS, json={**INGEST, "source_path": "season1"}).json()

        assert "queue_task_id" not in body
        assert "task-1" not in str(body)
        assert body["source_path"] == "season1"
        assert not str(body["source_path"]).startswith("/")

    def test_optional_ingest_inputs_are_kept_in_the_payload(self, jobs_client: TestClient) -> None:
        body = jobs_client.post(
            JOBS, json={**INGEST, "competition": "Champions League", "season": "2016/2017"}
        ).json()

        assert body["payload"] == {
            "statsbomb_match_id": 9001,
            "competition": "Champions League",
            "season": "2016/2017",
        }

    @pytest.mark.parametrize(
        "body",
        [
            {"job_type": "delete_everything", "match_id": 1},
            {"job_type": "", "match_id": 1},
            {"match_id": 1},
            {"job_type": "ingest_match"},
            {"job_type": "ingest_match", "match_id": 0},
            {"job_type": "ingest_match", "match_id": -5},
            {"job_type": "ingest_match", "match_id": "abc"},
            {"job_type": "ingest_match", "match_id": 1.5},
            {"job_type": "ingest_match", "match_id": 1, "command": "rm -rf /"},
            {"job_type": "ingest_match", "match_id": 1, "source_path": "/etc"},
            {"job_type": "ingest_match", "match_id": 1, "source_path": "../secrets"},
            {"job_type": "ingest_match", "match_id": 1, "source_path": "a/../../b"},
            {"job_type": "ingest_match", "match_id": 1, "source_path": "a\\b"},
            {"job_type": "compute_match_statistics", "match_id": 1, "source_path": "x"},
            {"job_type": "compute_match_statistics", "match_id": 1, "season": "2017/2018"},
        ],
    )
    def test_invalid_requests_are_rejected_without_side_effects(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue, body: dict[str, Any]
    ) -> None:
        response = jobs_client.post(JOBS, json=body)

        assert response.status_code == 422
        assert _count(session) == 0
        assert queue.sent == []

    def test_a_non_json_body_is_rejected(self, jobs_client: TestClient) -> None:
        assert jobs_client.post(JOBS, content=b"not json").status_code == 422

    def test_statistics_for_an_unknown_match_is_404_and_creates_nothing(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        response = jobs_client.post(
            JOBS, json={"job_type": "compute_match_statistics", "match_id": 9001}
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "Match not found"}
        assert _count(session) == 0 and queue.sent == []

    def test_statistics_for_an_imported_match_is_accepted(
        self, jobs_client: TestClient, session: Session, v2_source_dir: Path, queue: FakeQueue
    ) -> None:
        IngestionService(SourceLoader(v2_source_dir), session).run()
        session.commit()

        response = jobs_client.post(
            JOBS, json={"job_type": "compute_match_statistics", "match_id": 9001}
        )

        assert response.status_code == 202
        assert len(queue.sent) == 1


class TestActiveJobReuse:
    @pytest.mark.parametrize("status", ["pending", "running", "retrying"])
    def test_an_active_job_for_the_match_is_returned_not_duplicated(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue, status: str
    ) -> None:
        existing = _make(session, status)

        response = jobs_client.post(JOBS, json=INGEST)

        assert response.status_code == 200
        assert response.json()["id"] == str(existing.id)
        assert _count(session) == 1
        assert queue.sent == []

    @pytest.mark.parametrize("status", ["completed", "failed"])
    def test_a_finished_job_does_not_block_a_new_submission(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue, status: str
    ) -> None:
        _make(session, status)

        response = jobs_client.post(JOBS, json=INGEST)

        assert response.status_code == 202
        assert _count(session) == 2
        assert len(queue.sent) == 1

    def test_different_matches_and_types_are_independent(
        self, jobs_client: TestClient, session: Session
    ) -> None:
        _make(session, "running", match=9001)

        assert jobs_client.post(JOBS, json={**INGEST, "match_id": 9002}).status_code == 202

    def test_a_lost_race_reuses_the_winning_job(
        self, session: Session, queue: FakeQueue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        winner = _make(session, "pending")
        real = repository.find_active_job
        calls = {"n": 0}

        def blind_first_time(*args: Any, **kwargs: Any) -> ProcessingJob | None:
            calls["n"] += 1
            return None if calls["n"] == 1 else real(*args, **kwargs)

        monkeypatch.setattr(repository, "find_active_job", blind_first_time)
        service = JobService(session, queue, get_settings())

        from app.db.models import JobType

        result = service.submit(JobType.ingest_match, 9001)

        assert result.created is False
        assert result.job.id == winner.id
        assert queue.sent == []


class TestQueueUnavailable:
    def test_the_job_is_marked_failed_and_the_client_is_told(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        queue.fail_with = ConnectionError("redis://admin:s3cret@broker.internal:6379 refused")

        response = jobs_client.post(JOBS, json=INGEST)

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "could not be queued" in detail
        assert "s3cret" not in response.text and "broker.internal" not in response.text

        (job,) = session.scalars(select(ProcessingJob)).all()
        assert job.status == "failed"
        assert job.error_message == "The job could not be queued."
        assert job.completed_at is not None
        assert str(job.id) in detail
        assert job.attempt_count == 0

    def test_a_job_that_was_never_queued_is_not_reported_as_accepted(
        self, jobs_client: TestClient, queue: FakeQueue
    ) -> None:
        queue.fail_with = RuntimeError("down")

        response = jobs_client.post(JOBS, json=INGEST)

        assert response.status_code != 202

    def test_the_failed_job_can_be_retried_once_the_broker_is_back(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        queue.fail_with = RuntimeError("down")
        jobs_client.post(JOBS, json=INGEST)
        queue.fail_with = None
        job = session.scalar(select(ProcessingJob))
        assert job is not None

        response = jobs_client.post(f"{JOBS}/{job.id}/retry")

        assert response.status_code == 202
        assert response.json()["status"] == "retrying"
        assert queue.job_ids == [job.id]


class TestStatus:
    def test_get_returns_the_job(self, jobs_client: TestClient, session: Session) -> None:
        job = _make(
            session,
            "completed",
            result={"events_imported": 3},
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            attempt_count=1,
        )

        body = jobs_client.get(f"{JOBS}/{job.id}").json()

        assert body["status"] == "completed"
        assert body["result"] == {"events_imported": 3}
        assert body["attempt_count"] == 1
        assert body["completed_at"] is not None

    def test_a_failed_job_shows_only_a_safe_message(
        self, jobs_client: TestClient, session: Session
    ) -> None:
        job = _make(session, "failed", error_message="The match source data is malformed.")

        body = jobs_client.get(f"{JOBS}/{job.id}").json()

        assert body["error_message"] == "The match source data is malformed."

    def test_unknown_job_is_404(self, jobs_client: TestClient) -> None:
        response = jobs_client.get(f"{JOBS}/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json() == {"detail": "Job not found"}

    def test_a_malformed_id_is_422(self, jobs_client: TestClient) -> None:
        assert jobs_client.get(f"{JOBS}/not-a-uuid").status_code == 422


class TestListing:
    @pytest.fixture(autouse=True)
    def _jobs(self, session: Session) -> None:
        _make(session, "completed", match=1)
        _make(session, "failed", match=2)
        _make(session, "pending", match=3)
        _make(session, "running", match=4, job_type="compute_match_statistics")
        _make(session, "completed", match=3, job_type="compute_match_statistics")

    def test_envelope_and_total(self, jobs_client: TestClient) -> None:
        body = jobs_client.get(JOBS).json()

        assert set(body) == {"items", "limit", "offset", "total"}
        assert body["total"] == 5
        assert (body["limit"], body["offset"]) == (50, 0)

    def test_newest_first(self, jobs_client: TestClient) -> None:
        items = jobs_client.get(JOBS).json()["items"]

        stamps = [item["created_at"] for item in items]
        assert stamps == sorted(stamps, reverse=True)

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            ({"status": "completed"}, 2),
            ({"status": "failed"}, 1),
            ({"status": "retrying"}, 0),
            ({"job_type": "compute_match_statistics"}, 2),
            ({"job_type": "ingest_match"}, 3),
            ({"match_id": 3}, 2),
            ({"status": "completed", "job_type": "compute_match_statistics"}, 1),
            ({"match_id": 3, "status": "pending"}, 1),
            ({"match_id": 999}, 0),
        ],
    )
    def test_filters(self, jobs_client: TestClient, params: dict[str, Any], expected: int) -> None:
        assert jobs_client.get(JOBS, params=params).json()["total"] == expected

    def test_pagination(self, jobs_client: TestClient) -> None:
        first = jobs_client.get(JOBS, params={"limit": 2, "offset": 0}).json()
        second = jobs_client.get(JOBS, params={"limit": 2, "offset": 2}).json()
        third = jobs_client.get(JOBS, params={"limit": 2, "offset": 4}).json()

        ids = [i["id"] for page in (first, second, third) for i in page["items"]]
        assert [len(p["items"]) for p in (first, second, third)] == [2, 2, 1]
        assert len(set(ids)) == 5
        assert first["total"] == 5

    @pytest.mark.parametrize(
        "params",
        [
            {"status": "queued"},
            {"job_type": "nope"},
            {"match_id": "x"},
            {"limit": 0},
            {"limit": 101},
            {"offset": -1},
        ],
    )
    def test_invalid_parameters_are_422(
        self, jobs_client: TestClient, params: dict[str, Any]
    ) -> None:
        assert jobs_client.get(JOBS, params=params).status_code == 422


class TestRetryEndpoint:
    def test_a_failed_job_with_attempts_left_is_retried(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        job = _make(
            session, "failed", attempt_count=1, error_message="x", completed_at=datetime.now(UTC)
        )

        response = jobs_client.post(f"{JOBS}/{job.id}/retry")

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "retrying"
        assert body["completed_at"] is None
        assert queue.job_ids == [job.id]

    @pytest.mark.parametrize("status", ["pending", "running", "retrying", "completed"])
    def test_only_a_failed_job_can_be_retried(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue, status: str
    ) -> None:
        job = _make(session, status)

        response = jobs_client.post(f"{JOBS}/{job.id}/retry")

        assert response.status_code == 409
        assert "failed" in response.json()["detail"]
        assert queue.sent == []

    def test_a_job_with_no_attempts_left_is_refused(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        job = _make(session, "failed", attempt_count=3, max_attempts=3)

        response = jobs_client.post(f"{JOBS}/{job.id}/retry")

        assert response.status_code == 409
        assert "attempts" in response.json()["detail"]
        assert queue.sent == []
        session.refresh(job)
        assert job.status == "failed"

    def test_unknown_job_is_404(self, jobs_client: TestClient) -> None:
        assert jobs_client.post(f"{JOBS}/{uuid.uuid4()}/retry").status_code == 404

    def test_a_conflicting_active_job_blocks_the_retry(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        failed = _make(session, "failed", attempt_count=1)
        _make(session, "pending")

        response = jobs_client.post(f"{JOBS}/{failed.id}/retry")

        assert response.status_code == 409
        assert "active job" in response.json()["detail"]
        session.refresh(failed)
        assert failed.status == "failed"
        assert queue.sent == []

    def test_an_unavailable_broker_fails_the_job_again_safely(
        self, jobs_client: TestClient, session: Session, queue: FakeQueue
    ) -> None:
        job = _make(session, "failed", attempt_count=1)
        queue.fail_with = ConnectionError("redis://u:pw@host refused")

        response = jobs_client.post(f"{JOBS}/{job.id}/retry")

        assert response.status_code == 503
        assert "pw" not in response.text
        session.refresh(job)
        assert job.status == "failed"


def test_the_openapi_document_describes_the_job_endpoints(jobs_client: TestClient) -> None:
    paths = jobs_client.get("/openapi.json").json()["paths"]

    assert "202" in paths["/api/v1/jobs"]["post"]["responses"]
    assert "/api/v1/jobs/{job_id}" in paths
    assert "/api/v1/jobs/{job_id}/retry" in paths
    assert {p["name"] for p in paths["/api/v1/jobs"]["get"]["parameters"]} >= {
        "status",
        "job_type",
        "match_id",
        "limit",
        "offset",
    }
