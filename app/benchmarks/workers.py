"""Benchmark: job throughput at different worker counts.

    python -m app.benchmarks.workers --worker-counts 1,2,4 --jobs-per-run 20

For each worker count it starts a fresh worker process pool, warms it up on
separate matches, wipes the data, then times how long it takes to finish the same
set of ``ingest_match`` jobs. Job rows, queue messages, retries, and database
writes are all real. Results go to ``artifacts/benchmarks/`` as JSON and Markdown.

It truncates tables, so it refuses to run against any database whose name does
not end in ``_benchmark``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.benchmarks.workload import WorkloadSpec, write_workload
from app.core.config import load_settings
from app.db.models import Base, JobStatus, JobType, ProcessingJob
from app.jobs.celery_app import create_celery_app
from app.jobs.queue import CeleryJobQueue
from app.jobs.service import JobService

DEFAULT_DATABASE_URL = "postgresql+psycopg://football:football@localhost:5432/football_benchmark"
DEFAULT_BROKER_URL = "redis://localhost:6379/7"
DEFAULT_OUTPUT_DIR = Path("artifacts/benchmarks")
POLL_SECONDS = 0.05
RUN_TIMEOUT_SECONDS = 900.0
TERMINAL = (JobStatus.completed.value, JobStatus.failed.value)


@dataclass
class RunResult:
    workers: int
    repeat: int
    jobs: int
    elapsed_seconds: float
    completed: int
    failed: int
    throughput_jobs_per_minute: float
    duration_p50: float
    duration_p90: float
    duration_p95: float
    duration_max: float
    peak_running: int
    distinct_worker_processes: int
    retries: int
    events_stored: int
    events_expected: int
    data_consistent: bool


@dataclass
class BenchmarkResults:
    parameters: dict[str, Any]
    environment: dict[str, Any]
    runs: list[RunResult] = field(default_factory=list)


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile; 0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def require_benchmark_database(url: str) -> None:
    name = make_url(url).database or ""
    if not name.endswith("_benchmark"):
        raise SystemExit(
            f"Refusing to run: database {name!r} does not end in '_benchmark'. "
            "The benchmark truncates tables; point --database-url at a dedicated database."
        )


def ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


def reset_schema(engine: Engine) -> None:
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.create_all(engine)


def truncate_all(engine: Engine) -> None:
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


class WorkerProcess:
    """A real `python -m app.jobs.worker` subprocess."""

    def __init__(self, workers: int, env: dict[str, str], celery_app: Any, log: Path) -> None:
        self._log = log.open("w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app.jobs.worker", "--concurrency", str(workers)],
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._celery = celery_app

    def wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("the worker exited during startup; see its log")
            if self._celery.control.inspect(timeout=1.0).ping():
                return
        raise RuntimeError("the worker did not become ready in time")

    def stop(self) -> None:
        if self.process.poll() is None:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        self._log.close()


def submit_jobs(
    settings: Any, celery_app: Any, queue_name: str, engine: Engine, ids: list[int]
) -> list[uuid.UUID]:
    job_ids = []
    with Session(engine, expire_on_commit=False) as session:
        service = JobService(session, CeleryJobQueue(celery_app, queue_name), settings)
        for match_id in ids:
            job_ids.append(service.submit(JobType.ingest_match, match_id).job.id)
    return job_ids


def wait_for_jobs(engine: Engine, job_ids: list[uuid.UUID]) -> int:
    """Block until every job is terminal; return the peak number running at once."""
    peak = 0
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            rows = connection.execute(
                select(ProcessingJob.status, func.count())
                .where(ProcessingJob.id.in_(job_ids))
                .group_by(ProcessingJob.status)
            ).all()
        counts = {status: total for status, total in rows}
        peak = max(peak, counts.get(JobStatus.running.value, 0))
        if sum(counts.get(status, 0) for status in TERMINAL) == len(job_ids):
            return peak
        time.sleep(POLL_SECONDS)
    raise RuntimeError("the jobs did not finish before the timeout")


def run_once(
    workers: int,
    repeat: int,
    spec: WorkloadSpec,
    warmup_jobs: int,
    source_dir: Path,
    measured_ids: list[int],
    warmup_ids: list[int],
    database_url: str,
    broker_url: str,
    log_dir: Path,
) -> RunResult:
    queue_name = f"football-bench-{uuid.uuid4().hex[:8]}"
    settings = load_settings(
        database_url=database_url,
        broker_url=broker_url,
        job_queue_name=queue_name,
        statsbomb_source_dir=source_dir,
        _env_file=None,
    )
    celery_app = create_celery_app(settings)
    engine = create_engine(database_url, pool_size=5, pool_pre_ping=True)
    reset_schema(engine)

    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "BROKER_URL": broker_url,
        "JOB_QUEUE_NAME": queue_name,
        # Pools on one host would all contend for the same exporter port.
        "WORKER_METRICS_PORT": "0",
        "STATSBOMB_SOURCE_DIR": str(source_dir),
        "LOG_LEVEL": "WARNING",
    }
    worker = WorkerProcess(workers, env, celery_app, log_dir / f"worker-{workers}-{repeat}.log")
    try:
        worker.wait_ready()
        # Warm up every slot on matches that are not measured: imports, connections, caches.
        warm = submit_jobs(
            settings, celery_app, queue_name, engine, warmup_ids[: max(warmup_jobs, workers * 2)]
        )
        wait_for_jobs(engine, warm)
        truncate_all(engine)

        started = time.perf_counter()
        job_ids = submit_jobs(settings, celery_app, queue_name, engine, measured_ids)
        peak = wait_for_jobs(engine, job_ids)
        elapsed = time.perf_counter() - started

        with Session(engine, expire_on_commit=False) as session:
            jobs = list(session.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(job_ids))))
            events_stored = session.scalar(text("SELECT count(*) FROM events")) or 0
            matches_stored = session.scalar(text("SELECT count(*) FROM matches")) or 0
    finally:
        worker.stop()
        engine.dispose()
        try:
            import redis

            redis.Redis.from_url(broker_url).delete(queue_name)
        except Exception:
            pass

    completed = [job for job in jobs if job.status == JobStatus.completed.value]
    durations = [
        (job.completed_at - job.started_at).total_seconds()
        for job in completed
        if job.completed_at and job.started_at
    ]
    expected = len(completed) * spec.events_per_match
    return RunResult(
        workers=workers,
        repeat=repeat,
        jobs=len(job_ids),
        elapsed_seconds=round(elapsed, 3),
        completed=len(completed),
        failed=len(jobs) - len(completed),
        throughput_jobs_per_minute=round(len(completed) / elapsed * 60, 2),
        duration_p50=round(percentile(durations, 0.50), 3),
        duration_p90=round(percentile(durations, 0.90), 3),
        duration_p95=round(percentile(durations, 0.95), 3),
        duration_max=round(max(durations, default=0.0), 3),
        peak_running=peak,
        distinct_worker_processes=len({job.result["worker"] for job in completed if job.result}),
        retries=sum(max(job.attempt_count - 1, 0) for job in jobs),
        events_stored=events_stored,
        events_expected=expected,
        data_consistent=events_stored == expected and matches_stored == len(completed),
    )


def environment_info(database_url: str, broker_url: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }
    try:
        engine = create_engine(database_url)
        with engine.connect() as connection:
            info["postgresql"] = str(connection.scalar(text("SHOW server_version")))
        engine.dispose()
        import redis

        server_info = cast(dict[str, Any], redis.Redis.from_url(broker_url).info("server"))
        info["redis"] = server_info["redis_version"]
    except Exception:
        pass
    with contextlib.suppress(Exception):
        info["git_commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    return info


def summarise(runs: list[RunResult]) -> list[RunResult]:
    """The median-elapsed run for each worker count, in ascending worker order."""
    chosen = []
    for count in sorted({run.workers for run in runs}):
        group = sorted(
            (run for run in runs if run.workers == count), key=lambda r: r.elapsed_seconds
        )
        chosen.append(group[(len(group) - 1) // 2])
    return chosen


def render_report(results: BenchmarkResults) -> str:
    summary = summarise(results.runs)
    baseline = next((run for run in summary if run.workers == 1), summary[0])
    params, env = results.parameters, results.environment
    lines = [
        "# Worker-count benchmark",
        "",
        f"Recorded {env.get('recorded_at', '?')} at commit `{env.get('git_commit', 'unknown')}`.",
        "",
        "## Workload",
        "",
        f"- {params['jobs_per_run']} independent `ingest_match` jobs per run, one match each",
        f"- {params['events_per_match']} events per match (about "
        f"{params['jobs_per_run'] * params['events_per_match']:,} events in total), generated "
        f"deterministically with seed {params['seed']}",
        "- matches share 8 teams and their players, so workers contend on shared reference rows",
        f"- each worker count is run {params['repeats']} time(s); the median-elapsed run is shown",
        "- a fresh worker pool is started per run, warmed up on separate matches, and the data "
        "wiped before timing",
        "",
        "## Results",
        "",
        "| Workers | Elapsed (s) | Jobs/min | Speedup | Efficiency | Job p50 (s) | Job p95 (s) "
        "| Peak running | Worker processes used | Failed |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in summary:
        speedup = baseline.elapsed_seconds / run.elapsed_seconds
        lines.append(
            f"| {run.workers} | {run.elapsed_seconds:.1f} | {run.throughput_jobs_per_minute:.1f} "
            f"| {speedup:.2f}x | {speedup / run.workers:.0%} | {run.duration_p50:.2f} "
            f"| {run.duration_p95:.2f} | {run.peak_running} | {run.distinct_worker_processes} "
            f"| {run.failed} |"
        )
    consistent = all(run.data_consistent for run in results.runs)
    lines += [
        "",
        "*Speedup* is elapsed time relative to one worker; *efficiency* is speedup divided by "
        "worker count (100% would be perfectly linear). *Peak running* is the most jobs seen "
        "in the `running` state at once, and *worker processes used* is how many distinct "
        "processes executed jobs, which together show that extra slots changed behaviour "
        "rather than only a setting.",
        "",
        f"Data integrity after every run (matches and events stored equal what the completed "
        f"jobs should have written): **{'consistent' if consistent else 'INCONSISTENT'}**.",
        "",
        "## Environment",
        "",
        f"- {env.get('platform', '?')}, {env.get('machine', '?')}, "
        f"{env.get('cpu_count', '?')} CPUs",
        f"- Python {env.get('python', '?')}, PostgreSQL {env.get('postgresql', '?')}, "
        f"Redis {env.get('redis', '?')}",
        "",
        "## Caveats",
        "",
        "- Everything runs on one machine, so the worker pool, PostgreSQL, Redis, and this "
        "driver compete for the same CPUs; PostgreSQL ran in a Docker VM on a laptop.",
        "- The workload is synthetic and CPU-and-database mixed. Real StatsBomb matches or "
        "other hardware will scale differently, so treat these as an illustration of this "
        "setup, not a universal gain.",
        "- Throughput stops improving once the CPUs, or the database's write capacity, "
        "saturate; efficiency below 100% is expected.",
        "- With few jobs per run the last job to start sets a floor on elapsed time, which "
        "penalises higher worker counts a little.",
        "- Treat differences of a few percent as noise.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "docker compose up -d db broker",
        f"uv run python -m app.benchmarks.workers --worker-counts "
        f"{','.join(str(r.workers) for r in summary)} --jobs-per-run {params['jobs_per_run']} "
        f"--events-per-match {params['events_per_match']} --repeats {params['repeats']}",
        "```",
        "",
    ]
    return "\n".join(lines)


def parse_worker_counts(value: str) -> list[int]:
    try:
        counts = [int(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("worker counts must be comma-separated integers") from exc
    if not counts or any(count < 1 for count in counts):
        raise argparse.ArgumentTypeError("worker counts must be positive integers")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare job throughput across worker counts.")
    parser.add_argument("--worker-counts", type=parse_worker_counts, default=[1, 2, 4])
    parser.add_argument("--jobs-per-run", type=int, default=20)
    parser.add_argument("--events-per-match", type=int, default=3000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--warmup-jobs", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument(
        "--database-url", default=os.environ.get("BENCHMARK_DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    parser.add_argument(
        "--broker-url", default=os.environ.get("BENCHMARK_BROKER_URL", DEFAULT_BROKER_URL)
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    if args.jobs_per_run < 1 or args.events_per_match < 1 or args.repeats < 1:
        parser.error("--jobs-per-run, --events-per-match and --repeats must be at least 1")

    require_benchmark_database(args.database_url)
    ensure_database(args.database_url)

    warmup = max(args.warmup_jobs, max(args.worker_counts) * 2)
    spec = WorkloadSpec(
        matches=args.jobs_per_run + warmup, events_per_match=args.events_per_match, seed=args.seed
    )
    work_dir = Path(tempfile.mkdtemp(prefix="football-benchmark-"))
    try:
        ids = write_workload(spec, work_dir / "raw")
        measured, warmup_ids = ids[: args.jobs_per_run], ids[args.jobs_per_run :]
        results = BenchmarkResults(
            parameters={
                "worker_counts": args.worker_counts,
                "jobs_per_run": args.jobs_per_run,
                "events_per_match": args.events_per_match,
                "repeats": args.repeats,
                "warmup_jobs": warmup,
                "seed": args.seed,
            },
            environment=environment_info(args.database_url, args.broker_url),
        )
        for workers in args.worker_counts:
            for repeat in range(1, args.repeats + 1):
                print(
                    f"Running {workers} worker(s), repeat {repeat}/{args.repeats} ...", flush=True
                )
                run = run_once(
                    workers,
                    repeat,
                    spec,
                    args.warmup_jobs,
                    work_dir / "raw",
                    measured,
                    warmup_ids,
                    args.database_url,
                    args.broker_url,
                    work_dir,
                )
                results.runs.append(run)
                print(
                    f"  {run.elapsed_seconds:.1f}s  {run.throughput_jobs_per_minute:.1f} jobs/min  "
                    f"peak running {run.peak_running}  failed {run.failed}  "
                    f"consistent={run.data_consistent}",
                    flush=True,
                )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "parameters": results.parameters,
        "environment": results.environment,
        "runs": [asdict(run) for run in results.runs],
        "summary": [asdict(run) for run in summarise(results.runs)],
    }
    (args.output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (args.output_dir / "report.md").write_text(render_report(results))
    print(f"\nWrote {args.output_dir / 'results.json'} and {args.output_dir / 'report.md'}")
    return 0 if all(run.failed == 0 and run.data_consistent for run in results.runs) else 1


if __name__ == "__main__":
    sys.exit(main())
