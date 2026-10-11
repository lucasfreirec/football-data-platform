"""A deterministic, StatsBomb-shaped workload of many independent matches.

The real open data holds one Champions League match for the season, which cannot
show concurrency. This generates N matches in the same file layout, passing the
real validators, so the benchmark exercises the real import code. Given the same
arguments it always writes the same files.

Teams and players are shared between matches on purpose: workers importing
different matches then contend on the same reference rows, as they would for a
real season, instead of running trivially in isolation.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from itertools import cycle, permutations
from pathlib import Path
from typing import Any

COMPETITION_ID = 16
SEASON_ID = 1
COMPETITION_NAME = "Champions League"
SEASON_NAME = "2017/2018"
FIRST_MATCH_ID = 900_001
TEAM_COUNT = 8
SQUAD_SIZE = 14
STARTERS = 11

_NAMESPACE = uuid.UUID("5f0a6f5e-7d0a-4c43-9a8e-0d6f0f1a2b3c")


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    matches: int
    events_per_match: int = 3000
    seed: int = 20260101

    @property
    def match_ids(self) -> list[int]:
        return [FIRST_MATCH_ID + index for index in range(self.matches)]


def _team(team_id: int) -> dict[str, Any]:
    return {"id": team_id, "name": f"Team {team_id}"}


def _player_id(team_id: int, slot: int) -> int:
    return team_id * 100 + slot


def _player(team_id: int, slot: int) -> dict[str, Any]:
    player_id = _player_id(team_id, slot)
    return {"id": player_id, "name": f"Player {player_id}"}


def _pairings() -> list[tuple[int, int]]:
    return list(permutations(range(1, TEAM_COUNT + 1), 2))


def _competitions() -> list[dict[str, Any]]:
    return [
        {
            "competition_id": COMPETITION_ID,
            "season_id": SEASON_ID,
            "country_name": "Europe",
            "competition_name": COMPETITION_NAME,
            "competition_gender": "male",
            "season_name": SEASON_NAME,
        }
    ]


def _match(match_id: int, home: int, away: int, index: int) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "match_date": f"2018-{(index % 9) + 1:02d}-{(index % 27) + 1:02d}",
        "kick_off": "20:45:00.000",
        "competition": {
            "competition_id": COMPETITION_ID,
            "country_name": "Europe",
            "competition_name": COMPETITION_NAME,
        },
        "season": {"season_id": SEASON_ID, "season_name": SEASON_NAME},
        "home_team": {
            "home_team_id": home,
            "home_team_name": f"Team {home}",
            "home_team_gender": "male",
        },
        "away_team": {
            "away_team_id": away,
            "away_team_name": f"Team {away}",
            "away_team_gender": "male",
        },
        "home_score": index % 4,
        "away_score": (index + 1) % 3,
        "match_status": "available",
        "last_updated": "2020-07-29T05:00",
        "competition_stage": {"id": 10, "name": "Group Stage"},
    }


def _lineups(home: int, away: int) -> list[dict[str, Any]]:
    blocks = []
    for team in (home, away):
        squad = []
        for slot in range(1, SQUAD_SIZE + 1):
            starter = slot <= STARTERS
            squad.append(
                {
                    "player_id": _player_id(team, slot),
                    "player_name": f"Player {_player_id(team, slot)}",
                    "jersey_number": slot,
                    "country": {"id": team, "name": f"Country {team}"},
                    "cards": [],
                    "positions": [
                        {
                            "position_id": slot,
                            "position": "Center Midfield",
                            "start_reason": "Starting XI" if starter else "Substitution - On",
                        }
                    ],
                }
            )
        blocks.append({"team_id": team, "team_name": f"Team {team}", "lineup": squad})
    return blocks


def _events(
    match_id: int, home: int, away: int, count: int, rng: random.Random
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    pass_ids: dict[int, str] = {}
    possession = 1
    possession_team = home

    def event_id(index: int) -> str:
        return str(uuid.uuid5(_NAMESPACE, f"{match_id}:{index}"))

    for index in range(1, count + 1):
        if rng.random() < 0.08:
            possession += 1
            possession_team = away if possession_team == home else home
        team = possession_team
        player = _player(team, rng.randint(1, STARTERS))
        seconds = index * (5400 // max(count, 1))
        clock = f"00:{(seconds // 60) % 60:02d}:{seconds % 60:02d}.{rng.randint(0, 999):03d}"
        base: dict[str, Any] = {
            "id": event_id(index),
            "index": index,
            "period": 1 if seconds < 2700 else 2,
            "timestamp": clock,
            "minute": seconds // 60,
            "second": seconds % 60,
            "possession": possession,
            "possession_team": _team(possession_team),
            "team": _team(team),
            "player": player,
            "location": [round(rng.uniform(0, 120), 1), round(rng.uniform(0, 80), 1)],
        }
        roll = rng.random()
        if roll < 0.55:
            recipient = _player(team, rng.randint(1, STARTERS))
            detail: dict[str, Any] = {
                "recipient": recipient,
                "length": round(rng.uniform(1, 45), 2),
                "angle": round(rng.uniform(-3.14, 3.14), 2),
                "height": {"id": 1, "name": "Ground Pass"},
                "end_location": [round(rng.uniform(0, 120), 1), round(rng.uniform(0, 80), 1)],
                "body_part": {"id": 40, "name": "Right Foot"},
            }
            if rng.random() < 0.18:
                detail["outcome"] = {"id": 9, "name": "Incomplete"}
            events.append({**base, "type": {"id": 30, "name": "Pass"}, "pass": detail})
            pass_ids[index] = base["id"]
        elif roll < 0.58:
            scored = rng.random() < 0.12
            shot: dict[str, Any] = {
                "statsbomb_xg": round(rng.uniform(0.01, 0.6), 4),
                "end_location": [
                    120.0,
                    round(rng.uniform(34, 46), 1),
                    round(rng.uniform(0, 2.5), 1),
                ],
                "technique": {"id": 93, "name": "Normal"},
                "body_part": {"id": 40, "name": "Right Foot"},
                "type": {"id": 87, "name": "Open Play"},
                "outcome": {"id": 97, "name": "Goal"} if scored else {"id": 100, "name": "Saved"},
            }
            if pass_ids:
                shot["key_pass_id"] = pass_ids[max(pass_ids)]
            events.append({**base, "type": {"id": 16, "name": "Shot"}, "shot": shot})
        elif roll < 0.76:
            end = [round(rng.uniform(0, 120), 1), round(rng.uniform(0, 80), 1)]
            events.append(
                {**base, "type": {"id": 43, "name": "Carry"}, "carry": {"end_location": end}}
            )
        elif roll < 0.88:
            events.append({**base, "type": {"id": 42, "name": "Ball Receipt*"}})
        else:
            events.append({**base, "type": {"id": 17, "name": "Pressure"}, "under_pressure": True})
    return events


def write_workload(spec: WorkloadSpec, target: Path) -> list[int]:
    """Write the source files for ``spec`` under ``target`` and return the match ids."""
    target.mkdir(parents=True, exist_ok=True)
    for sub in ("events", "lineups", f"matches/{COMPETITION_ID}"):
        (target / sub).mkdir(parents=True, exist_ok=True)

    pairings = cycle(_pairings())
    matches = []
    for index, match_id in enumerate(spec.match_ids):
        home, away = next(pairings)
        matches.append(_match(match_id, home, away, index))
        rng = random.Random(f"{spec.seed}:{match_id}")
        _dump(
            target / "events" / f"{match_id}.json",
            _events(match_id, home, away, spec.events_per_match, rng),
        )
        _dump(target / "lineups" / f"{match_id}.json", _lineups(home, away))

    _dump(target / "competitions.json", _competitions())
    _dump(target / "matches" / str(COMPETITION_ID) / f"{SEASON_ID}.json", matches)
    return spec.match_ids


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
