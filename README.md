# Football Data Platform

Imports [StatsBomb Open Data](https://github.com/statsbomb/open-data) for the **UEFA Champions
League 2017/18** season into PostgreSQL and exposes it through a read-only FastAPI REST API.

- Reproducible source download, pinned to a repository revision
- Idempotent importer keyed on stable StatsBomb identifiers, with a manifest of every run
- Normalized events plus dedicated pass and shot detail tables
- Paginated, filterable read endpoints and season statistics, with generated OpenAPI docs
- 550+ tests; the unit tests run without Docker

> **What "the whole season" means here.** StatsBomb Open Data does not contain every historical
> match of a tournament. For Champions League 2017/18 the source list holds **one match** (the
> final, Real Madrid 3–1 Liverpool). Everything this project computes is derived from the matches
> actually present in the source, and the import summary and manifest always state how many were
> listed. See [What the source contains](#what-the-source-contains).

## Requirements

| Tool | Version |
| --- | --- |
| Python | 3.12+ |
| [uv](https://docs.astral.sh/uv/) | any recent |
| Docker | for local PostgreSQL |
| `bash`, `curl`, `python3` | for the download script (macOS and Linux defaults are fine) |

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

# 5. Download the source data (pinned to one revision)
./scripts/fetch_statsbomb_data.sh Champions League 2017/2018 ./data/raw

# 6. Import
uv run python -m app.ingestion.cli ingest --source-dir ./data/raw

# 7. Serve
uv run uvicorn app.main:app --reload

# 8. Test
uv run pytest
```

The API is then at <http://localhost:8000>, with interactive docs at
<http://localhost:8000/docs>.

### Upgrading an existing V1 database

```bash
uv run alembic upgrade head
uv run python -m app.ingestion.cli ingest --source-dir ./data/raw
```

The migration only adds tables. A V1 database already holds events but has no pass or shot
details, so the statistics endpoints return zeros until the import is re-run. Re-importing is
safe: it updates rows in place and never duplicates them.

If `docker compose up -d db` fails with a socket error, Docker Desktop is not running
(`open -a Docker` on macOS); it does not start automatically after a reboot.

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
| `STATSBOMB_BASE_URL` | unset | Only the download script uses this, and it reads it from the shell environment, not `.env`. The application itself ignores it. |

Invalid configuration fails immediately with a clear message — a non-PostgreSQL `DATABASE_URL`,
an out-of-range `API_PORT`, or an unknown `APP_ENV` all abort startup.

`docker compose` additionally reads `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
`POSTGRES_PORT`, which default to `football_data` / `football` / `football` / `5432`.

The download script has its own options, read from the shell environment (not `.env`); see
[Downloading the source](#downloading-the-source).

## What the source contains

The importer reads a local directory laid out like the StatsBomb Open Data repository:

```text
data/raw/
  SOURCE_REVISION                      written by the download script
  competitions.json
  matches/<competition_id>/<season_id>.json
  events/<match_id>.json
  lineups/<match_id>.json
```

The numeric path components are **not** hard-coded. The importer reads `competitions.json`, finds
the entry whose competition and season names match, and derives the identifiers from it. For UEFA
Champions League 2017/18 that resolves to competition `16`, season `1`, i.e. `matches/16/1.json`
(season `4` is 2018/19, which is easy to mix up).

**Source versus tournament.** `competitions.json` and the match list describe what StatsBomb has
published, not the tournament as played. Open Data can lack matches, and individual event or lineup
files can be missing for a listed match. Two figures therefore matter and are reported on every
import:

| Figure | Meaning |
| --- | --- |
| `matches listed` | Matches present in the source list. This is the ceiling for everything below. |
| `event files` / `lineup files` | How many of those matches actually have the file. |

At the time of writing the 2017/18 list holds 1 match. Treat any statistic as a statement about
the imported matches, not about the competition.

The `data/` directory is gitignored.

## Downloading the source

```bash
./scripts/fetch_statsbomb_data.sh Champions League 2017/2018 ./data/raw
```

The competition name may be quoted or not, in any case. With no arguments it uses
`Champions League`, `2017/2018`, and `./data/raw`.

The script:

1. downloads `competitions.json`;
2. resolves the competition and season names to identifiers, case-insensitively;
3. downloads the matching `matches/<competition_id>/<season_id>.json`;
4. downloads the event and lineup file for every listed match;
5. reports the matches listed and the files downloaded, skipped, and unavailable.

```text
Matches in the source list : 1
Files downloaded           : 4
Files skipped (up to date) : 0
Files unavailable          : 0
```

**Reproducibility.** The script resolves the branch to a single commit through the GitHub API,
downloads every file from that commit, and records it in `data/raw/SOURCE_REVISION`. A branch that
moves mid-download therefore cannot mix two versions. A file already fetched at the same revision
is skipped; a new revision downloads everything again. If the revision cannot be resolved the
script warns, downloads the branch head, and does not claim reproducibility.

**Failure behavior.** The script exits non-zero if `competitions.json` or the match list cannot be
downloaded or parsed, or if the season name is not found. An unavailable event or lineup file is
reported and listed but is not fatal, because the source may simply not have it. Files are written
to a `.part` file first, so an interrupted download never leaves a truncated JSON file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STATSBOMB_REPO` | `statsbomb/open-data` | GitHub repository to download from. |
| `STATSBOMB_REF` | `master` | Branch, tag, or commit to pin. |
| `STATSBOMB_BASE_URL` | unset | Download from this data URL instead of GitHub. |
| `STATSBOMB_REVISION` | unset | Revision label to record when `STATSBOMB_BASE_URL` is set. |

**Verifying a download:**

```bash
cat data/raw/SOURCE_REVISION                 # the pinned commit
ls data/raw/events data/raw/lineups          # one file per listed match
```

The same revision is stored with each import (see [Source manifest](#source-manifest)), so you can
tell later exactly which source produced the data in the database.

## Ingestion

```bash
uv run python -m app.ingestion.cli ingest --source-dir ./data/raw
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--source-dir` | `$STATSBOMB_SOURCE_DIR` | Directory holding the JSON files. |
| `--competition` | `Champions League` | Competition name to import. |
| `--season` | `2017/2018` | Season name to import. |

The command prints a summary. This is the real output for the 2017/18 source:

```text
Ingestion summary
  matches listed    : 1
  matches processed : 1
  matches skipped   : 0
  event files       : 1 found / 0 missing
  lineup files      : 1 found / 0 missing
  events imported   : 3497
  events skipped    : 0
  passes imported   : 1033
  passes skipped    : 0
  shots imported    : 28
  shots skipped     : 0
  lineups imported  : 36
  lineups skipped   : 0
  validation failures: 0
  import status     : success
  source manifest id: 3
```

When something goes wrong the summary says which of two different things happened:

```text
  import status     : partial
  unavailable files:
    - events file for match 9002: /path/to/events/9002.json
  rejected records:
    - /path/to/events/9001.json (match 9001) record 90010000-...: Expected a two-number array for 'pass.end_location', got [60.0]
```

- **Unavailable files** are absent from the source. They are counted as `missing`, never treated as
  an empty file, and never counted as validation failures.
- **Rejected records** were present but malformed. Each is listed with its source file, match ID,
  record ID, and the reason.

Behavior:

- **Idempotent.** Re-running against the same source updates rows instead of inserting duplicates.
  Records are matched on their StatsBomb external IDs.
- **Non-destructive.** Null values in the source never overwrite data already stored, and the
  importer never deletes unrelated rows. The one deliberate exception is a stale detail row: if a
  re-imported event is no longer a `Pass` or `Shot`, its old `pass_details` or `shot_details` row is
  removed so the tables stay consistent with the event type.
- **Resilient.** An individual event or lineup entry that fails validation is skipped and reported.
  A pass or shot whose detail is malformed rejects only that event, with a reason naming the field
  (for example `pass.end_location` or `shot.statsbomb_xg`). A structurally invalid file aborts the run.
- **Atomic.** An event and its pass or shot detail are written in the same transaction.
- **Explicit.** Ingestion never runs automatically on API startup.

### Source manifest

Every run that resolves its competition and season writes one row to `source_manifests`, so the
database can answer "what was imported, from where, and did it work?".

| Column | Meaning |
| --- | --- |
| `competition_id`, `season_id` | The resolved competition and season. |
| `source_path` | Absolute path of the source directory. |
| `source_version` | Contents of `SOURCE_REVISION`, or `NULL` when the source has none. |
| `match_count_expected` | Matches listed in the source. |
| `event_file_count_found`, `lineup_file_count_found` | Files actually present. |
| `imported_at` | Timezone-aware timestamp. |
| `import_status` | `success`, `partial`, or `failed` (enforced by a check constraint). |
| `summary` | Small JSONB digest of the counts, plus up to 50 rejected records and unavailable files. It never contains raw match or event payloads. |

| Status | When |
| --- | --- |
| `success` | Every listed file was present and no record was rejected. |
| `partial` | An expected event or lineup file was unavailable, or any record was rejected. |
| `failed` | The run could not complete after the season was resolved. |

A **failed** run rolls back everything it wrote, then records a `failed` manifest with the reason
and still exits non-zero, so the diagnosis survives and the status never claims success. No manifest
is written when the run fails before the season resolves (missing directory, unknown season).

```sql
select id, import_status, match_count_expected, event_file_count_found,
       lineup_file_count_found, left(source_version, 12) as revision, imported_at
from source_manifests order by id desc limit 5;
```

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success or partial import |
| `3` | Invalid configuration |
| `4` | Missing or malformed source data |
| `5` | Database unreachable or write failure |

A failed import exits `4` or `5` and still prints its summary, including the manifest id.

## Database schema

Ten tables, all managed by Alembic migrations in `alembic/versions/`:

| Table | Notes |
| --- | --- |
| `competitions` | Unique `statsbomb_id`. |
| `seasons` | Unique `(competition_id, statsbomb_id)`. |
| `teams`, `players` | Unique `statsbomb_id`. |
| `matches` | Unique `statsbomb_id`; indexed on competition/season, match date, and both team FKs. |
| `events` | The canonical timeline for every event type. Unique `statsbomb_id` and `(match_id, index_in_match)`. Locations stored as `location_x` / `location_y`. |
| `lineups` | One row per player in a match squad; unique `(match_id, team_id, player_id)`. |
| `pass_details` | One row per `Pass` event. |
| `shot_details` | One row per `Shot` event. |
| `source_manifests` | One row per import run; see above. |

`events.raw_data` and `lineups.raw_data` are nullable JSONB columns holding a small fragment of
unmodelled source metadata. They supplement, and never replace, the normalized columns.

### Pass and shot details

`pass_details` and `shot_details` use the parent event's id as their own **primary key**
(`ON DELETE CASCADE`), so an event has at most one detail row. Only these two event types get a
table; nothing else is split out.

| `pass_details` | |
| --- | --- |
| `recipient_player_id` | Receiving player (nullable, indexed). |
| `length`, `angle` | Pass geometry. |
| `height_name`, `body_part_name`, `technique_name`, `type_name` | Names from StatsBomb; `type_name` is the pass subtype such as `Recovery` or `Corner`. |
| `end_location_x`, `end_location_y` | Where the pass ended. |
| `outcome_name` | `NULL` means the pass was completed (indexed). |
| `is_cross`, `is_cut_back`, `is_switch`, `is_assisted_shot`, `is_goal_assist` | Flags. |

| `shot_details` | |
| --- | --- |
| `key_pass_event_id` | The pass that created the chance (nullable; `ON DELETE SET NULL`). |
| `statsbomb_xg` | StatsBomb's expected-goals value (nullable, indexed). |
| `outcome_name` | For example `Goal`, `Saved`, `Blocked`, `Off T` (indexed). |
| `end_location_x`, `end_location_y` | Where the shot was headed. |
| `body_part_name`, `technique_name`, `type_name` | `type_name` is open play, free kick, penalty, and so on. |
| `first_time`, `one_on_one`, `open_goal` | Flags. |

Rules worth knowing:

- **Absent means `NULL`, never a guess.** StatsBomb writes its boolean flags only when they are
  true, so a flag is either `true` or `NULL`. `NULL` here means "not recorded", not "false".
- **`is_assisted_shot`** is true when the source carries `shot_assist` or `assisted_shot_id`.
  StatsBomb sets `assisted_shot_id` on every assist but `shot_assist` on fewer passes (16 versus 18
  in the sample match).
- **Shot end locations may have three values.** StatsBomb emits `[x, y, z]` for on-target shots (17
  of the 28 shots in the sample match); `z`, the ball height, has no column and is discarded. Pass
  end locations must be exactly two values. Anything else rejects the event.
- **`statsbomb_xg` must be a finite number**; text, booleans, `NaN`, and infinities reject the event.
- **An unresolvable key pass** keeps the shot with `key_pass_event_id = NULL` and logs a warning,
  rather than discarding a shot that is otherwise valid.

Common migration commands:

```bash
uv run alembic upgrade head        # apply
uv run alembic downgrade -1        # roll back one revision
uv run alembic check               # confirm models and schema agree
```

## Statistics definitions

Season statistics are computed from the persisted events and detail tables **at request time**.
There are no scheduled jobs or precomputed tables. They are derived from the imported source records
and are **not an official StatsBomb analytical product**.

| Statistic | Definition |
| --- | --- |
| Appearances | Distinct matches in which the player has at least one lineup row. |
| Matches played (team) | Distinct imported matches in which the team was home or away. |
| Passes attempted | Count of `Pass` events by the player or team. |
| Passes completed | Passes whose `pass_details.outcome_name` is `NULL`. |
| Pass completion % | `completed / attempted * 100`; `null` when nothing was attempted. |
| Shots | Count of `Shot` events by the player or team. |
| Goals | Shots whose `shot_details.outcome_name` is `Goal`. |
| xG | Sum of the non-null `statsbomb_xg` values; `0` when no shot carries one. |
| xG per shot | `xG / shots`; `null` when there are no shots. |

Limitations:

- **Appearances count squad membership, not playing time.** A substitute who never took the field
  has a lineup row and is counted. **Minutes played is deliberately not provided**: estimating it
  would need a tested substitution and tactical timeline, and an estimate would be presented as fact.
- **Shots without xG still count as shots.** They add nothing to xG but do enter the denominator of
  xG per shot, so a player with one xG-less shot has an xG per shot of `0`, not `null`.
- **Every shot counts**, whatever its period or subtype, so penalties and free kicks are included.
- **Events are attributed to the player or team on the event.** For a pass that is the passer; the
  recipient is not credited with anything.
- **Rejected records are absent.** A malformed pass or shot was never imported, so it is in no total.
  Check `validation failures` and the manifest before trusting a figure.
- **Values are unrounded floats.** Round for display.
- **Not provided:** possession, expected assists, progressive passes, pressures, or any other derived
  metric.

## API

Everything is prefixed with `/api/v1` and returns JSON. The API is read-only: no create, update, or
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
| `GET` | `/api/v1/matches/{match_id}/passes` | **V2.** Passes with their pass details. |
| `GET` | `/api/v1/matches/{match_id}/shots` | **V2.** Shots with their shot details. |
| `GET` | `/api/v1/matches/{match_id}/events/{event_id}` | **V2.** One event plus its pass or shot object. |
| `GET` | `/api/v1/players/{player_id}/stats` | **V2.** A player's season statistics. |
| `GET` | `/api/v1/teams/{team_id}/stats` | **V2.** A team's season statistics. |
| `GET` | `/api/v1/competitions/{competition_id}/seasons/{season_id}/player-stats` | **V2.** Player leaderboard. |
| `GET` | `/api/v1/competitions/{competition_id}/seasons/{season_id}/team-stats` | **V2.** Team leaderboard. |

All V1 routes and response shapes are unchanged.

### Identifiers

Every public `id` is the **stable StatsBomb external identifier**, not an internal primary key.
Match `18245`, team `220`, and competition `16` are StatsBomb IDs, and filter parameters use the
same scheme. Event ids are StatsBomb UUIDs, and a shot's `key_pass_id` is the UUID of its pass.

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

Only these filters are supported; anything else is ignored. A valid filter that matches nothing
returns an empty page, not an error.

| Endpoint | Filters |
| --- | --- |
| `/matches` | `competition_id`, `season_id`, `team_id` (home or away), `match_date` |
| `/matches/{id}/events` | `type_name`, `team_id`, `player_id`, `period` |
| `/matches/{id}/passes` | `team_id`, `player_id`, `recipient_player_id`, `outcome_name` |
| `/matches/{id}/shots` | `team_id`, `player_id`, `outcome_name` |
| `/teams`, `/players` | `q` (case-insensitive name search) |

A completed pass has no outcome, so `outcome_name` cannot select it; filter for the outcomes you
want instead (`Incomplete`, `Out`, `Pass Offside`, `Unknown`).

### Event details

`/matches/{match_id}/passes` and `/shots` return the base event plus the matching detail object:

```json
{
  "id": "7654fe57-734f-45d8-bc83-ab940cd37c45",
  "index_in_match": 2110,
  "type_name": "Shot",
  "period": 2,
  "minute": 50,
  "team": { "id": 220, "name": "Real Madrid" },
  "player": { "id": 19677, "name": "Karim Benzema" },
  "shot": {
    "key_pass_id": null,
    "statsbomb_xg": 0.62239987,
    "outcome_name": "Goal",
    "body_part_name": "Right Foot",
    "type_name": "Open Play"
  }
}
```

`/matches/{match_id}/events/{event_id}` returns the base event with **both** `pass` and `shot`
keys, only the one matching the event's type populated and the other `null`. Other event types
return both as `null`. `raw_data` is never exposed by the V2 routes (the V1 events list still
returns it, unchanged).

### Statistics endpoints

`/players/{id}/stats` and `/teams/{id}/stats` **require** `competition_id` and `season_id`; leaving
either out is a 422.

The leaderboards accept:

| Parameter | Values | Default |
| --- | --- | --- |
| `sort_by` | `xg`, `goals`, `shots`, `passes_completed`, `pass_completion_pct` | `xg` |
| `order` | `asc`, `desc` | `desc` |
| `limit`, `offset` | as above | `50`, `0` |

Ordering guarantees:

- Ties are broken by **ascending StatsBomb id** in both directions, so a page boundary never
  duplicates or drops a row.
- `null` values (such as the completion rate of a player who attempted no passes) sort **last** in
  both directions.
- A leaderboard lists every player with a lineup row or a pass or shot in that season, and every
  team that played an imported match in it.
- Statistics only include matches of the requested competition **and** season.

A valid player or team with no activity in the season gets zeros and `null` ratios rather than a
404. A 404 means the player, team, competition, or season does not exist.

### Examples

These are real responses from the 2017/18 import.

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/api/v1/matches?competition_id=16&season_id=1"
curl "http://localhost:8000/api/v1/matches/18245/shots?outcome_name=Goal"
curl "http://localhost:8000/api/v1/matches/18245/passes?outcome_name=Incomplete&limit=5"
curl "http://localhost:8000/api/v1/matches/18245/events/<event_id>"
curl "http://localhost:8000/api/v1/players?q=benzema"
curl "http://localhost:8000/api/v1/players/19677/stats?competition_id=16&season_id=1"
curl "http://localhost:8000/api/v1/competitions/16/seasons/1/player-stats?sort_by=xg&limit=3"
curl "http://localhost:8000/api/v1/competitions/16/seasons/1/team-stats"
```

A player's season line:

```json
{
  "player_id": 19677,
  "player_name": "Karim Benzema",
  "competition_id": 16,
  "season_id": 1,
  "appearances": 1,
  "passes_attempted": 39,
  "passes_completed": 36,
  "pass_completion_pct": 92.3076923076923,
  "shots": 4,
  "goals": 1,
  "xg": 0.820717093,
  "xg_per_shot": 0.20517927325
}
```

The team leaderboard for the season (the final is the only imported match):

| Team | Matches | Passes | Completed | Completion | Shots | Goals | xG | xG / shot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Real Madrid | 1 | 673 | 596 | 88.6% | 14 | 3 | 1.68 | 0.120 |
| Liverpool | 1 | 360 | 266 | 73.9% | 14 | 1 | 1.43 | 0.102 |

### Errors

| Status | Body | When |
| --- | --- | --- |
| 404 | `{"detail": "Match not found"}` | Unknown match, event, team, player, competition, or season |
| 422 | FastAPI validation detail | Malformed or missing query or path parameter |
| 500 | `{"detail": "Internal server error"}` | Unexpected failure; the cause is logged server-side only |

OpenAPI documentation is generated by FastAPI and served at `/docs`, `/redoc`, and
`/openapi.json`.

## Testing

```bash
uv run pytest                                   # unit tests, no Docker required
uv run pytest --cov=app --cov-report=term-missing
```

Unit tests run against an in-memory SQLite database created from the same models, using
deterministic fixtures in `tests/fixtures/`. They never reach the network or download the full
source dataset. The download script is tested offline through a `file://` base URL.

`tests/fixtures/v2/` holds three matches in two seasons (plus a second competition built in the
tests) with completed and incomplete passes, passes with and without recipients, shots with and
without xG, malformed details, and deliberately tied totals. Expected statistics there were
worked out by hand, so the tests check the formulas rather than echoing the implementation.

### PostgreSQL integration tests

Integration tests are marked `integration` and are skipped unless `TEST_DATABASE_URL` points at a
**scratch** PostgreSQL database. They **drop and recreate every table**, so never point them at
data you care about.

```bash
docker compose up -d db
docker compose exec db psql -U football -d postgres \
  -c "CREATE DATABASE football_test OWNER football;"

TEST_DATABASE_URL=postgresql+psycopg://football:football@localhost:5432/football_test \
  uv run pytest -m integration
```

They cover what only PostgreSQL can prove:

- the Alembic chain on a blank database: upgrade, downgrade, re-upgrade, V1 data surviving the V2
  migration, and the migrated schema equalling the schema the models create;
- JSONB column types and queries, timezone-aware timestamps, `ON DELETE CASCADE` and `SET NULL`;
- the required indexes, including that the planner can use the xG index;
- a failed import rolling back its writes while its manifest stays visible to other connections;
- the aggregate SQL: float ratios, zero division yielding `NULL`, `NULLS LAST`, and stable pagination.

`NULLS LAST` is worth the second test suite: without it PostgreSQL sorts `NULL` first on a
descending sort while SQLite sorts it first on an ascending one, so each database catches the
mistake the other misses.

## Development

```bash
uv run ruff format .     # format
uv run ruff check .      # lint
uv run mypy              # type check
```

## Project layout

```text
app/
  api/            routes, response schemas, shared dependencies and 404 lookups
  core/           configuration, exceptions, logging
  db/             declarative base, models, session management
  ingestion/      loader, validators, normalizers, persistence, service, CLI
  repositories/   read-only queries: reference data, event details, statistics
  main.py         FastAPI application factory
alembic/          migrations
scripts/          fetch_statsbomb_data.sh
tests/            api, ingestion, integration, fixtures
```

Route handlers stay thin: database queries live in `app/repositories/`, and all StatsBomb-specific
mapping lives in `app/ingestion/` — never in routes or ORM models.

## Future work

Deliberately out of scope, recorded here rather than built:

- Importing additional competitions and seasons in one run
- Minutes played, from a tested substitution and tactical timeline
- Typed detail tables for other event types (carries, duels, goalkeeper actions)
- More metrics: possession, expected assists, progressive passes, pressures
- Per-match statistics and team form
- Cursor-based pagination and richer query filters
- Caching or precomputed aggregates, should request-time computation become too slow on a larger source
- Authentication, rate limiting, and multi-tenancy
