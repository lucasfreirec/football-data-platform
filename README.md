# Football Data Platform (In progress)

A Python platform that imports StatsBomb Open Data, stores normalized football data in PostgreSQL, and exposes it through FastAPI. It also runs ingestion and match-statistics jobs asynchronously with Redis-backed Celery workers.

The default data is the FIFA World Cup 2022 (`competition_id=43`, `season_id=106`), which provides a meaningful multi-match workload for asynchronous processing, analytics, and worker benchmarks. The Champions League 2017/18 final remains compatible as a small smoke-test dataset when explicitly selected.

## Features

- Imports competitions, seasons, teams, players, matches, lineups, events, passes, and shots.
- Provides read-only, paginated REST endpoints and player/team statistics, including xG.
- Makes ingestion idempotent and records source manifests, missing files, and validation failures.
- Queues background match-ingestion and match-statistics jobs.
- Tracks jobs as `pending`, `running`, `retrying`, `completed`, or `failed`.
- Retries transient failures and can reconcile stale jobs after a worker or broker interruption.
- Runs API, PostgreSQL, Redis, and workers as separate Compose services.
- Includes Prometheus metrics, a Grafana dashboard, and Kubernetes manifests for API/worker scaling.

## Architecture

```text
StatsBomb JSON ──> ingestion CLI / jobs ──> PostgreSQL ──> FastAPI
                            │                    ▲             │
                            ▼                    │             ▼
                       Redis queue ──> workers ──┘        Swagger / client
```

PostgreSQL is the system of record. Redis transports background jobs; job state and results stay durable in PostgreSQL.

## Run locally

Requirements: Docker Desktop, Python 3.12+, `uv`, `curl`, and `bash`.

```bash
git clone <repository-url>
cd football-data-platform
cp .env.example .env
uv sync --all-groups

# Download the StatsBomb source files.
./scripts/fetch_statsbomb_data.sh "FIFA World Cup" 2022 ./data/raw

# Start PostgreSQL, Redis, migrations, the API, and a worker.
docker compose up --build -d
```

Open [Swagger UI](http://localhost:8000/docs), then check the service:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","database":"ok"}
```

## Import and query data

Use either the explicit CLI or a background job.

```bash
# Direct import
uv run python -m app.ingestion.cli ingest --source-dir ./data/raw \
  --competition "FIFA World Cup" --season 2022

# Background import; the worker has data/raw mounted read-only.
curl -X POST http://localhost:8000/api/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"job_type":"ingest_match","match_id":3857276,"competition":"FIFA World Cup","season":"2022"}'

# Check the returned job ID
curl http://localhost:8000/api/v1/jobs/<job-id>
```

After the import completes:

```bash
curl 'http://localhost:8000/api/v1/competitions'
curl 'http://localhost:8000/api/v1/matches?competition_id=43&season_id=106'
curl 'http://localhost:8000/api/v1/matches/3857276/passes?limit=5'
curl 'http://localhost:8000/api/v1/matches/3857276/shots?limit=5'
curl 'http://localhost:8000/api/v1/competitions/43/seasons/106/player-stats?limit=10'
```

Run additional workers locally:

```bash
docker compose up -d --scale worker=2
```

Recover jobs that stopped progressing after worker/broker interruption:

```bash
uv run python -m app.jobs.cli reconcile-stale --older-than-minutes 15
```

## Monitoring

Start Prometheus and Grafana:

```bash
docker compose --profile monitoring up -d
```

- Prometheus: [http://localhost:9090](http://localhost:9090)
- Grafana: [http://localhost:3000](http://localhost:3000) — `admin` / `admin` by default

The dashboard shows job throughput, failures, durations, API traffic, worker health, and queue activity.

## Kubernetes

`k8s/` contains Kustomize manifests for a namespace, API deployment/service, worker deployment, worker HPA, migration job, Secret example, and optional stale-job reconciliation CronJob.

PostgreSQL and Redis are assumed to be external managed services. Workers also need a read-only `statsbomb-data` PVC containing the downloaded source layout.

```bash
# Create credentials with real values; do not commit them.
kubectl -n football-data create secret generic football-secrets \
  --from-literal=DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME' \
  --from-literal=BROKER_URL='redis://HOST:6379/0'

# Build/push the image and update k8s/base/kustomization.yaml first.
kubectl apply -k k8s/base
kubectl create -f k8s/migrate-job.yaml

# Optional stale-job reconciliation every ten minutes.
kubectl apply -k k8s/overlays/with-reconcile
```

Workers scale from 1 to 5 replicas based on CPU utilization. Validate manifests before deployment:

```bash
kubectl kustomize k8s/base
kubectl kustomize k8s/overlays/with-reconcile
```

## Validate

```bash
uv run pytest
uv run ruff check .
uv run mypy app
```

PostgreSQL/Redis integration tests require disposable `TEST_DATABASE_URL` and `TEST_BROKER_URL` values.

## Project structure

```text
app/           API, ingestion, jobs, metrics, database models, and queries
alembic/       database migrations
k8s/           Kustomize deployment manifests
monitoring/    Prometheus and Grafana configuration
scripts/       StatsBomb download script
tests/         unit and integration tests
```