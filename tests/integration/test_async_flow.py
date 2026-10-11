"""The whole asynchronous path on real services: API -> Redis -> worker process -> PostgreSQL.

Needs ``TEST_DATABASE_URL`` (a scratch database) and a reachable Redis, by default
``redis://localhost:6379/9`` or ``TEST_BROKER_URL``. Skipped otherwise::

    docker compose up -d db broker
    TEST_DATABASE_URL=postgresql+psycopg://football:football@localhost:5432/football_test \
        uv run pytest tests/integration/test_async_flow.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.api.routes.jobs import get_job_queue
from app.core.config import load_settings
from app.db.models import Event, Match, ProcessingJob
from app.db.session import get_db
from app.jobs import handlers
from app.jobs.celery_app import create_celery_app
from app.jobs.queue import CeleryJobQueue
from app.main import create_app
from tests.integration.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration

JOBS = "/api/v1/jobs"
BROKER_URL = os.environ.get("TEST_BROKER_URL", "redis://localhost:6379/9")
TIMEOUT = 40.0


def _require_redis() -> None:
    import redis

    try:
        redis.Redis.from_url(BROKER_URL, socket_connect_timeout=2).ping()
    except Exception as exc:
        pytest.skip(f"Redis is not reachable at the test broker URL: {exc}")


@pytest.fixture(scope="module")
def stack(
    pg_engine: Engine, v2_source_dir_module: Path, tmp_path_factory: Any
) -> Iterator[dict[str, Any]]:
    """A running worker process plus the Celery app the API publishes through."""
    _require_redis()
    queue_name = f"football-test-{uuid.uuid4().hex[:8]}"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        metrics_port = probe.getsockname()[1]
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL or "",
        "BROKER_URL": BROKER_URL,
        "JOB_QUEUE_NAME": queue_name,
        "WORKER_METRICS_PORT": str(metrics_port),
        "STATSBOMB_SOURCE_DIR": str(v2_source_dir_module),
        "JOB_RETRY_DELAYS_SECONDS": "1,1,1",
        "LOG_LEVEL": "INFO",
    }
    log_path = tmp_path_factory.mktemp("worker") / "worker.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.jobs.worker", "--concurrency", "2", "--wait-seconds", "20"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    settings = load_settings(broker_url=BROKER_URL, job_queue_name=queue_name, _env_file=None)
    celery_app = create_celery_app(settings)

    deadline = time.monotonic() + 40
    ready = False
    while time.monotonic() < deadline and process.poll() is None:
        if celery_app.control.inspect(timeout=1.0).ping():
            ready = True
            break
    if not ready:
        process.terminate()
        pytest.fail(f"the worker never became ready:\n{log_path.read_text()[-2000:]}")

    try:
        yield {
            "celery": celery_app,
            "queue_name": queue_name,
            "log": log_path,
            "metrics_url": f"http://127.0.0.1:{metrics_port}",
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        import redis

        redis.Redis.from_url(BROKER_URL).delete(queue_name)


@pytest.fixture(scope="module")
def v2_source_dir_module() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "v2" / "raw"


@pytest.fixture
def api(pg_session: Session, stack: dict[str, Any]) -> Iterator[TestClient]:
    from app.api.routes import health as health_route

    app: FastAPI = create_app()
    health_route.check_database_connection = lambda: True  # type: ignore[assignment]
    app.dependency_overrides[get_db] = lambda: pg_session
    app.dependency_overrides[get_job_queue] = lambda: CeleryJobQueue(
        stack["celery"], stack["queue_name"]
    )
    with TestClient(app) as client:
        yield client


def wait_for(
    client: TestClient, job_id: str, statuses: set[str], timeout: float = TIMEOUT
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"{JOBS}/{job_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.2)
    pytest.fail(f"job stuck in {body.get('status')} (wanted {statuses}): {body}")


def count(session: Session, model: type) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(model)) or 0


def submit(client: TestClient, match: int, **extra: Any) -> str:
    response = client.post(JOBS, json={"job_type": "ingest_match", "match_id": match, **extra})
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def test_a_job_goes_from_submission_to_completion(api: TestClient, pg_session: Session) -> None:
    job_id = submit(api, 9001)

    done = wait_for(api, job_id, {"completed", "failed"})

    assert done["status"] == "completed", done
    assert done["attempt_count"] == 1
    assert done["result"]["events_imported"] == 10
    assert done["result"]["worker"]
    assert done["started_at"] and done["completed_at"]
    assert count(pg_session, Match) == 1
    assert count(pg_session, Event) == 10


def test_a_statistics_job_runs_after_the_import(api: TestClient) -> None:
    wait_for(api, submit(api, 9001), {"completed"})

    response = api.post(JOBS, json={"job_type": "compute_match_statistics", "match_id": 9001})
    assert response.status_code == 202
    done = wait_for(api, response.json()["id"], {"completed", "failed"})

    assert done["status"] == "completed", done
    teams = {t["team_id"]: t for t in done["result"]["teams"]}
    assert teams[101]["shots"] == 2 and teams[101]["goals"] == 1
    assert teams[101]["xg"] == pytest.approx(0.35)


def test_jobs_for_different_matches_run_concurrently_without_corruption(
    api: TestClient, pg_session: Session
) -> None:
    ids = [
        submit(api, 9001),
        submit(api, 9002),
        submit(api, 9003, season="2016/2017"),
    ]

    results = [wait_for(api, job_id, {"completed", "failed"}) for job_id in ids]

    assert [r["status"] for r in results] == ["completed"] * 3, results
    assert count(pg_session, Match) == 3
    assert count(pg_session, Event) == 10 + 9 + 6
    # The teams and players the three matches share exist exactly once.
    assert pg_session.scalar(text("select count(*) from teams")) == 3
    assert pg_session.scalar(text("select count(*) from players")) == 7


def test_a_second_request_for_an_active_match_reuses_the_job(
    api: TestClient, pg_engine: Engine, pg_session: Session
) -> None:
    # Hold the match's lock so the first job is certain to still be active.
    key = handlers._advisory_key("ingest_match", 9001)
    blocker = pg_engine.connect()
    blocker.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
    try:
        first_id = submit(api, 9001)
        wait_for(api, first_id, {"retrying"})

        second = api.post(JOBS, json={"job_type": "ingest_match", "match_id": 9001})

        assert second.status_code == 200
        assert second.json()["id"] == first_id
        assert count(pg_session, ProcessingJob) == 1
    finally:
        blocker.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        blocker.close()

    assert wait_for(api, first_id, {"completed", "failed"})["status"] == "completed"


def test_a_transient_failure_retries_through_the_queue_and_then_succeeds(
    api: TestClient, pg_engine: Engine, pg_session: Session
) -> None:
    # Another session holds the match's advisory lock, so the worker cannot claim it.
    key = handlers._advisory_key("ingest_match", 9001)
    blocker = pg_engine.connect()
    blocker.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
    try:
        job_id = submit(api, 9001)
        retrying = wait_for(api, job_id, {"retrying", "failed", "completed"})
        assert retrying["status"] == "retrying", retrying
        assert retrying["attempt_count"] >= 1
        assert "already being processed" in retrying["error_message"]
    finally:
        blocker.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        blocker.close()

    done = wait_for(api, job_id, {"completed", "failed"})

    assert done["status"] == "completed", done
    assert done["attempt_count"] >= 2
    assert done["error_message"] is None
    assert count(pg_session, Event) == 10


def test_a_permanent_failure_is_not_retried(api: TestClient) -> None:
    job_id = submit(api, 9001, source_path="no/such/dir")

    done = wait_for(api, job_id, {"completed", "failed"})
    time.sleep(2.5)  # long enough for any (wrong) retry delay of 1s to have fired
    final = api.get(f"{JOBS}/{job_id}").json()

    assert done["status"] == "failed"
    assert final["attempt_count"] == 1
    assert final["error_message"] == "The match source data is missing or malformed."
    assert "/" not in final["error_message"]


def test_a_failed_job_can_be_retried_through_the_api(api: TestClient, pg_session: Session) -> None:
    job_id = submit(api, 9001, source_path="no/such/dir")
    wait_for(api, job_id, {"failed"})

    response = api.post(f"{JOBS}/{job_id}/retry")

    assert response.status_code == 202
    again = wait_for(api, job_id, {"failed", "completed"})
    assert again["attempt_count"] == 2
    assert again["status"] == "failed"  # still pointing at the missing directory


def test_duplicate_message_delivery_does_not_duplicate_data(
    api: TestClient, pg_session: Session, stack: dict[str, Any]
) -> None:
    job_id = submit(api, 9001)
    wait_for(api, job_id, {"completed"})
    events = count(pg_session, Event)
    queue = CeleryJobQueue(stack["celery"], stack["queue_name"])

    for _ in range(3):
        queue.enqueue(uuid.UUID(job_id))
    import redis

    client = redis.Redis.from_url(BROKER_URL)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and client.llen(stack["queue_name"]) > 0:
        time.sleep(0.2)
    time.sleep(1.5)

    job = pg_session.get(ProcessingJob, uuid.UUID(job_id))
    pg_session.refresh(job)
    assert job is not None
    assert job.status == "completed"
    assert job.attempt_count == 1
    assert count(pg_session, Event) == events


def test_the_worker_logs_each_attempt_with_its_context(
    api: TestClient, stack: dict[str, Any]
) -> None:
    job_id = submit(api, 9001)
    wait_for(api, job_id, {"completed"})
    time.sleep(0.5)

    log = stack["log"].read_text()

    line = next(row for row in log.splitlines() if "job_finished" in row and job_id in row)
    for field in (
        "job_type=ingest_match",
        "match_id=9001",
        "attempt=1/3",
        "worker=",
        "duration_s=",
        "outcome=completed",
    ):
        assert field in line


def scrape(stack: dict[str, Any]) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    """The worker's /metrics as {(sample name, sorted labels): value}."""
    from prometheus_client.parser import text_string_to_metric_families

    with urllib.request.urlopen(f"{stack['metrics_url']}/metrics", timeout=5) as response:
        assert response.headers["Content-Type"].startswith("text/plain")
        body = response.read().decode()
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(body)
        for sample in family.samples
    }


