"""Metrics: executor counters, API request metrics, the exporter, and multiprocess setup."""

from __future__ import annotations

import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, load_settings
from app.core.exceptions import PermanentJobError, RetryableJobError
from app.jobs import handlers
from app.jobs.executor import execute_job
from app.observability import multiproc
from app.observability.exporter import start_exporter
from app.observability.metrics import QueueDepthCollector, job_type_label, render_metrics
from tests.jobs.conftest import FakeQueue
from tests.jobs.test_executor import make_job

INGEST = {"job_type": "ingest_match"}


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Delta:
    """What a block of code changed, since metrics are process-wide and never reset."""

    def __init__(self, name: str, **labels: str) -> None:
        self.name, self.labels = name, labels
        self.start = sample(name, **labels)

    @property
    def value(self) -> float:
        return sample(self.name, **self.labels) - self.start


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def settings() -> Settings:
    return load_settings(_env_file=None)


@pytest.fixture
def run_with(
    session: Session,
    session_factory: sessionmaker[Session],
    queue: FakeQueue,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[..., dict[str, Any]], dict[str, Any]], str]:
    def _run(handler: Callable[..., dict[str, Any]], fields: dict[str, Any]) -> str:
        monkeypatch.setitem(handlers.HANDLERS, "ingest_match", handler)
        job = make_job(session, **fields)
        return execute_job(
            job.id,
            session_factory=session_factory,
            queue=queue,
            settings=settings,
            worker="test:1",
        )

    return _run


def ok(*_: object) -> dict[str, Any]:
    return {"ok": True}


def raising(error: Exception) -> Callable[..., dict[str, Any]]:
    def handler(*_: object) -> dict[str, Any]:
        raise error

    return handler


class TestJobMetrics:
    def test_a_completed_job_is_counted_and_timed(self, run_with: Callable[..., str]) -> None:
        completed = Delta("football_jobs_completed_total", **INGEST)
        timed = Delta("football_job_duration_seconds_count", outcome="completed", **INGEST)

        assert run_with(ok, {}) == "completed"

        assert completed.value == 1
        assert timed.value == 1

    def test_a_transient_failure_counts_as_a_retry_not_a_failure(
        self, run_with: Callable[..., str]
    ) -> None:
        retried = Delta("football_jobs_retried_total", **INGEST)
        failed = Delta("football_jobs_failed_total", **INGEST)

        assert run_with(raising(RetryableJobError("later")), {}) == "retrying"

        assert (retried.value, failed.value) == (1, 0)

    def test_a_permanent_failure_counts_as_failed(self, run_with: Callable[..., str]) -> None:
        retried = Delta("football_jobs_retried_total", **INGEST)
        failed = Delta("football_jobs_failed_total", **INGEST)

        assert run_with(raising(PermanentJobError("no")), {}) == "failed"

        assert (retried.value, failed.value) == (0, 1)

    def test_running_out_of_attempts_counts_as_failed(self, run_with: Callable[..., str]) -> None:
        failed = Delta("football_jobs_failed_total", **INGEST)

        outcome = run_with(
            raising(RetryableJobError("later")), {"status": "retrying", "attempt_count": 2}
        )

        assert outcome == "failed"
        assert failed.value == 1

    def test_a_job_that_cannot_be_claimed_is_not_counted(
        self, run_with: Callable[..., str]
    ) -> None:
        completed = Delta("football_jobs_completed_total", **INGEST)
        timed = Delta("football_job_duration_seconds_count", outcome="completed", **INGEST)

        assert run_with(ok, {"status": "completed"}) == "skipped"

        assert (completed.value, timed.value) == (0, 0)

    def test_the_running_gauge_is_up_during_the_job_and_back_to_zero_after(
        self, run_with: Callable[..., str]
    ) -> None:
        during: list[float] = []

        def handler(*_: object) -> dict[str, Any]:
            during.append(sample("football_jobs_running", **INGEST))
            return {}

        base = sample("football_jobs_running", **INGEST)
        run_with(handler, {})

        assert during == [base + 1]
        assert sample("football_jobs_running", **INGEST) == base

    def test_the_running_gauge_returns_to_zero_when_the_job_fails(
        self, run_with: Callable[..., str]
    ) -> None:
        base = sample("football_jobs_running", **INGEST)

        run_with(raising(RuntimeError("boom")), {})

        assert sample("football_jobs_running", **INGEST) == base

    @pytest.mark.parametrize(
        ("raw", "label"),
        [
            ("ingest_match", "ingest_match"),
            ("compute_match_statistics", "compute_match_statistics"),
            ("something-else", "unknown"),
        ],
    )
    def test_job_type_labels_are_bounded(self, raw: str, label: str) -> None:
        assert job_type_label(raw) == label


