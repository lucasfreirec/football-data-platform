# Football Data Platform

Imports [StatsBomb Open Data](https://github.com/statsbomb/open-data) for the **UEFA Champions
League 2017/18** season into PostgreSQL and exposes it through a read-only FastAPI REST API.

> Full setup, ingestion, and API documentation is added in a later implementation step.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker (for local PostgreSQL)

## Quick start

```bash
uv sync --all-groups
cp .env.example .env
uv run ruff check .
uv run pytest
```
