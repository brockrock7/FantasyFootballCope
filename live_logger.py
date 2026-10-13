"""Append live ESPN matchup win-probability snapshots to a JSON file.

The probability model uses ESPN's remaining player projections and a simple
independent normal approximation. It is an estimate, not an ESPN-provided
win probability.
"""
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
    mean = 0.0
    variance = 0.0
    for player in lineup or []:
        if getattr(player, "slot_position", "") in ("BE", "IR"):
            continue
        if getattr(player, "game_played", 100) >= 100:
            continue
        projected = float(getattr(player, "projected_points", 0) or 0)
        scored = float(getattr(player, "points", 0) or 0)
        remaining = max(0.0, projected - scored)
        if remaining <= 0:
            continue
        mean += remaining
        # An approximate player-level standard deviation, with a floor for
        # kickers/defenses and low-projection players.
        sd = max(1.5, 0.65 * math.sqrt(remaining))
        variance += sd * sd
    return mean, variance


def _home_probability(home_score, away_score, home_lineup, away_lineup):
    home_mean, home_var = _remaining_projection(home_lineup)
    away_mean, away_var = _remaining_projection(away_lineup)
    mean = (float(home_score) + home_mean) - (float(away_score) + away_mean)
    sd = math.sqrt(max(home_var + away_var, 0.01))
    rng = random.Random()
    wins = 0.0
    for _ in range(SIMULATIONS):
        result = mean + rng.gauss(0.0, sd)
        wins += 1.0 if result > 0 else (0.5 if result == 0 else 0.0)
    return 100.0 * wins / SIMULATIONS


def main():
    league = League(league_id=LEAGUE_ID, year=YEAR)
    week = int(league.current_week)
    path = ROOT / f"live_week_{week}.json"
    matchups = []
    values = []
    for box in league.box_scores(week):
        home = getattr(getattr(box, "home_team", None), "team_name", None)
        away = getattr(getattr(box, "away_team", None), "team_name", None)
        if not home or not away:
            continue
        matchups.append([home, away])
        values.append([
            round(float(box.home_score or 0), 2),
            round(float(box.away_score or 0), 2),
            round(_home_probability(
                box.home_score or 0, box.away_score or 0,
                getattr(box, "home_lineup", []), getattr(box, "away_lineup", []),
            ), 1),
        ])

    now = int(time.time())
    old = {"week": week, "matchups": matchups, "snaps": []}
    if path.exists():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if candidate.get("week") == week and candidate.get("matchups") == matchups:
                old = candidate
        except (OSError, ValueError, AttributeError):
            pass

    snaps = old.get("snaps", [])
    # Avoid duplicate points if a workflow is manually retried in the same minute.
    if not snaps or now - int(snaps[-1][0]) >= 60:
        snaps.append([now, values])
    # Keep a bounded file while preserving a useful graph across the week.
    old.update({"week": week, "matchups": matchups, "snaps": snaps[-3000:]})
    path.write_text(json.dumps(old, separators=(",", ":")), encoding="utf-8")
    print(f"Saved {len(old['snaps'])} live snapshots for week {week} to {path.name}")


if __name__ == "__main__":
    main()
