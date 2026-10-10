"""The Celery task: a thin wrapper around :func:`app.jobs.executor.execute_job`."""

from __future__ import annotations

import uuid

from celery.signals import worker_process_init

from app.core.config import get_settings
from app.db.session import get_engine, get_session_factory
from app.jobs.celery_app import RUN_JOB_TASK, celery_app
from app.jobs.executor import execute_job
from app.jobs.queue import CeleryJobQueue


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
