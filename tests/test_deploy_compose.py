"""Static checks on the Docker Compose deployment (no Docker needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "compose.yml").read_text())


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return compose["services"]


def test_it_defines_the_required_services(services: dict[str, dict[str, Any]]) -> None:
    assert {"api", "worker", "broker", "db"} <= set(services)


def test_api_and_worker_share_one_image_but_run_different_commands(
    services: dict[str, dict[str, Any]],
) -> None:
    assert services["api"]["image"] == services["worker"]["image"]
    assert services["api"]["build"] == services["worker"]["build"] == "."
    assert services["api"]["command"][0] == "uvicorn"
    assert services["worker"]["command"][:3] == ["python", "-m", "app.jobs.worker"]


def test_workers_can_be_scaled(services: dict[str, dict[str, Any]]) -> None:
    worker = services["worker"]

    # A fixed name or a published host port would make `--scale worker=N` fail.
    assert "container_name" not in worker
    assert "ports" not in worker


@pytest.mark.parametrize("name", ["db", "broker", "api", "worker"])
def test_every_long_running_service_has_a_health_check(
    services: dict[str, dict[str, Any]], name: str
) -> None:
    assert services[name]["healthcheck"]["test"]


@pytest.mark.parametrize("name", ["api", "worker"])
def test_api_and_worker_wait_for_healthy_dependencies_and_migrations(
    services: dict[str, dict[str, Any]], name: str
) -> None:
    depends = services[name]["depends_on"]

    assert depends["db"]["condition"] == "service_healthy"
    assert depends["broker"]["condition"] == "service_healthy"
    assert depends["migrate"]["condition"] == "service_completed_successfully"


def test_migrations_run_once_and_do_not_restart(services: dict[str, dict[str, Any]]) -> None:
    migrate = services["migrate"]

    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert migrate["restart"] == "no"
    assert migrate["depends_on"]["db"]["condition"] == "service_healthy"


@pytest.mark.parametrize("name", ["migrate", "api", "worker"])
def test_connections_use_service_names_not_localhost(
    services: dict[str, dict[str, Any]], name: str
) -> None:
    env = services[name]["environment"]

    assert "@db:5432/" in env["DATABASE_URL"]
    assert env["BROKER_URL"] == "redis://broker:6379/0"
    assert "localhost" not in env["DATABASE_URL"] + env["BROKER_URL"]


def test_the_worker_reads_match_data_from_a_read_only_mount(
    services: dict[str, dict[str, Any]],
) -> None:
    mounts = services["worker"]["volumes"]

    assert any(m.endswith(":/data/raw:ro") for m in mounts)
    assert services["worker"]["environment"]["STATSBOMB_SOURCE_DIR"] == "/data/raw"


def test_worker_concurrency_is_configurable(services: dict[str, dict[str, Any]]) -> None:
    assert "WORKER_CONCURRENCY" in services["worker"]["environment"]


def test_the_broker_does_not_persist_because_postgres_is_the_record(
    services: dict[str, dict[str, Any]],
) -> None:
    command = services["broker"]["command"]

    assert "--appendonly" in command and command[command.index("--appendonly") + 1] == "no"


def test_workers_get_time_to_finish_the_job_in_flight(
    services: dict[str, dict[str, Any]],
) -> None:
    assert services["worker"]["stop_grace_period"] == "60s"


class TestImage:
    @pytest.fixture
    def dockerfile(self) -> str:
        return (ROOT / "Dockerfile").read_text()

    def test_it_does_not_run_as_root(self, dockerfile: str) -> None:
        assert any(line.strip().startswith("USER app") for line in dockerfile.splitlines())

    def test_it_installs_locked_runtime_dependencies_only(self, dockerfile: str) -> None:
        assert "uv sync --frozen --no-dev" in dockerfile

    def test_it_carries_the_migrations_and_the_app(self, dockerfile: str) -> None:
        assert "COPY --chown=app:app alembic ./alembic" in dockerfile
        assert "COPY --chown=app:app app ./app" in dockerfile

    def test_it_does_not_bake_in_secrets_or_match_data(self) -> None:
        ignored = (ROOT / ".dockerignore").read_text().splitlines()

        assert ".env" in ignored
        assert "data" in ignored
        assert ".git" in ignored