class TestApiMetrics:
    def test_submitting_a_job_is_counted_once_and_reuse_is_not(
        self, jobs_client: TestClient
    ) -> None:
        submitted = Delta("football_jobs_submitted_total", **INGEST)
        body = {"job_type": "ingest_match", "match_id": 9001}

        assert jobs_client.post("/api/v1/jobs", json=body).status_code == 202
        assert jobs_client.post("/api/v1/jobs", json=body).status_code == 200

        assert submitted.value == 1

    def test_a_manual_retry_is_counted(self, jobs_client: TestClient, session: Session) -> None:
        job = make_job(session, status="failed", attempt_count=1)
        retried = Delta("football_jobs_retried_total", **INGEST)

        assert jobs_client.post(f"/api/v1/jobs/{job.id}/retry").status_code == 202

        assert retried.value == 1

    def test_requests_are_labelled_by_route_template_not_by_job_id(
        self, jobs_client: TestClient
    ) -> None:
        job_id = uuid.uuid4()
        route = "/api/v1/jobs/{job_id}"
        count = Delta("football_http_requests_total", method="GET", route=route, status="404")
        latency = Delta("football_http_request_duration_seconds_count", method="GET", route=route)

        jobs_client.get(f"/api/v1/jobs/{job_id}")

        assert (count.value, latency.value) == (1, 1)
        assert str(job_id) not in render_metrics(load_settings(_env_file=None)).decode()

    def test_unknown_paths_share_one_label(self, jobs_client: TestClient) -> None:
        count = Delta("football_http_requests_total", method="GET", route="unmatched", status="404")

        for name in ("a", "b", "c"):
            jobs_client.get(f"/no-such-{name}")

        assert count.value == 3

    def test_a_server_error_is_recorded_as_a_500(
        self, jobs_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.routes import jobs

        def explode(*_: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(jobs.repository, "get_job", explode)
        failures = Delta(
            "football_http_requests_total",
            method="GET",
            route="/api/v1/jobs/{job_id}",
            status="500",
        )

        jobs_client.get(f"/api/v1/jobs/{uuid.uuid4()}")

        assert failures.value == 1

    def test_the_metrics_endpoint_exposes_prometheus_text(self, jobs_client: TestClient) -> None:
        jobs_client.get("/health")

        response = jobs_client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "football_http_requests_total" in response.text
        assert 'route="/health"' in response.text

    def test_scraping_does_not_count_itself(self, jobs_client: TestClient) -> None:
        scrapes = Delta(
            "football_http_requests_total", method="GET", route="/metrics", status="200"
        )

        jobs_client.get("/metrics")
        jobs_client.get("/metrics")

        assert scrapes.value == 0

    def test_the_metrics_endpoint_is_not_part_of_the_api_schema(
        self, jobs_client: TestClient
    ) -> None:
        assert "/metrics" not in jobs_client.get("/openapi.json").json()["paths"]


class TestQueueDepth:
    class FakeRedis:
        def __init__(self, depth: int | Exception) -> None:
            self.depth = depth

        def llen(self, name: str) -> int:
            if isinstance(self.depth, Exception):
                raise self.depth
            return self.depth

    def collect(self, client: object) -> list[Any]:
        collector = QueueDepthCollector("redis://unused", "football-jobs")
        collector._client = client
        return list(collector.collect())

    def test_it_reports_the_queue_length(self) -> None:
        (family,) = self.collect(self.FakeRedis(7))

        assert family.name == "football_queue_depth"
        assert [(s.labels, s.value) for s in family.samples] == [({"queue": "football-jobs"}, 7)]

    def test_an_empty_queue_is_zero_not_missing(self) -> None:
        (family,) = self.collect(self.FakeRedis(0))

        assert family.samples[0].value == 0

    def test_an_unreachable_broker_omits_the_metric_instead_of_failing_the_scrape(self) -> None:
        assert self.collect(self.FakeRedis(ConnectionError("down"))) == []

    def test_a_scrape_still_works_when_the_broker_is_down(self) -> None:
        settings = load_settings(broker_url="redis://127.0.0.1:1/0", _env_file=None)

        body = render_metrics(settings).decode()

        assert "football_jobs_completed_total" in body
        assert "football_queue_depth" not in body


class TestExporter:
    @pytest.fixture
    def server(self) -> Iterator[Any]:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        settings = load_settings(worker_metrics_port=port, _env_file=None)
        started = start_exporter(settings, host="127.0.0.1")
        assert started is not None
        try:
            yield started
        finally:
            started.shutdown()
            started.server_close()

    def url(self, server: Any, path: str) -> str:
        return f"http://127.0.0.1:{server.server_address[1]}{path}"

    def test_it_serves_liveness(self, server: Any) -> None:
        with urllib.request.urlopen(self.url(server, "/healthz"), timeout=5) as response:
            assert (response.status, response.read()) == (200, b"ok\n")

    def test_it_serves_prometheus_metrics(self, server: Any) -> None:
        with urllib.request.urlopen(self.url(server, "/metrics?x=1"), timeout=5) as response:
            assert response.headers["Content-Type"].startswith("text/plain")
            assert b"football_jobs_completed_total" in response.read()

    def test_it_returns_404_for_anything_else(self, server: Any) -> None:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url(server, "/admin"), timeout=5)

        assert error.value.code == 404

    def test_port_zero_disables_it(self) -> None:
        assert start_exporter(load_settings(worker_metrics_port=0, _env_file=None)) is None

    def test_a_port_that_is_taken_does_not_stop_the_worker(self, server: Any) -> None:
        settings = load_settings(worker_metrics_port=server.server_address[1], _env_file=None)

        assert start_exporter(settings, host="127.0.0.1") is None


class TestMultiprocessSetup:
    def test_it_sets_the_directory_and_clears_files_from_a_previous_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "counter_123.db").write_text("stale")
        monkeypatch.setenv(multiproc.MULTIPROC_ENV, str(tmp_path))

        directory = multiproc.enable_multiprocess()

        assert directory == tmp_path
        assert list(tmp_path.iterdir()) == []

    def test_it_creates_a_private_directory_when_none_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(multiproc.MULTIPROC_ENV, raising=False)

        directory = multiproc.enable_multiprocess()
        try:
            assert directory.is_dir()
            assert multiproc.multiprocess_enabled()
        finally:
            directory.rmdir()
            monkeypatch.delenv(multiproc.MULTIPROC_ENV)

    def test_the_launcher_enables_it_before_anything_imports_prometheus(self) -> None:
        import subprocess
        import sys

        code = "import sys\nimport app.jobs.worker\nprint('prometheus_client' in sys.modules)\n"
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )

        assert result.stdout.strip() == "False"
