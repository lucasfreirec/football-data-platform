"""Job guarantees that only real PostgreSQL can prove (needs ``TEST_DATABASE_URL``)."""

from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import load_settings
from app.db.models import ProcessingJob
from app.jobs import handlers
from app.jobs.executor import execute_job
from tests.jobs.conftest import FakeQueue

pytestmark = pytest.mark.integration


def _job(match: int, status: str = "pending") -> ProcessingJob:
    return ProcessingJob(
        job_type="ingest_match",
        status=status,
        statsbomb_match_id=match,
        max_attempts=3,
        payload={"statsbomb_match_id": match},
    )


def test_the_partial_unique_index_allows_one_active_job_per_match(pg_session: Session) -> None:
    pg_session.add(_job(9001, "running"))
    pg_session.commit()

    pg_session.add(_job(9001, "pending"))
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()

    pg_session.add_all([_job(9002), _job(9001, "completed"), _job(9001, "failed")])
    pg_session.commit()


def test_concurrent_workers_importing_matches_that_share_teams_and_players(
    pg_engine: Engine, pg_session: Session, v2_source_dir: Path
) -> None:
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    settings = load_settings(statsbomb_source_dir=v2_source_dir, _env_file=None)
    outcomes: Counter[str] = Counter()

    for _ in range(8):
        with pg_engine.begin() as connection:
            connection.execute(
                text("TRUNCATE events, matches, teams, players RESTART IDENTITY CASCADE")
            )
            connection.execute(text("DELETE FROM processing_jobs"))
        ids = []
        for match in (9001, 9002):
            job = _job(match)
            pg_session.add(job)
            pg_session.commit()
            ids.append(job.id)

        barrier = threading.Barrier(2)

        def run(job_id: object, gate: threading.Barrier = barrier) -> None:
            gate.wait()
            outcomes[
                execute_job(
                    job_id,  # type: ignore[arg-type]
                    session_factory=factory,
                    queue=FakeQueue(),
                    settings=settings,
                )
            ] += 1

        threads = [threading.Thread(target=run, args=(job_id,)) for job_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with pg_engine.connect() as connection:
            totals = tuple(
                connection.scalar(text(f"select count(*) from {table}"))
                for table in ("events", "teams", "players")
            )
        assert totals == (19, 2, 6)

    assert outcomes == {"completed": 16}


def test_a_match_locked_by_another_worker_is_retried_not_run_twice(
    pg_engine: Engine, pg_session: Session, v2_source_dir: Path
) -> None:
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    settings = load_settings(statsbomb_source_dir=v2_source_dir, _env_file=None)
    queue = FakeQueue()
    job = _job(9001)
    pg_session.add(job)
    pg_session.commit()
    key = handlers._advisory_key("ingest_match", 9001)

    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        outcome = execute_job(job.id, session_factory=factory, queue=queue, settings=settings)
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})

    pg_session.refresh(job)
    assert outcome == "retrying"
    assert job.status == "retrying" and job.attempt_count == 1
    assert queue.sent == [(job.id, 5)]
    assert (
        execute_job(job.id, session_factory=factory, queue=queue, settings=settings) == "completed"
    )
