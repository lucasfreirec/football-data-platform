"""Prometheus metrics for the API and the workers.

The API is one process and uses the default registry. Workers run a Celery prefork
pool, so counters are incremented in child processes while the exporter lives in the
parent; that needs prometheus_client's multiprocess mode, which is chosen at *import*
time from ``PROMETHEUS_MULTIPROC_DIR``. The worker launcher therefore sets that
variable before anything imports this module (see ``multiproc.enable_multiprocess``).

Labels are deliberately low-cardinality: job type and outcome come from fixed sets,
and HTTP metrics use the route template, never the raw path (which holds job ids).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import JobType
from app.observability.multiproc import multiprocess_enabled

logger = get_logger(__name__)

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST
UNKNOWN = "unknown"

_JOB_TYPES = frozenset(job_type.value for job_type in JobType)

JOB_DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
HTTP_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

JOBS_SUBMITTED = Counter(
    "football_jobs_submitted_total",
    "Jobs accepted by the API (a reused active job is not counted again).",
    ["job_type"],
)
JOBS_COMPLETED = Counter(
    "football_jobs_completed_total", "Jobs that finished successfully.", ["job_type"]
)
JOBS_FAILED = Counter(
    "football_jobs_failed_total",
    "Jobs that failed for good: a permanent error or no attempts left.",
    ["job_type"],
)
JOBS_RETRIED = Counter(
    "football_jobs_retried_total",
    "Attempts that failed and were scheduled to run again, plus manual retries.",
    ["job_type"],
)
JOBS_RUNNING = Gauge(
    "football_jobs_running",
    "Jobs executing right now.",
    ["job_type"],
    multiprocess_mode="livesum",
)
JOB_DURATION = Histogram(
    "football_job_duration_seconds",
    "Time spent executing one attempt of a job.",
    ["job_type", "outcome"],
    buckets=JOB_DURATION_BUCKETS,
)
WORKER_UP = Gauge(
    "football_worker_up",
    "1 while a worker is running; one series per worker.",
    ["worker"],
    multiprocess_mode="livesum",
)
HTTP_REQUESTS = Counter(
    "football_http_requests_total",
    "HTTP requests handled by the API.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "football_http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "route"],
    buckets=HTTP_DURATION_BUCKETS,
)


def job_type_label(job_type: str) -> str:
    """Bound the label's cardinality even if a row ever holds an unexpected type."""
    return job_type if job_type in _JOB_TYPES else UNKNOWN


def mark_process_dead(pid: int) -> None:
    """Stop a dead pool process from contributing its live gauges (running jobs)."""
    if multiprocess_enabled():
        multiprocess.mark_process_dead(pid)


class QueueDepthCollector(Collector):
    """Reads the broker queue length at scrape time.

    Retries scheduled with a delay are held by a worker rather than in the list, so
    this counts messages waiting for a free slot, not every job that is not done yet.
    """

    def __init__(self, broker_url: str, queue_name: str) -> None:
        self._broker_url = broker_url
        self._queue_name = queue_name
        self._client: Any = None

    def collect(self) -> Iterator[Metric]:
        family = GaugeMetricFamily(
            "football_queue_depth",
            "Messages waiting in the broker queue; absent when the broker is unreachable.",
            labels=["queue"],
        )
        depth = self._depth()
        if depth is not None:
            family.add_metric([self._queue_name], depth)
            yield family

    def _depth(self) -> int | None:
        try:
            import redis

            if self._client is None:
                self._client = redis.Redis.from_url(
                    self._broker_url, socket_timeout=1, socket_connect_timeout=1
                )
            return int(self._client.llen(self._queue_name))
        except Exception as exc:
            logger.debug("queue_depth_unavailable error=%r", exc)
            return None


_queue_collectors: dict[tuple[str, str], QueueDepthCollector] = {}


def render_metrics(settings: Settings) -> bytes:
    """Serialise this process's metrics (all pool processes, in a worker) plus queue depth."""
    if multiprocess_enabled():
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY

    key = (settings.broker_url, settings.job_queue_name)
    collector = _queue_collectors.setdefault(key, QueueDepthCollector(*key))
    queue_registry = CollectorRegistry()
    queue_registry.register(collector)
    return generate_latest(registry) + generate_latest(queue_registry)
