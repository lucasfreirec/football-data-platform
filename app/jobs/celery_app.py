"""The Celery application, shared by the API (to enqueue) and the workers (to run).

Importing this module only builds the app object; it does not connect to the
broker and does not import any worker code, so the API stays light.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import Settings, get_settings

RUN_JOB_TASK = "app.jobs.run_job"


def create_celery_app(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery("football", broker=settings.broker_url)
    app.conf.update(
        task_default_queue=settings.job_queue_name,
        task_serializer="json",
        accept_content=["json"],
        # Outcomes live in PostgreSQL, not in a result backend.
        task_ignore_result=True,
        # Acknowledge after the work, and redeliver if the worker process dies mid-task.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # One message at a time per slot, so slots stay evenly loaded and a crashed
        # worker strands at most the jobs it was actually running.
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=3,
        broker_transport_options={"visibility_timeout": 3600, "socket_connect_timeout": 3},
        # Publishing fails fast so an unavailable broker surfaces as an API error.
        task_publish_retry_policy={
            "max_retries": 2,
            "interval_start": 0,
            "interval_step": 0.2,
            "interval_max": 0.5,
        },
        timezone="UTC",
        enable_utc=True,
        imports=("app.jobs.tasks",),
    )
    return app


celery_app = create_celery_app()
