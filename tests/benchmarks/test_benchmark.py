import json
from pathlib import Path

import pytest

from app.benchmarks.workers import (
    BenchmarkResults,
    RunResult,
    parse_worker_counts,
    percentile,
    render_report,
    require_benchmark_database,
    summarise,
)
from app.benchmarks.workload import FIRST_MATCH_ID, WorkloadSpec, write_workload
from app.core.exceptions import ValidationError
from app.ingestion.loader import SourceLoader
from app.ingestion.normalizers import normalize_event, normalize_lineup_team, normalize_match


def run(workers: int, elapsed: float, repeat: int = 1, **overrides: object) -> RunResult:
    values: dict[str, object] = {
        "workers": workers,
        "repeat": repeat,
        "jobs": 20,
        "elapsed_seconds": elapsed,
        "completed": 20,
        "failed": 0,
        "throughput_jobs_per_minute": round(20 / elapsed * 60, 2),
        "duration_p50": 0.7,
        "duration_p90": 0.8,
        "duration_p95": 0.9,
        "duration_max": 1.0,
        "peak_running": workers,
        "distinct_worker_processes": workers,
        "retries": 0,
        "events_stored": 60000,
        "events_expected": 60000,
        "data_consistent": True,
    }
    values.update(overrides)
    return RunResult(**values)  # type: ignore[arg-type]


