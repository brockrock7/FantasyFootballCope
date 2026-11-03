"""Append current-week live win-probability snapshots for the dashboard."""
from __future__ import annotations

import json
import math
import os
import random
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from espn_api.football import League
from scoring_model import player_score_model, sample_player_score, player_progress, merge_scoring_history

LEAGUE_ID = 509033444
YEAR = 2026
SIMULATIONS = 5000
# ESPN projections tend to be too certain in the first snapshots of a matchup.
# Keep uncertainty wider for each player's remaining points, and retain a floor
# while a game is in progress even when the player has already exceeded projection.
PLAYER_SD_MULTIPLIER = 1.70
PLAYER_SD_FLOOR = 3.0
ROOT = Path(__file__).resolve().parent


def _remaining_models(lineup, week):
    models = []
    for player in lineup or []:
        if getattr(player, "slot_position", "") in ("BE", "IR"):
            continue
        progress = player_progress(player)
        if progress >= 100:
            continue
        projected = float(getattr(player, "projected_points", 0) or 0)
        scored = float(getattr(player, "points", 0) or 0)
        remaining = max(0.0, projected - scored)
        if remaining or (0 < progress < 100):
            active_fraction = max(0.0, min(1.0, 1.0 - progress / 100.0))
            # A player who has beaten the projection can still score during the
            # unplayed fraction. Keep that upside without a Gaussian loss tail.
            if remaining == 0 and 0 < progress < 100:
                remaining = max(projected, scored, 0.0) * active_fraction
            uncertainty_basis = max(remaining, max(projected, scored, 0.0) * active_fraction)
            sd = max(PLAYER_SD_FLOOR * math.sqrt(active_fraction),
                     PLAYER_SD_MULTIPLIER * math.sqrt(uncertainty_basis))
            models.append(player_score_model(player, week, mean=remaining, sd=sd,
                                             history_scale=active_fraction))
    return models


def _remaining_projection(lineup, week=99):
    """Analytic moments for diagnostics; odds sample the individual mixtures."""
    models = _remaining_models(lineup, week)
    return sum(model["mean"] for model in models), sum(model["variance"] for model in models)


def _probability(home_score, away_score, home_lineup, away_lineup, week=99, rng=None):
    home_models = _remaining_models(home_lineup, week)
    away_models = _remaining_models(away_lineup, week)
    current_margin = float(home_score or 0) - float(away_score or 0)
    rng = rng or random.Random()
    wins = sum(
        1.0 if (margin := current_margin
                + sum(sample_player_score(model, rng) for model in home_models)
                - sum(sample_player_score(model, rng) for model in away_models)) > 0
        else (0.5 if margin == 0 else 0.0)
        for _ in range(SIMULATIONS)
    )
    return round(100.0 * wins / SIMULATIONS, 1)


def _game_in_progress(*lineups):
    """Best-effort fallback status for older dashboard clients."""
    for lineup in lineups:
        for player in lineup or []:
            if getattr(player, "slot_position", "") in ("BE", "IR"):
                continue
            progress = player_progress(player)
            if 0 < progress < 100:
                return True
    return False


def _game_windows(*lineups, pro_schedule=None):
    """Build windows from the full NFL slate plus fantasy roster kickoff times."""
    eastern = ZoneInfo("America/New_York")
    kickoffs = set()
    for lineup in lineups:
        for player in lineup or []:
            game_date = getattr(player, "game_date", None)
            if game_date is None:
                continue
            try:
                # espn-api creates naive game_date via datetime.fromtimestamp,
                # so it represents the collector machine's local timezone.
                start = game_date.astimezone(eastern)
            except (AttributeError, OSError, OverflowError, TypeError, ValueError):
                continue
            kickoffs.add(start)

    # ESPN's weekly pro schedule covers NFL games even when no league team
    # roster contains a player from that matchup.
    for game in (pro_schedule or {}).values():
        try:
            scheduled = game[1]
            if isinstance(scheduled, (int, float)):
                scheduled = datetime.fromtimestamp(float(scheduled) / 1000.0, timezone.utc)
            kickoffs.add(scheduled.astimezone(eastern))
        except (IndexError, OSError, OverflowError, TypeError, ValueError):
            continue

    windows = set()
    sunday_dates = set()
    for kickoff in kickoffs:
        if kickoff.weekday() == 6 and kickoff.time() >= datetime_time(13, 0):
            sunday_dates.add(kickoff.date())
            continue
        end = kickoff + timedelta(hours=3)
        windows.add((int(kickoff.timestamp()), int(end.timestamp())))

    # Sunday afternoon and night games use one shared 1 p.m. to midnight block.
    for game_date in sunday_dates:
        start = datetime.combine(game_date, datetime_time(13, 0), tzinfo=eastern)
        end = datetime.combine(game_date + timedelta(days=1), datetime_time(0, 0), tzinfo=eastern)
        windows.add((int(start.timestamp()), int(end.timestamp())))
    return [list(window) for window in sorted(windows)]


