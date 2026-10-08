# Football Data Platform

Imports [StatsBomb Open Data](https://github.com/statsbomb/open-data) for the **UEFA Champions
League 2017/18** season into PostgreSQL and exposes it through a read-only FastAPI REST API.

- Idempotent importer keyed on stable StatsBomb identifiers
- Normalized relational schema managed with Alembic
- Paginated, filterable read endpoints with generated OpenAPI docs
- 200+ tests; unit tests run without Docker

## Requirements

| Tool | Version |
| --- | --- |
| Python | 3.12+ |
| [uv](https://docs.astral.sh/uv/) | any recent |
| Docker | for local PostgreSQL |

## Quick start

```bash
# 1. Install dependencies
uv sync --all-groups

# 2. Configure
cp .env.example .env

# 3. Start PostgreSQL
docker compose up -d db

# 4. Create the schema
uv run alembic upgrade head

# 5. Fetch source data (see "Source data" below)
./scripts/fetch_statsbomb_data.sh

# 6. Import
uv run python -m app.ingestion.cli ingest --source-dir ./data/raw

# 7. Serve
uv run uvicorn app.main:app --reload

# 8. Test
uv run pytest
```

The API is then at <http://localhost:8000>, with interactive docs at
<http://localhost:8000/docs>.

## Configuration

All settings come from environment variables, falling back to `.env`. Defaults are suitable for
local development only; never commit real credentials.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://football:football@localhost:5432/football_data` | PostgreSQL connection. Must be a PostgreSQL URL. |
| `APP_ENV` | `development` | One of `development`, `test`, `production`. |
| `LOG_LEVEL` | `INFO` | Standard Python log level. |
| `STATSBOMB_SOURCE_DIR` | `./data/raw` | Default source directory for ingestion. |
| `API_HOST` | `0.0.0.0` | Host for the HTTP server. |
| `API_PORT` | `8000` | Port for the HTTP server. |
| `STATSBOMB_BASE_URL` | unset | Optional base URL for downloading source files. |

Invalid configuration fails immediately with a clear message — a non-PostgreSQL `DATABASE_URL`,
an out-of-range `API_PORT`, or an unknown `APP_ENV` all abort startup.

`docker compose` additionally reads `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`POSTGRES_PORT`, which default to `football_data` / `football` / `football` / `5432`.

## Source data

The importer reads a local directory laid out like the StatsBomb Open Data repository:

```text
data/raw/
  competitions.json
  matches/<competition_id>/<season_id>.json
  events/<match_id>.json
  lineups/<match_id>.json
```

The numeric path components are **not** hard-coded. The importer reads `competitions.json`, finds
the entry whose competition and season names match, and derives the identifiers from it. For UEFA
Champions League 2017/18 this resolves to `matches/16/1.json`.

To download just that season:

```bash
./scripts/fetch_statsbomb_data.sh
```

The `data/` directory is gitignored.

## Ingestion

```bash
uv run python -m app.ingestion.cli ingest --source-dir ./data/raw
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--source-dir` | `$STATSBOMB_SOURCE_DIR` | Directory holding the JSON files. |
| `--competition` | `Champions League` | Competition name to import. |
| `--season` | `2017/2018` | Season name to import. |

The command prints a summary:

```text
Ingestion summary
  matches processed : 1
  matches skipped   : 0
  events imported   : 3497
  events skipped    : 0
  lineups imported  : 36
  lineups skipped   : 0
  validation failures: 0
```

Behavior:

- **Idempotent.** Re-running against the same source updates rows instead of inserting duplicates.
  Records are matched on their StatsBomb external IDs.
- **Non-destructive.** Null values in the source never overwrite data already stored, and the
  importer never deletes unrelated rows.
- **Resilient.** An individual event or lineup entry that fails validation (malformed location,
  unparseable timestamp, missing identifier, duplicate index) is skipped and reported with its
  source file, match ID, record ID, and reason. A structurally invalid file or an unresolvable
  competition/season aborts the command.
- **Explicit.** Ingestion never runs automatically on API startup.

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `3` | Invalid configuration |
| `4` | Missing or malformed source data |
| `5` | Database unreachable or write failure |

## Database schema

Seven tables, all managed by Alembic migrations in `alembic/versions/`:

| Table | Notes |
| --- | --- |
| `competitions` | Unique `statsbomb_id`. |
| `seasons` | Unique `(competition_id, statsbomb_id)`. |
| `teams`, `players` | Unique `statsbomb_id`. |
| `matches` | Unique `statsbomb_id`; indexed on competition/season, match date, and both team FKs. |
| `events` | Unique `statsbomb_id` and `(match_id, index_in_match)`; indexed on `type_name`, `team_id`, `player_id`. Locations stored as separate `location_x` / `location_y`. |
| `lineups` | One row per player in a match squad; unique `(match_id, team_id, player_id)`. |

`events.raw_data` and `lineups.raw_data` are nullable JSONB columns holding a small fragment of
unmodelled source metadata. They supplement, and never replace, the normalized columns.

Common migration commands:

```bash
uv run alembic upgrade head        # apply
uv run alembic downgrade -1        # roll back one revision
uv run alembic check               # confirm models and schema agree
```

## API

Everything is prefixed with `/api/v1` and returns JSON. V1 is read-only — no create, update, or
delete endpoints exist.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Application and database reachability. Returns 503 if the database is down. |
| `GET` | `/api/v1/competitions` | List competitions. |
| `GET` | `/api/v1/competitions/{competition_id}` | One competition. |
| `GET` | `/api/v1/competitions/{competition_id}/seasons` | Seasons of a competition. |
| `GET` | `/api/v1/teams` | List teams. |
| `GET` | `/api/v1/teams/{team_id}` | One team. |
| `GET` | `/api/v1/players` | List players. |
| `GET` | `/api/v1/players/{player_id}` | One player. |
| `GET` | `/api/v1/matches` | List matches. |
| `GET` | `/api/v1/matches/{match_id}` | One match. |
| `GET` | `/api/v1/matches/{match_id}/events` | Events of a match. |
| `GET` | `/api/v1/matches/{match_id}/lineups` | Squad rows of a match. |

### Identifiers

Every public `id` is the **stable StatsBomb external identifier**, not an internal primary key.
Match `18245`, team `220`, and competition `16` are StatsBomb IDs, and filter parameters use the
same scheme.

### Pagination

Collection endpoints use offset pagination and return a consistent envelope.

| Parameter | Default | Constraint |
| --- | --- | --- |
| `limit` | `50` | `1 <= limit <= 100` |
| `offset` | `0` | `offset >= 0` |

```json
{ "items": [], "limit": 50, "offset": 0, "total": 0 }
```

Out-of-range or non-numeric values return HTTP 422.

### Filters

Only these filters are supported; anything else is ignored.

| Endpoint | Filters |
| --- | --- |
| `/matches` | `competition_id`, `season_id`, `team_id` (home or away), `match_date` |
| `/matches/{id}/events` | `type_name`, `team_id`, `player_id`, `period` |
| `/teams`, `/players` | `q` (case-insensitive name search) |

### Examples

```bash
curl http://localhost:8000/health

curl http://localhost:8000/api/v1/competitions

curl "http://localhost:8000/api/v1/matches?competition_id=16&season_id=1"

curl "http://localhost:8000/api/v1/matches/18245/events?type_name=Shot&limit=5"

curl "http://localhost:8000/api/v1/matches/18245/lineups"

curl "http://localhost:8000/api/v1/players?q=salah"
```

### Errors

| Status | Body | When |
| --- | --- | --- |
| 404 | `{"detail": "Match not found"}` | Unknown resource |
| 422 | FastAPI validation detail | Malformed query or path parameter |
| 500 | `{"detail": "Internal server error"}` | Unexpected failure; the cause is logged server-side only |

OpenAPI documentation is generated by FastAPI and served at `/docs`, `/redoc`, and
`/openapi.json`.

## Testing

```bash
uv run pytest                                   # unit tests, no Docker required
uv run pytest --cov=app --cov-report=term-missing
```

Unit tests run against an in-memory SQLite database created from the same models, using
deterministic fixtures in `tests/fixtures/raw/`. They never reach the network or download the full
source dataset.

Integration tests are marked `integration` and are skipped unless `TEST_DATABASE_URL` points at a
**scratch** PostgreSQL database, which they drop and recreate:

```bash
docker compose up -d db
docker compose exec db psql -U football -d postgres \
  -c "CREATE DATABASE football_test OWNER football;"

TEST_DATABASE_URL=postgresql+psycopg://football:football@localhost:5432/football_test \
  uv run pytest -m integration
```

## Development

```bash
uv run ruff format .     # format
uv run ruff check .      # lint
uv run mypy              # type check
```

## Project layout

```text
app/
  api/            routes, response schemas, shared dependencies
  core/           configuration, exceptions, logging
  db/             declarative base, models, session management
  ingestion/      loader, validators, normalizers, persistence, service, CLI
  repositories/   read-only queries backing the API
  main.py         FastAPI application factory
alembic/          migrations
scripts/          helper scripts
tests/            api, ingestion, integration, fixtures
```

Route handlers stay thin: database queries live in `app/repositories/`, and all StatsBomb-specific
mapping lives in `app/ingestion/` — never in routes or ORM models.

## Future work

Deliberately out of scope for V1, recorded here rather than built:

- Downloading source data directly from StatsBomb inside the importer
- Importing additional competitions and seasons in one run
- Dedicated tables or typed models for individual event subtypes (passes, shots, carries)
- Derived analytics such as xG aggregates, player match statistics, and team form
- Cursor-based pagination and richer query filters
- Caching, background ingestion, and parallel event loading
- Authentication, rate limiting, and multi-tenancy
