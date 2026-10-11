# Worker-count benchmark

Recorded 2026-10-11T09:21:09+00:00 at commit `1fd68ed`.

## Workload

- 20 independent `ingest_match` jobs per run, one match each
- 3000 events per match (about 60,000 events in total), generated deterministically with seed 20260101
- matches share 8 teams and their players, so workers contend on shared reference rows
- each worker count is run 3 time(s); the median-elapsed run is shown
- a fresh worker pool is started per run, warmed up on separate matches, and the data wiped before timing

## Results

| Workers | Elapsed (s) | Jobs/min | Speedup | Efficiency | Job p50 (s) | Job p95 (s) | Peak running | Worker processes used | Failed |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 14.6 | 82.4 | 1.00x | 100% | 0.72 | 0.74 | 1 | 1 | 0 |
| 2 | 8.4 | 143.1 | 1.74x | 87% | 0.77 | 0.83 | 2 | 2 | 0 |
| 4 | 6.5 | 185.0 | 2.24x | 56% | 1.16 | 1.69 | 4 | 4 | 0 |

*Speedup* is elapsed time relative to one worker; *efficiency* is speedup divided by worker count (100% would be perfectly linear). *Peak running* is the most jobs seen in the `running` state at once, and *worker processes used* is how many distinct processes executed jobs, which together show that extra slots changed behaviour rather than only a setting.

Data integrity after every run (matches and events stored equal what the completed jobs should have written): **consistent**.

## Environment

- macOS-27.0.1-arm64-arm-64bit, arm64, 10 CPUs
- Python 3.12.13, PostgreSQL 16.15, Redis 7.4.11

## Caveats

- Everything runs on one machine, so the worker pool, PostgreSQL, Redis, and this driver compete for the same CPUs; PostgreSQL ran in a Docker VM on a laptop.
- The workload is synthetic and CPU-and-database mixed. Real StatsBomb matches or other hardware will scale differently, so treat these as an illustration of this setup, not a universal gain.
- Throughput stops improving once the CPUs, or the database's write capacity, saturate; efficiency below 100% is expected.
- With few jobs per run the last job to start sets a floor on elapsed time, which penalises higher worker counts a little.
- Treat differences of a few percent as noise.

## Reproduce

```bash
docker compose up -d db broker
uv run python -m app.benchmarks.workers --worker-counts 1,2,4 --jobs-per-run 20 --events-per-match 3000 --repeats 3
```