def value(
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float], name: str, **labels: str
) -> float:
    return samples.get((name, tuple(sorted(labels.items()))), 0.0)


def test_the_worker_serves_a_liveness_endpoint_independent_of_its_dependencies(
    stack: dict[str, Any],
) -> None:
    with urllib.request.urlopen(f"{stack['metrics_url']}/healthz", timeout=5) as response:
        assert response.status == 200
    with pytest.raises(urllib.error.HTTPError) as unknown:
        urllib.request.urlopen(f"{stack['metrics_url']}/nope", timeout=5)
    assert unknown.value.code == 404


def test_worker_metrics_count_completed_and_failed_jobs(
    api: TestClient, stack: dict[str, Any]
) -> None:
    before = scrape(stack)
    kind = {"job_type": "ingest_match"}

    wait_for(api, submit(api, 9001), {"completed"})
    wait_for(api, submit(api, 9002, source_path="no/such/dir"), {"failed"})
    time.sleep(0.5)
    after = scrape(stack)

    def delta(name: str, **labels: str) -> float:
        return value(after, name, **labels) - value(before, name, **labels)

    # These come from the pool processes, merged by the exporter in the parent process.
    assert delta("football_jobs_completed_total", **kind) == 1
    assert delta("football_jobs_failed_total", **kind) == 1
    assert delta("football_job_duration_seconds_count", outcome="completed", **kind) == 1
    assert delta("football_job_duration_seconds_count", outcome="failed", **kind) == 1
    assert value(after, "football_jobs_running", **kind) == 0
    assert any(name == "football_worker_up" and v == 1 for (name, _labels), v in after.items()), (
        "the worker should report itself up"
    )
    assert value(after, "football_queue_depth", queue=stack["queue_name"]) == 0


def test_worker_metrics_count_retries(
    api: TestClient, pg_engine: Engine, stack: dict[str, Any]
) -> None:
    before = value(scrape(stack), "football_jobs_retried_total", job_type="ingest_match")
    key = handlers._advisory_key("ingest_match", 9001)
    blocker = pg_engine.connect()
    blocker.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
    try:
        job_id = submit(api, 9001)
        wait_for(api, job_id, {"retrying"})
        time.sleep(0.5)
        during = scrape(stack)
    finally:
        blocker.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        blocker.close()
    wait_for(api, job_id, {"completed"})

    assert value(during, "football_jobs_retried_total", job_type="ingest_match") > before
