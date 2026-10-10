from pathlib import Path

from app.ingestion.loader import SourceLoader


def test_v2_fixture_set_is_complete(v2_source_dir: Path) -> None:
    loader = SourceLoader(v2_source_dir)
    loader.ensure_available()

    assert [m["match_id"] for m in loader.load_matches(16, 1)] == [9001, 9002]
    assert [m["match_id"] for m in loader.load_matches(16, 2)] == [9003]
    for match_id in (9001, 9002, 9003):
        assert loader.has_events(match_id)
        assert loader.has_lineups(match_id)


def test_v2_fixtures_cover_the_required_pass_and_shot_cases(v2_source_dir: Path) -> None:
    loader = SourceLoader(v2_source_dir)
    events = loader.load_events(9001) + loader.load_events(9002)
    passes = [e["pass"] for e in events if e["type"]["name"] == "Pass"]
    shots = [e["shot"] for e in events if e["type"]["name"] == "Shot"]

    assert any("outcome" not in p for p in passes), "needs a completed pass"
    assert any("outcome" in p for p in passes), "needs an incomplete pass"
    assert any("recipient" in p for p in passes), "needs a pass with a recipient"
    assert any("recipient" not in p for p in passes), "needs a pass without a recipient"
    assert any(s.get("statsbomb_xg") for s in shots), "needs a shot with xG"
    assert any("statsbomb_xg" not in s for s in shots), "needs a shot without xG"
    assert any(s.get("outcome", {}).get("name") == "Goal" for s in shots), "needs a goal"
