"""The monitoring configuration stays consistent with the metrics the code exports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from prometheus_client import REGISTRY

import app.observability.metrics  # noqa: F401  (registers the metrics)

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"

# Exported by a custom collector at scrape time rather than registered up front.
SCRAPE_TIME_SAMPLES = {"football_queue_depth"}


@pytest.fixture(scope="module")
def dashboard() -> dict[str, Any]:
    return json.loads((MONITORING / "grafana/dashboards/football-platform.json").read_text())


@pytest.fixture(scope="module")
def exported_samples() -> set[str]:
    names = {
        sample.name
        for family in REGISTRY.collect()
        for sample in family.samples
        if sample.name.startswith("football_")
    }
    # Labelled metrics emit no samples until used, so also accept the declared names.
    for family in REGISTRY.collect():
        if family.name.startswith("football_"):
            names.add(family.name)
            names.add(f"{family.name}_total")
            names.update(f"{family.name}_{suffix}" for suffix in ("bucket", "count", "sum"))
    return names | SCRAPE_TIME_SAMPLES


def expressions(dashboard: dict[str, Any]) -> list[tuple[str, str]]:
    return [(p["title"], t["expr"]) for p in dashboard["panels"] for t in p["targets"]]


def test_the_dashboard_is_valid_and_uses_the_provisioned_datasource(
    dashboard: dict[str, Any],
) -> None:
    assert dashboard["uid"] == "football-platform"
    assert dashboard["panels"]
    datasource = yaml.safe_load(
        (MONITORING / "grafana/provisioning/datasources/prometheus.yml").read_text()
    )["datasources"][0]
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == datasource["uid"] == "prometheus"


def test_dashboard_panel_ids_are_unique(dashboard: dict[str, Any]) -> None:
    ids = [panel["id"] for panel in dashboard["panels"]]

    assert len(ids) == len(set(ids))


def test_every_metric_the_dashboard_queries_is_exported(
    dashboard: dict[str, Any], exported_samples: set[str]
) -> None:
    for title, expr in expressions(dashboard):
        for name in re.findall(r"\bfootball_[a-z_]+", expr):
            assert name in exported_samples, f"panel {title!r} queries unknown metric {name}"


@pytest.mark.parametrize(
    "required",
    [
        "football_jobs_submitted_total",
        "football_jobs_completed_total",
        "football_jobs_failed_total",
        "football_jobs_retried_total",
        "football_jobs_running",
        "football_job_duration_seconds",
        "football_queue_depth",
        "football_http_requests_total",
        "football_http_request_duration_seconds",
        "football_worker_up",
    ],
)
def test_the_dashboard_covers_every_required_signal(
    dashboard: dict[str, Any], required: str
) -> None:
    assert any(required in expr for _, expr in expressions(dashboard))


def test_prometheus_scrapes_the_api_and_every_worker_replica() -> None:
    config = yaml.safe_load((MONITORING / "prometheus.yml").read_text())
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}

    assert jobs["football-api"]["static_configs"][0]["targets"] == ["api:8000"]
    worker = jobs["football-worker"]["dns_sd_configs"][0]
    assert (worker["names"], worker["type"], worker["port"]) == (["worker"], "A", 9100)
    assert all(job["metrics_path"] == "/metrics" for job in jobs.values())


def test_monitoring_is_optional_in_compose() -> None:
    services = yaml.safe_load((ROOT / "compose.yml").read_text())["services"]

    for name in ("prometheus", "grafana"):
        assert services[name]["profiles"] == ["monitoring"]
    assert "profiles" not in services["api"] and "profiles" not in services["worker"]
    # Dashboards and the datasource are mounted, not baked in or entered by hand.
    assert any("provisioning" in v for v in services["grafana"]["volumes"])
