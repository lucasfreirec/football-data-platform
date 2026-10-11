"""The benchmark end to end, on a tiny workload (needs PostgreSQL and Redis)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.benchmarks import workers
from tests.integration.conftest import TEST_DATABASE_URL
from tests.integration.test_async_flow import BROKER_URL, _require_redis

pytestmark = pytest.mark.integration


def test_the_benchmark_runs_and_shows_that_workers_ran_concurrently(
    pg_engine: object, tmp_path: Path
) -> None:
    _require_redis()
    assert TEST_DATABASE_URL
    database_url = make_url(TEST_DATABASE_URL).set(database="football_benchmark")
    out = tmp_path / "out"

    code = workers.main(
        [
            "--worker-counts", "1,2",
            "--jobs-per-run", "6",
            "--events-per-match", "400",
            "--database-url", database_url.render_as_string(hide_password=False),
            "--broker-url", os.environ.get("TEST_BENCHMARK_BROKER_URL", BROKER_URL),
            "--output-dir", str(out),
        ]
    )  # fmt: skip

    assert code == 0
    data = json.loads((out / "results.json").read_text())
    one, two = data["summary"]
    assert (one["workers"], two["workers"]) == (1, 2)
    assert one["completed"] == two["completed"] == 6
    assert one["failed"] == two["failed"] == 0
    assert one["data_consistent"] and two["data_consistent"]
    # The extra slot changed behaviour, not just a setting.
    assert one["peak_running"] == 1 and one["distinct_worker_processes"] == 1
    assert two["peak_running"] >= 2 and two["distinct_worker_processes"] == 2
    assert "Worker-count benchmark" in (out / "report.md").read_text()
