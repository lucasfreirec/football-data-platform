"""The Celery task, plus the worker's metrics lifecycle hooks."""

from __future__ import annotations

import socket
import uuid

from celery.signals import (
    worker_process_init,
    worker_process_shutdown,
    worker_ready,
    worker_shutdown,
)

from app.core.config import get_settings
from app.db.session import get_engine, get_session_factory
from app.jobs.celery_app import RUN_JOB_TASK, celery_app
from app.jobs.executor import execute_job
from app.jobs.queue import CeleryJobQueue
from app.observability.exporter import ExporterServer, start_exporter
from app.observability.metrics import WORKER_UP, mark_process_dead

_exporter: ExporterServer | None = None


@celery_app.task(name=RUN_JOB_TASK, ignore_result=True)
def run_job(job_id: str) -> str:
    return execute_job(
        uuid.UUID(job_id),
        session_factory=get_session_factory(),
        queue=CeleryJobQueue(),
        settings=get_settings(),
    )


@worker_process_init.connect
def _fresh_database_connections(**_: object) -> None:
    """Each forked pool process opens its own connections instead of sharing the parent's."""
    get_session_factory.cache_clear()
    get_engine.cache_clear()


@worker_ready.connect
def _start_metrics(**_: object) -> None:
    """Serve /metrics and /healthz from the main process once the worker is consuming."""
    global _exporter
    _exporter = start_exporter(get_settings())
    WORKER_UP.labels(socket.gethostname()).set(1)


@worker_shutdown.connect
def _stop_metrics(**_: object) -> None:
    WORKER_UP.labels(socket.gethostname()).set(0)
    if _exporter is not None:
        _exporter.shutdown()


@worker_process_shutdown.connect
def _forget_process(pid: int | None = None, **_: object) -> None:
    """A pool process that exits mid-job must stop counting as running."""
    if pid is not None:
        mark_process_dead(pid)
