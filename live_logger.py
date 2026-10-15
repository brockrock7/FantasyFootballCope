"""Append current-week live win-probability snapshots for the dashboard."""
from __future__ import annotations

import json
import math
import os
import random
import time
from pathlib import Path

from espn_api.football import League

LEAGUE_ID = 509033444
YEAR = 2026
SIMULATIONS = 5000
ROOT = Path(__file__).resolve().parent


def _remaining_projection(lineup):
    mean = variance = 0.0
    for player in lineup or []:
        if getattr(player, "slot_position", "") in ("BE", "IR"):
            continue
        if getattr(player, "game_played", 100) >= 100:
            continue
        projected = float(getattr(player, "projected_points", 0) or 0)
        scored = float(getattr(player, "points", 0) or 0)
        remaining = max(0.0, projected - scored)
        if remaining:
            mean += remaining
            sd = max(1.5, 0.65 * math.sqrt(remaining))
            variance += sd * sd
    return mean, variance


def _probability(home_score, away_score, home_lineup, away_lineup):
    home_mean, home_var = _remaining_projection(home_lineup)
    away_mean, away_var = _remaining_projection(away_lineup)
    expected_margin = float(home_score or 0) + home_mean - float(away_score or 0) - away_mean
    sd = math.sqrt(max(home_var + away_var, 0.01))
    rng = random.Random()
    wins = sum(
        1.0 if (margin := expected_margin + rng.gauss(0.0, sd)) > 0
        else (0.5 if margin == 0 else 0.0)
        for _ in range(SIMULATIONS)
    )
    return round(100.0 * wins / SIMULATIONS, 1)


def main():
    league = League(
        league_id=LEAGUE_ID,
        year=YEAR,
        espn_s2=os.getenv("ESPN_S2"),
        swid=os.getenv("SWID"),
    )
    week = int(league.current_week)
    path = ROOT / f"live_week_{week}.json"
    matchups, values = [], []
    for box in league.box_scores(week):
        home = getattr(getattr(box, "home_team", None), "team_name", None)
        away = getattr(getattr(box, "away_team", None), "team_name", None)
        if not home or not away:
            continue
        matchups.append([home, away])
        values.append([
            round(float(box.home_score or 0), 2),
            round(float(box.away_score or 0), 2),
            _probability(
                box.home_score, box.away_score,
                getattr(box, "home_lineup", []), getattr(box, "away_lineup", []),
            ),
        ])

    now = int(time.time())
    snapshot = {"week": week, "matchups": matchups, "snaps": []}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("week") == week and old.get("matchups") == matchups:
                snapshot = old
        except (OSError, ValueError, AttributeError):
            pass
    snaps = snapshot.get("snaps", [])
    if not snaps or now - int(snaps[-1][0]) >= 60:
        snaps.append([now, values])
    snapshot.update({"week": week, "matchups": matchups, "snaps": snaps[-3000:]})
    path.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
    print(f"Saved {len(snapshot['snaps'])} snapshots for week {week}")


if __name__ == "__main__":
    main()