class TestWorkload:
    def test_it_is_deterministic(self, tmp_path: Path) -> None:
        spec = WorkloadSpec(matches=3, events_per_match=200)
        write_workload(spec, tmp_path / "a")
        write_workload(spec, tmp_path / "b")

        files = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*.json"))
        assert files and all(
            (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes() for f in files
        )

    def test_a_different_seed_gives_different_events(self, tmp_path: Path) -> None:
        write_workload(WorkloadSpec(matches=1, events_per_match=200, seed=1), tmp_path / "a")
        write_workload(WorkloadSpec(matches=1, events_per_match=200, seed=2), tmp_path / "b")
        name = f"events/{FIRST_MATCH_ID}.json"

        assert (tmp_path / "a" / name).read_bytes() != (tmp_path / "b" / name).read_bytes()

    def test_it_passes_the_real_validators(self, tmp_path: Path) -> None:
        spec = WorkloadSpec(matches=4, events_per_match=400)
        ids = write_workload(spec, tmp_path)
        loader = SourceLoader(tmp_path)

        for raw in loader.load_matches(16, 1):
            normalize_match(raw)
        for match_id in ids:
            for block in loader.load_lineups(match_id):
                normalize_lineup_team(block)
            for raw in loader.load_events(match_id):
                try:
                    normalize_event(raw)
                except ValidationError as exc:
                    pytest.fail(f"match {match_id} produced an invalid event: {exc}")

    def test_it_has_the_requested_shape(self, tmp_path: Path) -> None:
        ids = write_workload(WorkloadSpec(matches=5, events_per_match=300), tmp_path)
        loader = SourceLoader(tmp_path)

        assert ids == [FIRST_MATCH_ID + n for n in range(5)]
        assert len(loader.load_matches(16, 1)) == 5
        assert all(len(loader.load_events(m)) == 300 for m in ids)
        assert all(loader.has_lineups(m) for m in ids)

    def test_event_ids_and_indexes_are_unique_across_matches(self, tmp_path: Path) -> None:
        ids = write_workload(WorkloadSpec(matches=4, events_per_match=300), tmp_path)
        loader = SourceLoader(tmp_path)
        everything = [e for m in ids for e in loader.load_events(m)]

        assert len({e["id"] for e in everything}) == len(everything)
        for match_id in ids:
            indexes = [e["index"] for e in loader.load_events(match_id)]
            assert indexes == list(range(1, 301))

    def test_matches_share_teams_so_workers_contend(self, tmp_path: Path) -> None:
        ids = write_workload(WorkloadSpec(matches=6, events_per_match=50), tmp_path)
        loader = SourceLoader(tmp_path)
        teams = [
            (m["home_team"]["home_team_id"], m["away_team"]["away_team_id"])
            for m in loader.load_matches(16, 1)
        ]

        assert len(ids) == 6
        assert len({team for pair in teams for team in pair}) < 2 * len(teams)

    def test_it_contains_the_event_types_the_import_cares_about(self, tmp_path: Path) -> None:
        ids = write_workload(WorkloadSpec(matches=1, events_per_match=2000), tmp_path)
        kinds = {e["type"]["name"] for e in SourceLoader(tmp_path).load_events(ids[0])}

        assert {"Pass", "Shot", "Carry"} <= kinds

    def test_a_shot_outcome_id_matches_its_name(self, tmp_path: Path) -> None:
        ids = write_workload(WorkloadSpec(matches=1, events_per_match=3000), tmp_path)
        shots = [
            e["shot"]["outcome"]
            for e in SourceLoader(tmp_path).load_events(ids[0])
            if e["type"]["name"] == "Shot"
        ]

        assert shots
        assert all((o["id"], o["name"]) in {(97, "Goal"), (100, "Saved")} for o in shots)


class TestMeasurement:
    @pytest.mark.parametrize(
        ("values", "fraction", "expected"),
        [
            ([], 0.5, 0.0),
            ([4.0], 0.95, 4.0),
            ([1.0, 2.0, 3.0, 4.0, 5.0], 0.0, 1.0),
            ([1.0, 2.0, 3.0, 4.0, 5.0], 0.5, 3.0),
            ([1.0, 2.0, 3.0, 4.0, 5.0], 1.0, 5.0),
            ([5.0, 1.0, 3.0, 2.0, 4.0], 0.5, 3.0),
            ([1.0, 2.0], 0.5, 1.5),
            ([10.0, 20.0, 30.0, 40.0], 0.9, 37.0),
        ],
    )
    def test_percentile(self, values: list[float], fraction: float, expected: float) -> None:
        assert percentile(values, fraction) == pytest.approx(expected)

    def test_the_median_run_is_chosen_per_worker_count(self) -> None:
        runs = [
            run(1, 15.0, 1),
            run(1, 14.0, 2),
            run(1, 16.0, 3),
            run(2, 9.0, 1),
            run(2, 8.0, 2),
            run(2, 8.5, 3),
        ]

        chosen = summarise(runs)

        assert [(r.workers, r.elapsed_seconds) for r in chosen] == [(1, 15.0), (2, 8.5)]

    def test_summary_is_ordered_by_worker_count(self) -> None:
        chosen = summarise([run(4, 6.0), run(1, 14.0), run(2, 8.0)])

        assert [r.workers for r in chosen] == [1, 2, 4]


class TestReport:
    @pytest.fixture
    def results(self) -> BenchmarkResults:
        return BenchmarkResults(
            parameters={
                "worker_counts": [1, 2, 4],
                "jobs_per_run": 20,
                "events_per_match": 3000,
                "repeats": 1,
                "warmup_jobs": 8,
                "seed": 7,
            },
            environment={"cpu_count": 10, "platform": "test-os", "git_commit": "abc1234"},
            runs=[run(1, 14.0), run(2, 8.0), run(4, 6.0)],
        )

    def test_it_compares_every_worker_count(self, results: BenchmarkResults) -> None:
        report = render_report(results)

        for row in ("| 1 | 14.0 |", "| 2 | 8.0 |", "| 4 | 6.0 |"):
            assert row in report

    def test_speedup_and_efficiency_are_relative_to_one_worker(
        self, results: BenchmarkResults
    ) -> None:
        report = render_report(results)

        assert "1.00x" in report and "1.75x" in report and "2.33x" in report
        assert "88%" in report  # 1.75 / 2
        assert "58%" in report  # 2.33 / 4

    def test_it_states_its_caveats_and_how_to_reproduce(self, results: BenchmarkResults) -> None:
        report = render_report(results)

        assert "## Caveats" in report
        assert "not a universal gain" in report
        assert "--worker-counts 1,2,4" in report

    def test_it_flags_inconsistent_data(self, results: BenchmarkResults) -> None:
        results.runs.append(run(2, 8.0, 2, data_consistent=False))

        assert "INCONSISTENT" in render_report(results)

    def test_it_reports_concurrency_evidence(self, results: BenchmarkResults) -> None:
        report = render_report(results)

        assert "Peak running" in report and "Worker processes used" in report


class TestSafetyAndArguments:
    @pytest.mark.parametrize(
        "url",
        [
            "postgresql+psycopg://u:p@localhost/football_data",
            "postgresql+psycopg://u:p@localhost/football_test",
            "postgresql+psycopg://u:p@localhost/postgres",
            "postgresql+psycopg://u:p@localhost/benchmark_football",
        ],
    )
    def test_it_refuses_a_database_that_is_not_a_benchmark_database(self, url: str) -> None:
        with pytest.raises(SystemExit, match="Refusing to run"):
            require_benchmark_database(url)

    def test_it_accepts_a_benchmark_database(self) -> None:
        require_benchmark_database("postgresql+psycopg://u:p@localhost/football_benchmark")

    @pytest.mark.parametrize(
        ("text", "expected"), [("1,2,4", [1, 2, 4]), ("3", [3]), (" 1, 2 ", [1, 2])]
    )
    def test_worker_counts_are_parsed(self, text: str, expected: list[int]) -> None:
        assert parse_worker_counts(text) == expected

    @pytest.mark.parametrize("text", ["", "0", "-1,2", "a,b", "1.5"])
    def test_bad_worker_counts_are_rejected(self, text: str) -> None:
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            parse_worker_counts(text)


def test_the_results_file_round_trips_as_json(tmp_path: Path) -> None:
    from dataclasses import asdict

    payload = {"runs": [asdict(run(2, 8.0))]}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(payload))

    assert json.loads(path.read_text())["runs"][0]["workers"] == 2
