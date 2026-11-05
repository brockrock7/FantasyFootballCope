"""Prepare auditable weekly forecasts for hypothetical roster exchanges.

Only current roster membership is used. Future mRoster responses supply player
forecasts, not future ownership. The browser optimizes lineups using these rows.
"""
from __future__ import annotations

import math
import json
from collections import Counter


SLOT_NAMES = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE",
    6: "TE", 7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL",
    12: "CB", 13: "S", 14: "DB", 15: "DP", 16: "D/ST", 17: "K",
    18: "P", 19: "HC", 20: "BE", 21: "IR", 22: "", 23: "FLEX",
    24: "ER", 25: "Rookie",
}
SLOT_ORDER = ["QB", "TQB", "RB", "RB/WR", "WR", "WR/TE", "TE", "FLEX", "OP",
              "DT", "DE", "DL", "LB", "CB", "S", "DB", "DP", "K", "P", "HC", "D/ST"]
NONSTARTERS = {"", "BE", "IR", "BENCH", "RESERVE"}
KNOWN_OUT = {"OUT", "O", "IR", "INJURY_RESERVE", "INJURED_RESERVE", "SUSPENSION",
             "SUSPENDED", "PUP", "PHYSICALLY_UNABLE_TO_PERFORM"}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _integer(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def slot_label(value):
    """Normalize ESPN numeric IDs and equivalent textual slot names."""
    numeric = _integer(value)
    if numeric in SLOT_NAMES:
        return SLOT_NAMES[numeric]
    text = str(value or "").strip().upper()
    return {"RB/WR/TE": "FLEX", "DEF": "D/ST", "DST": "D/ST",
            "SUPERFLEX": "OP", "BENCH": "BE"}.get(text, text)


def lineup_slots(counts):
    parsed = Counter()
    for raw, count in (counts or {}).items():
        slot = slot_label(raw)
        if slot not in NONSTARTERS:
            parsed[slot] += max(0, _integer(count, 0))
    order = SLOT_ORDER + sorted(set(parsed) - set(SLOT_ORDER))
    return [slot for slot in order for _ in range(parsed[slot])]


def schedule_weeks(player):
    schedule = getattr(player, "schedule", {}) or {}
    if not isinstance(schedule, dict):
        return set()
    return {week for key, game in schedule.items()
            if (week := _integer(key)) is not None and 1 <= week <= 18 and game}


def bye_week(player):
    explicit = _integer(getattr(player, "bye_week", None) or getattr(player, "byeWeek", None))
    if explicit is not None and 1 <= explicit <= 18:
        return explicit
    games = schedule_weeks(player)
    # An incomplete schedule must not manufacture a bye from its first gap.
    missing = set(range(1, 19)) - games
    return next(iter(missing)) if len(games) == 17 and len(missing) == 1 else None


def raw_weekly_projections(response, year, requested_week):
    """Read only same-season, weekly projection stats (including true zero)."""
    projections = {}
    for team in (response or {}).get("teams", []):
        roster = team.get("roster") or {}
        entries = roster.get("entries", []) if isinstance(roster, dict) else roster
        for entry in entries or []:
            player = (entry.get("playerPoolEntry") or {}).get("player") or entry.get("player") or {}
            player_id = _integer(player.get("id", entry.get("playerId")))
            if player_id is None:
                continue
            for stats in player.get("stats", []) or []:
                week = _integer(stats.get("scoringPeriodId"))
                if (_integer(stats.get("seasonId")) != int(year)
                        or _integer(stats.get("statSourceId")) != 1
                        or _integer(stats.get("statSplitTypeId")) != 1
                        or week != requested_week or not 1 <= week <= 18):
                    continue
                value = _number(stats.get("appliedTotal"))
                if value is not None:
                    projections[(player_id, week)] = value
    return projections


def _baseline(player, roster_row, current_week):
    average = _number(getattr(player, "projected_avg_points", None))
    if average is not None and average > 0:
        return average, "espn_baseline"
    stats = getattr(player, "stats", {}) or {}
    recent = sorted(((week, _number(value.get("projected_points")))
                     for key, value in stats.items() if isinstance(value, dict)
                     and (week := _integer(key)) is not None), reverse=True)
    recent = [value for week, value in recent
              if week is not None and 1 <= week <= current_week and value is not None and value > 0][:3]
    if recent:
        return sum(recent) / len(recent), "recent_projection"
    if _integer(roster_row.get("score_games"), 0) > 0:
        ppg = _number(roster_row.get("ppg"))
        if ppg is not None:
            return ppg, "historical_ppg"
    return None, "missing"


def player_forecasts(player, roster_row, weeks, current_week, projections):
    player_id = _integer(getattr(player, "playerId", None), roster_row.get("id"))
    bye = bye_week(player)
    status = str(getattr(player, "injuryStatus", "") or "").upper()
    if status == "[]":
        status = "ACTIVE"
    on_ir = slot_label(getattr(player, "lineupSlot", roster_row.get("slot"))) == "IR"
    baseline, fallback_source = _baseline(player, roster_row, current_week)
    stats = getattr(player, "stats", {}) or {}
    weekly = {}
    for week in weeks:
        if week == bye:
            weekly[str(week)] = {"points": 0.0, "source": "bye", "available": False, "reason": "NFL bye"}
            continue
        if week == current_week and (status in KNOWN_OUT or on_ir):
            weekly[str(week)] = {"points": 0.0, "source": "unavailable", "available": False,
                                 "reason": "Currently on IR" if on_ir else f"Current injury status: {status}"}
            continue
        value = projections.get((player_id, week))
        if value is None:
            row = stats.get(week, stats.get(str(week), {})) or {}
            value = _number(row.get("projected_points")) if isinstance(row, dict) else None
        source = "espn_weekly" if value is not None else fallback_source
        if value is None:
            value = baseline
        weekly[str(week)] = {"points": round(value, 2) if value is not None else None,
                             "source": source, "available": value is not None}
        if value is None:
            weekly[str(week)]["reason"] = "No projection or historical baseline; excluded from projected lineup"
        elif week > current_week and (status in KNOWN_OUT or on_ir):
            weekly[str(week)]["reason"] = "Conditional on returning; recovery date is not modeled"
    eligible = [slot_label(slot) for slot in getattr(player, "eligibleSlots", []) or []]
    eligible = list(dict.fromkeys(slot for slot in eligible if slot not in NONSTARTERS))
    return {
        "id": player_id, "name": roster_row.get("name", str(getattr(player, "name", "Unknown player"))),
        "position": slot_label(roster_row.get("position", getattr(player, "position", ""))),
        "slot": slot_label(roster_row.get("slot", getattr(player, "lineupSlot", "BE"))),
        "eligible_slots": eligible, "bye_week": bye, "injury_status": status or "UNKNOWN",
        "on_ir": on_ir, "nfl_team": roster_row.get("nfl_team", str(getattr(player, "proTeam", ""))),
        "photo_url": roster_row.get("photo_url", ""), "team_logo_url": roster_row.get("team_logo_url", ""),
        "projections": weekly,
    }


def free_agent_forecasts(league, weeks, current_week):
    """Page through currently available (not waiver-locked) players for each week."""
    from espn_api.football.player import Player
    pool, forecasts = {}, {}
    schedule = league._get_all_pro_schedule()
    owned = {getattr(p, "playerId", None) for t in league.teams for p in t.roster}
    for week in weeks:
        offset = 0
        while True:
            filters = {"players": {"filterStatus": {"value": ["FREEAGENT"]},
                       "limit": 1000, "offset": offset,
                       "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
            response = league.espn_request.league_get(
                params={"view": "kona_player_info", "scoringPeriodId": week},
                headers={"x-fantasy-filter": json.dumps(filters)})
            entries = response.get("players", [])
            forecasts.update(raw_weekly_projections({"teams": [{"roster": {"entries": entries}}]}, league.year, week))
            for entry in entries:
                pid = (entry.get("player") or {}).get("id")
                if pid is not None and pid not in owned and pid not in pool:
                    pool[pid] = Player(entry, league.year, schedule)
            offset += len(entries)
            if len(entries) < 1000:
                break
    result = [dict(player_forecasts(player, {}, weeks, current_week, forecasts), free_agent=True)
              for player in pool.values()]
    return [p for p in result if p["nfl_team"] not in ("", "None", "FA") and any((row.get("points") or 0) > 0 for row in p["projections"].values())]


def build_trade_calculator(league, current_rosters, schedule_meta):
    """Fetch each remaining league week once and retain forecast provenance."""
    current_week = max(1, _integer(getattr(league, "current_week", 1), 1))
    periods = schedule_meta.get("matchup_periods") or getattr(league.settings, "matchup_periods", {}) or {}
    season_weeks = sorted({week for values in periods.values() for value in values or []
                           if (week := _integer(value)) is not None and 1 <= week <= 18})
    if not season_weeks:
        season_weeks = list(range(1, min(18, _integer(getattr(league, "finalScoringPeriod", 17), 17)) + 1))
    weeks = [week for week in season_weeks if week >= current_week]
    counts = schedule_meta.get("lineup_slot_counts") or getattr(league.settings, "position_slot_counts", {})
    slots = lineup_slots(counts)
    projections = {}
    failed_weeks = []
    for week in weeks:
        try:
            response = league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": week})
            projections.update(raw_weekly_projections(response, league.year, week))
        except Exception as exc:
            failed_weeks.append(week)
            print(f"Trade calculator: could not fetch week {week} projections: {exc}")
    teams = {}
    sources = Counter()
    for team in league.teams:
        team_id = str(team.team_id)
        roster_rows = {row["id"]: row for row in current_rosters.get(team_id, [])}
        players = [player_forecasts(player, roster_rows.get(getattr(player, "playerId", None), {}),
                                    weeks, current_week, projections)
                   for player in getattr(team, "roster", []) or []]
        for player in players:
            sources.update(row["source"] for row in player["projections"].values())
        teams[team_id] = {"id": team_id, "name": str(team.team_name), "players": players}
    free_agents = []
    free_agent_error = ""
    try:
        free_agents = free_agent_forecasts(league, weeks, current_week)
    except Exception as exc:
        free_agent_error = "Free-agent projections unavailable; replacement comparisons are incomplete."
        print(f"Trade calculator free-agent fetch failed: {exc}")
    notes = [
        "Each side is valued by its best legal projected starting lineup in each remaining league week, including fantasy playoffs.",
        "Weekly ESPN projections are used where published. Missing weeks use ESPN projected PPG, then recent ESPN projections, then historical PPG; missing values remain unknown.",
        "NFL byes score zero. Current-week OUT/IR players are unavailable; future forecasts for injured players are conditional on returning, with no invented recovery date.",
        "This is a full-week hypothetical. Already-played games, lineup locks, trade processing, roster limits, and playoff qualification are not simulated.",
    ]
    notes.append("Empty starter slots use the best projected currently available free agent, independently for each team and week. Waiver-locked players are excluded; availability may change. Occupied roster slots are retained.")
    if free_agent_error:
        notes.append(free_agent_error)
    if failed_weeks:
        notes.append("ESPN weekly forecast requests failed for week(s) " + ", ".join(map(str, failed_weeks)) + "; labeled baselines are used where available.")
    return {"weeks": weeks, "current_week": current_week, "lineup_slots": slots, "teams": teams,
            "free_agents": free_agents, "free_agent_error": free_agent_error, "notes": notes, "projection_coverage": dict(sources), "failed_weeks": failed_weeks}