def _sampling_bucket(timestamp=None, game_windows=None, active=False):
    """Return the scheduled sample slot using the user's Eastern time blocks."""
    eastern = ZoneInfo("America/Toronto")
    now = datetime.fromtimestamp(timestamp or time.time(), eastern)
    # Schedule-driven windows also cover Saturday/international games and late
    # kickoffs, which are absent from the customary Thu/Sun/Mon blocks.
    if active or any(start <= now.timestamp() <= end for start, end in game_windows or []):
        interval = 10 * 60
        return int(now.timestamp()) // interval * interval, interval
    # Fantasy sampling weeks roll over at Tuesday midnight.
    tuesday = now.date() - timedelta(days=(now.weekday() - 1) % 7)
    thursday = tuesday + timedelta(days=2)
    friday = tuesday + timedelta(days=3)
    sunday = tuesday + timedelta(days=5)
    monday = tuesday + timedelta(days=6)

    def at(day, hour):
        return datetime.combine(day, datetime_time(hour, 0), tzinfo=eastern)

    if now < at(thursday, 20):
        anchor, interval = at(tuesday, 0), 6 * 60 * 60
    elif now < at(friday, 0):
        anchor, interval = at(thursday, 20), 10 * 60
    elif now < at(sunday, 13):
        anchor, interval = at(friday, 0), 6 * 60 * 60
    elif now < at(monday, 0):
        anchor, interval = at(sunday, 13), 10 * 60
    elif now < at(monday, 20):
        anchor, interval = at(monday, 0), 6 * 60 * 60
    else:
        anchor, interval = at(monday, 20), 10 * 60

    anchor_epoch = int(anchor.timestamp())
    bucket = anchor_epoch + ((int(now.timestamp()) - anchor_epoch) // interval) * interval
    return bucket, interval


def _sampling_windows(timestamp=None):
    """Return the three higher-frequency blocks displayed behind the graph."""
    eastern = ZoneInfo("America/Toronto")
    now = datetime.fromtimestamp(timestamp or time.time(), eastern)
    tuesday = now.date() - timedelta(days=(now.weekday() - 1) % 7)
    thursday, friday = tuesday + timedelta(days=2), tuesday + timedelta(days=3)
    sunday, monday = tuesday + timedelta(days=5), tuesday + timedelta(days=6)

    def epoch(day, hour):
        return int(datetime.combine(day, datetime_time(hour, 0), tzinfo=eastern).timestamp())

    return [
        [epoch(thursday, 20), epoch(friday, 0)],
        [epoch(sunday, 13), epoch(monday, 0)],
        [epoch(monday, 20), epoch(tuesday + timedelta(days=7), 0)],
    ]


def main():
    league = League(
        league_id=LEAGUE_ID,
        year=YEAR,
        espn_s2=os.getenv("ESPN_S2"),
        swid=os.getenv("SWID"),
    )
    week = int(league.current_week)
    path = ROOT / f"live_week_{week}.json"
    matchups, values, all_lineups = [], [], []
    boxes = league.box_scores(week)
    starters = [player for box in boxes for lineup in
                (getattr(box, "home_lineup", []), getattr(box, "away_lineup", []))
                for player in lineup or [] if getattr(player, "slot_position", "") not in ("BE", "IR")]
    ids = sorted({int(getattr(player, "playerId", 0) or 0) for player in starters} - {0})
    histories = {}
    for offset in range(0, len(ids), 25):
        try:
            cards = league.player_info(playerId=ids[offset:offset + 25])
            if cards and not isinstance(cards, (tuple, list)):
                cards = [cards]
            histories.update({int(card.playerId): card.stats for card in cards or []})
        except Exception as exc:
            print(f"Could not load player scoring history; using positive lognormal prior: {exc}")
    for player in starters:
        history = histories.get(int(getattr(player, "playerId", 0) or 0), {})
        player.stats = merge_scoring_history(history, getattr(player, "stats", {}))
    for box in boxes:
        home = getattr(getattr(box, "home_team", None), "team_name", None)
        away = getattr(getattr(box, "away_team", None), "team_name", None)
        if not home or not away:
            continue
        matchups.append([home, away])
        home_lineup = getattr(box, "home_lineup", [])
        away_lineup = getattr(box, "away_lineup", [])
        all_lineups.extend((home_lineup, away_lineup))
        values.append([
            round(float(box.home_score or 0), 2),
            round(float(box.away_score or 0), 2),
            _probability(
                box.home_score, box.away_score,
                home_lineup, away_lineup,
                week=week,
            ),
            _game_in_progress(home_lineup, away_lineup),
        ])

    now = int(time.time())
    try:
        pro_schedule = league._get_pro_schedule(week)
    except Exception as exc:
        print(f"Could not load the full NFL schedule for week {week}: {exc}")
        pro_schedule = {}
    league_game_windows = _game_windows(*all_lineups, pro_schedule=pro_schedule)
    active = any(row[3] for row in values)
    sample_bucket, sample_interval = _sampling_bucket(now, league_game_windows, active)
    game_windows = [league_game_windows for _ in matchups]
    sampling_windows = _sampling_windows(now)
    snapshot = {"week": week, "matchups": matchups, "snaps": [], "game_windows": game_windows,
                "sampling_windows": sampling_windows}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("week") == week and old.get("matchups") == matchups:
                snapshot = old
        except (OSError, ValueError, AttributeError):
            pass
    snaps = snapshot.get("snaps", [])
    append_snapshot = not snaps or _sampling_bucket(int(snaps[-1][0]), league_game_windows, active)[0] != sample_bucket
    if append_snapshot:
        snaps.append([now, values])
    snapshot.update({"week": week, "matchups": matchups, "snaps": snaps[-3000:],
                     "game_windows": game_windows, "sampling_windows": sampling_windows,
                     "probability_model": "individual-lognormal-empirical-v1",
                     "sample_interval_minutes": sample_interval // 60})
    path.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
    print(f"{'Saved' if append_snapshot else 'No new sample'}; {len(snapshot['snaps'])} snapshots for week {week} (interval {sample_interval // 60} min)")


if __name__ == "__main__":
    main()
