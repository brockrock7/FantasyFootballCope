"""Build a four-tab ESPN fantasy football dashboard for GitHub Pages.

Run with: python expected_wins.py

League scores and roster data are public. ESPN_S2 and SWID environment
variables are optional for the dashboard, but ESPN's transaction history may
require them for complete trade records. Never put cookie values in this file.
"""
from __future__ import annotations

import json
import math
import os
import importlib
import random
from collections import Counter
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from espn_api.football import League

LEAGUE_ID = 509033444
YEAR = 2026
MULTIVERSE_SIMULATIONS = 20000
ROOT = Path(__file__).resolve().parent
GROUPS = ["QB", "RB", "WR", "TE", "K", "DEF"]
POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "D/ST": "DEF"}
SOURCES = ["Drafted", "Traded", "Waivers"]
SOURCE_MAP = {
    "DRAFT": "Drafted", "DRAFTED": "Drafted",
    "TRADE": "Traded", "TRADED": "Traded",
    "WAIVER": "Waivers", "WAIVERS": "Waivers", "WAIVER_WIRE": "Waivers",
    "WAIVERS_TRADITIONAL": "Waivers", "WAIVERS_ROLLING": "Waivers",
    "WAIVERS_FAAB": "Waivers", "FREE_AGENT": "Waivers",
    "FREEAGENT": "Waivers", "FA": "Waivers",
}


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _source_bucket(value):
    label = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return SOURCE_MAP.get(label, "Unknown")


def _week_acquisition_sources(league, week):
    """Read historical roster acquisition labels directly from ESPN."""
    by_team_and_player = {}
    by_player = {}
    data = league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": week})
    for team in data.get("teams", []) if isinstance(data, dict) else []:
        team_id = str(team.get("id", ""))
        roster = team.get("roster") or {}
        entries = roster.get("entries", []) if isinstance(roster, dict) else roster
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            pool = entry.get("playerPoolEntry") or {}
            player = pool.get("player") or {}
            player_id = entry.get("playerId") or pool.get("id") or player.get("id")
            if player_id is None:
                continue
            source = _source_bucket(entry.get("acquisitionType") or pool.get("acquisitionType") or player.get("acquisitionType"))
            if source == "Unknown":
                pending = [entry]
                while pending and source == "Unknown":
                    value = pending.pop()
                    if isinstance(value, dict):
                        for key, nested in value.items():
                            if str(key).lower() in {"acquisitiontype", "acquisitionsource"}:
                                source = _source_bucket(nested)
                                if source != "Unknown":
                                    break
                            if isinstance(nested, (dict, list)):
                                pending.append(nested)
                    elif isinstance(value, list):
                        pending.extend(value)
            if source != "Unknown":
                player_id = int(player_id)
                by_team_and_player[(team_id, player_id)] = source
                by_player[player_id] = source
    return by_team_and_player, by_player


def _drafted_player_teams(league):
    """Read draft ownership from ESPN's mDraftDetail endpoint."""
    drafted = set()
    try:
        data = league.espn_request.league_get(params={"view": "mDraftDetail"})
        if isinstance(data, list):
            data = data[0] if data else {}
        detail = data.get("draftDetail", {}) if isinstance(data, dict) else {}
        for pick in detail.get("picks", []) or []:
            try:
                player_id, team_id = int(pick.get("playerId", -1)), str(pick.get("teamId", ""))
            except (TypeError, ValueError):
                continue
            if player_id > 0 and team_id:
                drafted.add((team_id, player_id))
    except Exception as exc:
        print(f"Could not read ESPN draft details; untagged non-trade adds will use the waiver fallback: {exc}")
    return drafted


def _round_robin_rounds(team_count, rng):
    order = list(range(team_count))
    rng.shuffle(order)
    if team_count % 2:
        order.append(None)
    rounds = []
    for _ in range(len(order) - 1):
        rounds.append([(order[i], order[-1 - i]) for i in range(len(order) // 2)
                       if order[i] is not None and order[-1 - i] is not None])
        order = [order[0], order[-1], *order[1:-1]]
    return rounds


def _multiverse_rank(metrics, h2h_points, h2h_games, division_ids, tie_rule, rng):
    if tie_rule == "TOTAL_POINTS_SCORED":
        hierarchy = ("win_pct", "points_for", "head_to_head", "division_pct", "points_against", "coin")
    elif tie_rule == "INTRA_DIVISION_RECORD":
        hierarchy = ("division_pct", "head_to_head", "win_pct", "points_for", "points_against", "coin")
    else:
        hierarchy = ("win_pct", "head_to_head", "points_for", "division_pct", "points_against", "coin")

    def sort_group(indices, level=0):
        if len(indices) < 2 or level >= len(hierarchy):
            return indices
        criterion = hierarchy[level]
        if criterion == "head_to_head":
            played = [h2h_games[a][b] for a in indices for b in indices if a != b]
            valid = len(set(played)) <= 1
            values = {i: sum(h2h_points[i][j] for j in indices if j != i) if valid else 0.0 for i in indices}
        elif criterion == "coin":
            values = {i: rng.random() for i in indices}
        else:
            values = {i: metrics[i].get(criterion, 0.0) for i in indices}
        buckets = defaultdict(list)
        for i in indices:
            buckets[values[i]].append(i)
        result = []
        for value in sorted(buckets, reverse=True):
            result.extend(sort_group(buckets[value], level + 1))
        return result

    winners = []
    for division in division_ids:
        members = [i for i, row in enumerate(metrics) if row["division_id"] == division]
        if members:
            winners.append(sort_group(members)[0])
    selected = set(winners)
    winners.extend(sort_group([i for i in range(len(metrics)) if i not in selected]))
    return winners if winners else sort_group(list(range(len(metrics))))


def _simulate_multiverse(league, labels, teams, score_rows, completed_week,
                         regular_season_count, playoff_team_count, postseason_rows, actual_wins):
    """Sample valid round-robin schedules and aggregate standings and playoff outcomes."""
    team_count = len(labels)
    rng = random.Random((LEAGUE_ID * 1000003) ^ (YEAR * 9176) ^ completed_week)
    score_by_week = defaultdict(dict)
    for row in score_rows:
        if row["week"] <= completed_week:
            score_by_week[int(row["week"])][row["team"]] = float(row["score"])
    postseason_by_week = defaultdict(dict)
    for row in postseason_rows:
        postseason_by_week[int(row["week"])][row["team"]] = float(row["score"])
    complete_weeks = [week for week in range(1, completed_week + 1)
                      if all(team in score_by_week[week] for team in labels)]
    round_count = team_count if team_count % 2 else team_count - 1
    if team_count < 2:
        return {"simulations": 0, "weeks_used": [], "actual_wins": actual_wins,
                "distributions": {}, "first_place_counts": {},
                "playoff": {"semifinals_complete": False, "finals_complete": False,
                            "semifinal_matchups": {}, "final_matchups": {}, "champions": {}}}
    repeats = max(0, regular_season_count - round_count)
    division_by_name = {str(team.team_name): str(getattr(team, "division_id", None)) for team in teams}
    division_ids = [str(key) for key in getattr(league.settings, "division_map", {}).keys()]
    tie_rule = str(getattr(league.settings, "playoff_seed_tie_rule", "H2H_RECORD") or "H2H_RECORD")
    distributions = {name: Counter() for name in labels}
    first_counts, semi_counts, final_counts, champion_counts = Counter(), Counter(), Counter(), Counter()
    samples = 0
    rounds_count = math.ceil(math.log2(playoff_team_count)) if playoff_team_count > 1 else 0
    period_length = max(1, int(getattr(league.settings, "playoff_matchup_period_length", 1) or 1))
    playoff_start = regular_season_count + 1
    complete_postseason_weeks = {week for week, scores in postseason_by_week.items()
                                 if all(team in scores for team in labels)}

    for _ in range(MULTIVERSE_SIMULATIONS):
        # Generate a fresh round-robin draw for every sample. Reusing one draw
        # would make all 20,000 results identical and collapse the distributions.
        base_rounds = _round_robin_rounds(team_count, rng)
        schedule = base_rounds + [base_rounds[i % len(base_rounds)] for i in range(repeats)]
        wins, ties, games = [0] * team_count, [0] * team_count, [0] * team_count
        points_against = [0.0] * team_count
        div_wins, div_games = [0.0] * team_count, [0] * team_count
        h2h_points = [[0.0] * team_count for _ in labels]
        h2h_games = [[0] * team_count for _ in labels]
        for week in complete_weeks:
            if week > len(schedule):
                continue
            for left, right in schedule[week - 1]:
                left_score, right_score = score_by_week[week][labels[left]], score_by_week[week][labels[right]]
                games[left] += 1; games[right] += 1
                points_against[left] += right_score; points_against[right] += left_score
                if left_score > right_score:
                    wins[left] += 1; h2h_points[left][right] += 1
                elif right_score > left_score:
                    wins[right] += 1; h2h_points[right][left] += 1
                else:
                    ties[left] += 1; ties[right] += 1
                    h2h_points[left][right] += .5; h2h_points[right][left] += .5
                h2h_games[left][right] += 1; h2h_games[right][left] += 1
                if division_by_name[labels[left]] == division_by_name[labels[right]]:
                    div_games[left] += 1; div_games[right] += 1
                    div_wins[left] += 1 if left_score > right_score else (.5 if left_score == right_score else 0)
                    div_wins[right] += 1 if right_score > left_score else (.5 if right_score == left_score else 0)
        metrics = []
        for i, name in enumerate(labels):
            points_for = sum(score_by_week[w][name] for w in complete_weeks)
            metrics.append({"win_pct": (wins[i] + .5 * ties[i]) / games[i] if games[i] else 0,
                            "points_for": points_for, "points_against": points_against[i],
                            "division_pct": div_wins[i] / div_games[i] if div_games[i] else 0,
                            "division_id": division_by_name[name]})
            distributions[name][wins[i]] += 1
        samples += 1
        ranking = _multiverse_rank(metrics, h2h_points, h2h_games, division_ids, tie_rule, rng)
        first_counts[labels[ranking[0]]] += 1

        if rounds_count < 1:
            continue
        seed_order = [1, 2]
        for round_size in (2 ** power for power in range(2, rounds_count + 1)):
            seed_order = [seed for current in seed_order for seed in (current, round_size + 1 - current)]
        nodes = [ranking[seed - 1] if seed <= playoff_team_count else None for seed in seed_order]
        semifinal_round = max(0, rounds_count - 2)
        round_index = 0
        while len(nodes) > 1:
            games_this_round = [(nodes[i], nodes[i + 1]) for i in range(0, len(nodes), 2)]
            period_weeks = list(range(playoff_start + round_index * period_length,
                                      playoff_start + (round_index + 1) * period_length))
            if not all(week in complete_postseason_weeks for week in period_weeks):
                break
            if round_index == semifinal_round:
                for left, right in games_this_round:
                    if left is not None and right is not None:
                        semi_counts[" vs ".join(sorted((labels[left], labels[right])))] += 1
            winners = []
            for left, right in games_this_round:
                if left is None or right is None:
                    winners.append(left if right is None else right)
                    continue
                left_points = sum(postseason_by_week[week][labels[left]] for week in period_weeks)
                right_points = sum(postseason_by_week[week][labels[right]] for week in period_weeks)
                if left_points == right_points:
                    winner = left if ranking.index(left) < ranking.index(right) else right
                else:
                    winner = left if left_points > right_points else right
                winners.append(winner)
            if round_index == semifinal_round and len(winners) >= 2:
                final_counts[" vs ".join(sorted((labels[winners[0]], labels[winners[1]])))] += 1
            if round_index == rounds_count - 1 and winners:
                champion_counts[labels[winners[0]]] += 1
            nodes = winners
            round_index += 1

    dist = {name: [{"wins": wins, "count": count, "probability": count / max(samples, 1)}
                   for wins, count in sorted(distributions[name].items())] for name in labels}
    return {"simulations": samples, "weeks_used": complete_weeks, "actual_wins": actual_wins,
            "distributions": dist, "first_place_counts": dict(first_counts),
            "playoff": {"semifinals_complete": bool(semi_counts), "finals_complete": bool(champion_counts),
                        "semifinal_matchups": dict(semi_counts), "final_matchups": dict(final_counts),
                        "champions": dict(champion_counts)}}


def _trade_rating(points_per_week):
    """Assign a consistent five-level rating from net points per week."""
    if points_per_week is None:
        return "Awaiting points"
    if points_per_week >= 5:
        return "GM of the Year"
    if points_per_week >= 1:
        return "Won the Trade"
    if points_per_week > -1:
        return "Even Trade"
    if points_per_week > -5:
        return "Lost the Trade"
    return "Fleeced"


def _bucket(position, slot):
    if slot in ("BE", "IR"):
        return None
    return POSITIONS.get(position)


def _team_ids(box):
    return (
        getattr(getattr(box, "home_team", None), "team_id", None),
        getattr(getattr(box, "away_team", None), "team_id", None),
    )


def _raw_trade_transactions(league, week):
    """Fetch trade rows without espn-api's strict required-status conversion.

    Some ESPN mTransactions2 rows omit ``status``. The espn-api Transaction
    constructor indexes that field directly, which can otherwise discard an
    entire scoring period. Normalize only accepted trade rows with a usable
    completion date, then let espn-api fill missing player legs where supported.
    """
    params = {"view": "mTransactions2", "scoringPeriodId": week}
    filters = {"transactions": {"filterType": {"value": ["TRADE_ACCEPT"]}}}
    data = league.espn_request.league_get(
        params=params,
        headers={"x-fantasy-filter": json.dumps(filters)},
    )
    rows = data.get("transactions") or []
    module = importlib.import_module(type(league).__module__)
    transaction_class = getattr(module, "Transaction")
    player_class = getattr(module, "Player")
    transactions = []
    malformed_rows = 0
    trade_dates = []
    found_trade = False

    for raw in rows:
        if not isinstance(raw, dict) or str(raw.get("type", "")).upper() != "TRADE_ACCEPT":
            continue
        found_trade = True
        raw_date = raw.get("processDate") or raw.get("acceptedDate") or raw.get("proposedDate")
        if raw_date:
            try:
                trade_dates.append(int(raw_date))
            except (TypeError, ValueError):
                pass
        normalized = dict(raw)
        # Missing status is not proof that a proposed trade was completed.
        # Keep its timestamp for matching, but do not turn it into a trade.
        if str(normalized.get("status", "")).upper() != "EXECUTED":
            malformed_rows += 1
            continue
        try:
            transactions.append(transaction_class(
                normalized, league.player_map, league.get_team_data
            ))
        except (KeyError, TypeError, ValueError):
            malformed_rows += 1

    # ESPN sometimes omits trade items except for one participant. Use the
    # library's item-filling helper when this installed version provides it.
    fill_items = getattr(league, "_fill_trade_accept_from_player_cards", None)
    if transactions and callable(fill_items):
        fill_items(transactions, week, {"TRADE_ACCEPT"}, True, None, "week", player_class)
    return transactions, malformed_rows, trade_dates, found_trade


def _activity_trade_rows(league, date_weeks):
    """Rebuild team-to-team movements from ESPN's league activity feed.

    mTransactions2 can omit the player legs for deals between other managers.
    The league activity feed emits matching TRADE_SENT and TRADE_RECEIVED
    events, so pair those by player and team to recover the full trade.
    """
    activities = []
    offset = 0
    page_size = 25
    seen_pages = set()
    for _ in range(40):  # cap backfill at 1,000 activity topics
        page = league.recent_activity(size=page_size, msg_type="TRADED", offset=offset) or []
        if not page:
            break
        signature = tuple((int(getattr(a, "date", 0) or 0), repr(a)) for a in page)
        if signature in seen_pages:
            break
        seen_pages.add(signature)
        activities.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    # Map feed timestamps to the scoring period reported by the transaction
    # endpoint. Those rows can provide dates even when their status/items are
    # malformed.
    dated_weeks = [(stamp, week) for week, stamps in date_weeks.items() for stamp in stamps]
    trades = {}
    unmatched_dates = 0
    for activity in activities:
        stamp = int(getattr(activity, "date", 0) or 0)
        if not stamp:
            continue
        actions = getattr(activity, "actions", []) or []
        sent, received = [], []
        for action in actions:
            if len(action) < 3:
                continue
            team, kind, player = action[:3]
            tid = getattr(team, "team_id", None)
            pid = getattr(player, "playerId", player if isinstance(player, (int, str)) else None)
            if tid is None or pid is None:
                continue
            try:
                entry = (int(pid), str(tid))
            except (TypeError, ValueError):
                continue
            if kind == "TRADE_SENT":
                sent.append(entry)
            elif kind == "TRADE_RECEIVED":
                received.append(entry)

        legs = []
        for player_id, from_id in sent:
            destinations = [to_id for received_id, to_id in received if received_id == player_id and to_id != from_id]
            for to_id in destinations[:1]:
                legs.append({"id": player_id, "from": from_id, "to": to_id})
        if not legs:
            continue

        participants = sorted({leg[side] for leg in legs for side in ("from", "to")})
        if len(participants) < 2:
            continue
        if dated_weeks:
            nearest_stamp, week = min(dated_weeks, key=lambda pair: abs(pair[0] - stamp))
            if abs(nearest_stamp - stamp) > 48 * 60 * 60 * 1000:
                unmatched_dates += 1
                continue
        else:
            unmatched_dates += 1
            continue
        leg_signature = ",".join(
            f"{leg['id']}:{leg['from']}>{leg['to']}"
            for leg in sorted(legs, key=lambda item: (item["id"], item["from"], item["to"]))
        )
        trade_key = f"activity:{stamp}:{','.join(participants)}:{leg_signature}"
        trades[trade_key] = {
            "id": trade_key,
            "week": int(week),
            "date_ms": stamp,
            "legs": legs,
            "participants": participants,
        }
    return list(trades.values()), unmatched_dates


def _current_rosters(league):
    result = {}
    for team in league.teams:
        result[str(team.team_id)] = [
            {
                "id": int(player.playerId),
                "name": str(player.name),
                "position": str(player.position),
                "projected_remaining": round(max(
                    0.0,
                    _safe_float(getattr(player, "projected_total_points", 0))
                    - _safe_float(getattr(player, "total_points", 0)),
                ), 1),
            }
            for player in getattr(team, "roster", []) or []
        ]
    return result


def _trade_data(league, team_names, completed_week):
    """Read each week's raw transaction records and calculate realized value.

    ESPN has changed the shape and access rules for trade items over time. Rows
    without both sides' player movements are omitted rather than presented as
    complete deals. The page reports when credentials are needed or no complete
    trade records were returned.
    """
    raw_trades = {}
    incomplete_trade_rows = 0
    warning = ""
    malformed_weeks = []
    date_weeks = defaultdict(list)
    for week in range(1, max(league.current_week, 1) + 1):
        try:
            transactions, malformed_rows, trade_dates, found_trade = _raw_trade_transactions(league, week)
            date_weeks[week].extend(trade_dates)
            if malformed_rows:
                malformed_weeks.append(week)
                print(f"Skipped {malformed_rows} malformed ESPN trade row(s) for week {week}")
        except Exception as exc:  # keep the rest of the public dashboard available
            warning = "ESPN returned an error while reading transaction history. Verify the ESPN_S2 and SWID secrets and check the Actions build log for details."
            print(f"Trade history unavailable for week {week}: {exc}")
            continue
        for transaction in transactions or []:
            status = str(getattr(transaction, "status", "")).upper()
            if getattr(transaction, "type", "") != "TRADE_ACCEPT" or status != "EXECUTED":
                continue
            legs = []
            for item in getattr(transaction, "items", []) or []:
                if str(getattr(item, "type", "")).upper() != "TRADE":
                    continue
                player_id = getattr(item, "playerId", None)
                from_id = getattr(item, "from_team_id", None)
                to_id = getattr(item, "to_team_id", None)
                if player_id is None or from_id is None or to_id is None:
                    continue
                if int(from_id or 0) == 0 or int(to_id or 0) == 0:
                    continue
                legs.append({"id": int(player_id), "from": str(from_id), "to": str(to_id)})
            participants = sorted(
                {leg[side] for leg in legs for side in ("from", "to")},
                key=lambda tid: (team_names.get(tid, "").lower() != "league manager", team_names.get(tid, "").lower()),
            )
            if len(participants) < 2 or not legs:
                incomplete_trade_rows += 1
                continue
            trade_date = getattr(transaction, "date", None)
            trade_id = str(getattr(transaction, "id", None) or f"{week}:{trade_date}:{getattr(transaction, 'team_id', '')}")
            raw_trades[trade_id] = {
                "id": trade_id,
                "week": int(getattr(transaction, "scoring_period", None) or week),
                "date_ms": int(trade_date or 0),
                "legs": legs,
                "participants": participants,
            }

    # ESPN's transaction endpoint may only include item movements for the
    # authenticated manager's trades. Fill gaps from the league-wide activity
    # feed, which reports paired sent/received rows for all teams.
    try:
        activity_trades, unmatched_activity = _activity_trade_rows(league, date_weeks)
        for trade in activity_trades:
            signature = sorted((leg["id"], leg["from"], leg["to"]) for leg in trade["legs"])
            match_key = next((key for key, existing in raw_trades.items()
                              if sorted((leg["id"], leg["from"], leg["to"])
                                        for leg in existing["legs"]) == signature), None)
            if match_key is not None:
                raw_trades.pop(match_key, None)
            raw_trades[trade["id"]] = trade
        if unmatched_activity:
            print(f"Could not map {unmatched_activity} ESPN activity trade event(s) to a scoring week")
    except Exception as exc:
        warning = "ESPN's league activity feed could not be read; only trade details returned by its transaction endpoint are shown."
        print(f"League-wide trade activity unavailable: {exc}")

    player_ids = sorted({leg["id"] for trade in raw_trades.values() for leg in trade["legs"]})
    player_data = {}
    if player_ids:
        try:
            fetched = league.player_info(playerId=player_ids)
            if fetched and not isinstance(fetched, list):
                fetched = [fetched]
            for player in fetched or []:
                player_data[str(player.playerId)] = player
        except Exception as exc:
            warning = warning or "Trade names loaded, but ESPN did not return player scoring details."
            print(f"Trade player details unavailable: {exc}")

    result = []
    team_value = defaultdict(float)
    team_wins = defaultdict(int)
    team_losses = defaultdict(int)
    for trade in raw_trades.values():
        by_team = {}
        for tid in trade["participants"]:
            by_team[tid] = {"team": team_names.get(tid, f"Team {tid}"), "incoming": [], "outgoing": []}
        for leg in trade["legs"]:
            p = player_data.get(str(leg["id"]))
            name = str(getattr(p, "name", league.player_map.get(leg["id"], f"Player {leg['id']}")))
            points_after = 0.0
            weekly_points = {}
            if p is not None:
                stats = getattr(p, "stats", {}) or {}
                for scored_week in range(trade["week"] + 1, completed_week + 1):
                    week_points = _safe_float((stats.get(scored_week) or stats.get(str(scored_week)) or {}).get("points"))
                    weekly_points[str(scored_week)] = round(week_points, 2)
                    points_after += week_points
            moved = {"id": leg["id"], "name": name, "points": round(points_after, 2),
                     "weekly_points": weekly_points}
            by_team[leg["to"]]["incoming"].append(moved)
            by_team[leg["from"]]["outgoing"].append(moved)

        denominator = max(completed_week - trade["week"], 0)
        for tid, side in by_team.items():
            incoming_pts = sum(p["points"] for p in side["incoming"])
            outgoing_pts = sum(p["points"] for p in side["outgoing"])
            side["weekly_points"] = []
            for scored_week in range(trade["week"] + 1, completed_week + 1):
                incoming_week = sum(p["weekly_points"].get(str(scored_week), 0.0) for p in side["incoming"])
                outgoing_week = sum(p["weekly_points"].get(str(scored_week), 0.0) for p in side["outgoing"])
                side["weekly_points"].append({
                    "week": scored_week,
                    "incoming": round(incoming_week, 2),
                    "outgoing": round(outgoing_week, 2),
                    "net": round(incoming_week - outgoing_week, 2),
                })
            side.update({
                "incoming_points": round(incoming_pts, 2),
                "outgoing_points": round(outgoing_pts, 2),
                "net_points": round(incoming_pts - outgoing_pts, 2),
                "points_per_week": round((incoming_pts - outgoing_pts) / denominator, 2) if denominator else None,
            })
            side["rating"] = _trade_rating(side["points_per_week"])
            if denominator:
                team_value[tid] += (incoming_pts - outgoing_pts) / denominator
            if incoming_pts > outgoing_pts:
                team_wins[tid] += 1
            elif incoming_pts < outgoing_pts:
                team_losses[tid] += 1

        date = datetime.fromtimestamp(trade["date_ms"] / 1000, timezone.utc).strftime("%b %d, %Y").replace(" 0", " ") if trade["date_ms"] else f"Week {trade['week']}"
        result.append({"id": trade["id"], "week": trade["week"], "date": date, "sides": list(by_team.values())})

    if raw_trades and not result:
        warning = warning or "ESPN returned no trade rows with complete player movements."
    if not raw_trades and not warning:
        warning = "No completed trades were returned. If the league has trades, add ESPN_S2 and SWID as GitHub Actions secrets; ESPN may restrict transaction history without them."
    if incomplete_trade_rows:
        warning = (warning + " " if warning else "") + f"{incomplete_trade_rows} trade row(s) had incomplete player movements and were left out; authenticated ESPN cookies may be needed."
    if malformed_weeks:
        skipped = ", ".join(str(week) for week in malformed_weeks)
        warning = (warning + " " if warning else "") + f"Some malformed ESPN transaction rows were skipped for week(s) {skipped}; valid trades from those weeks were still included."

    ordered = sorted(team_names.items(), key=lambda item: (item[1].lower() != "league manager", item[1].lower()))
    ratings = []
    if ordered:
        for tid, name in ordered:
            has_trades = team_wins.get(tid, 0) + team_losses.get(tid, 0) > 0
            label = _trade_rating(team_value.get(tid, 0.0)) if has_trades else "No trades yet"
            ratings.append({"team_id": tid, "team": name, "score": round(team_value.get(tid, 0.0), 2),
                            "wins": team_wins.get(tid, 0), "losses": team_losses.get(tid, 0), "rating": label})
    return result, warning, ratings


def _build_payload(league):
    teams = sorted(league.teams, key=lambda t: (t.team_name.lower() != "league manager", t.team_name.lower()))
    labels = [str(t.team_name) for t in teams]
    team_names = {str(t.team_id): str(t.team_name) for t in teams}
    regular_season_count = int(getattr(league.settings, "reg_season_count", 14))
    current_week = int(league.current_week)
    max_week = min(max(current_week, 1), regular_season_count)
    completed_week = min(max(int(league.current_week) - 1, 0), max_week)
    current_rosters = _current_rosters(league)
    drafted_player_teams = _drafted_player_teams(league)
    trades, trade_warning, ratings = _trade_data(league, team_names, completed_week)
    trade_acquisitions = defaultdict(list)
    team_id_by_name = {name: team_id for team_id, name in team_names.items()}
    for trade in trades:
        for side in trade.get("sides", []):
            team_id = team_id_by_name.get(side.get("team"))
            if team_id is None:
                continue
            for player in side.get("incoming", []):
                try:
                    trade_acquisitions[(str(team_id), int(player["id"]))].append(int(trade["week"]))
                except (KeyError, TypeError, ValueError):
                    continue

    score_rows = []
    position_week = defaultdict(lambda: defaultdict(lambda: {g: 0.0 for g in GROUPS}))
    position_totals = defaultdict(lambda: {g: 0.0 for g in GROUPS})
    source_week = defaultdict(lambda: defaultdict(lambda: {source: 0.0 for source in SOURCES}))
    postseason_score_rows = []

    for week in range(1, max_week + 1):
        matchups = league.box_scores(week)
        id_to_source, id_only_source = {}, {}
        try:
            id_to_source, id_only_source = _week_acquisition_sources(league, week)
        except Exception as exc:
            print(f"Could not load historical acquisition types for week {week}: {exc}")
        week_scores = []
        for box in matchups:
            home_id, away_id = _team_ids(box)
            home_name = str(getattr(getattr(box, "home_team", None), "team_name", ""))
            away_name = str(getattr(getattr(box, "away_team", None), "team_name", ""))
            if not home_name or not away_name:
                continue
            home_score, away_score = _safe_float(box.home_score), _safe_float(box.away_score)
            score_rows.extend([
                {"week": week, "team": home_name, "score": home_score, "opp": away_score},
                {"week": week, "team": away_name, "score": away_score, "opp": home_score},
            ])
            week_scores.extend([(home_name, home_score, away_score), (away_name, away_score, home_score)])
            for tid, name, lineup in (
                (home_id, home_name, getattr(box, "home_lineup", [])),
                (away_id, away_name, getattr(box, "away_lineup", [])),
            ):
                for player in lineup or []:
                    slot = getattr(player, "slot_position", "")
                    pos = _bucket(getattr(player, "position", ""), slot)
                    if pos is None:
                        continue
                    points = _safe_float(getattr(player, "points", 0))
                    position_week[name][week][pos] += points
                    position_totals[name][pos] += points
                    player_id = int(getattr(player, "playerId", 0) or 0)
                    source = (
                        id_to_source.get((str(tid), player_id))
                        or id_only_source.get(player_id)
                        or _source_bucket(getattr(player, "acquisitionType", ""))
                    )
                    if source == "Unknown":
                        trade_weeks = trade_acquisitions.get((str(tid), player_id), [])
                        if any(trade_week <= week for trade_week in trade_weeks):
                            source = "Traded"
                        elif (str(tid), player_id) in drafted_player_teams:
                            source = "Drafted"
                        else:
                            # Once ESPN's direct tag, draft record, and accepted
                            # trade history have been checked, an untagged roster
                            # add is a waiver/free-agent acquisition.
                            source = "Waivers"
                    source_week[name][week][source] += points
        if week > completed_week:
            continue
        if not week_scores or sum(x[1] for x in week_scores) <= 0:
            continue
        league_scores = [score for _, score, _ in week_scores]
        for name, score, opp in week_scores:
            lower = sum(1 for x in league_scores if x < score)
            tied = sum(1 for x in league_scores if x == score)
            expected = (lower + 0.5 * (tied - 1)) / max(len(league_scores) - 1, 1)
            actual = 1.0 if score > opp else (0.5 if score == opp else 0.0)
            row = next(r for r in reversed(score_rows) if r["week"] == week and r["team"] == name)
            row.update({"xw": expected, "actual": actual})

    for week in range(regular_season_count + 1, current_week):
        try:
            for box in league.box_scores(week):
                home_name = str(getattr(getattr(box, "home_team", None), "team_name", ""))
                away_name = str(getattr(getattr(box, "away_team", None), "team_name", ""))
                if home_name and away_name:
                    postseason_score_rows.extend([
                        {"week": week, "team": home_name, "score": _safe_float(box.home_score)},
                        {"week": week, "team": away_name, "score": _safe_float(box.away_score)},
                    ])
        except Exception as exc:
            print(f"Could not load completed playoff scores for week {week}: {exc}")

    df = pd.DataFrame(score_rows)
    if not df.empty and "xw" not in df.columns:
        df["xw"] = pd.NA
        df["actual"] = pd.NA
    completed = df[(df["week"] <= completed_week) & df["xw"].notna()].copy() if not df.empty else pd.DataFrame()
    season = []
    if not completed.empty:
        for name, group in completed.groupby("team"):
            season.append({
                "team": name,
                "games": int(len(group)),
                "points_for": round(float(group["score"].sum()), 2),
                "actual": round(float(group["actual"].sum()), 2),
                "xw": round(float(group["xw"].sum()), 3),
                "xwin_pct": round(float(group["xw"].mean()), 3),
                "luck": round(float(group["actual"].sum() - group["xw"].sum()), 3),
                "weeks": [int(w) for w in group.sort_values("week")["week"].tolist()],
                "cum_xw": [round(float(v), 3) for v in group.sort_values("week")["xw"].cumsum().tolist()],
                "cum_actual": [round(float(v), 3) for v in group.sort_values("week")["actual"].cumsum().tolist()],
                "weekly_xw": {str(int(r.week)): round(float(r.xw), 3) for r in group.itertuples()},
            })
    season_by_team = {r["team"]: r for r in season}
    season.sort(key=lambda r: (r["team"].lower() != "league manager", -r["xw"], r["team"].lower()))

    weekly_results = {name: [] for name in labels}
    team_records = {}
    if not completed.empty:
        for name, group in completed.groupby("team"):
            ordered = group.sort_values("week")
            wins = int((ordered["actual"] == 1).sum())
            ties = int((ordered["actual"] == 0.5).sum())
            losses = int(len(ordered) - wins - ties)
            points_for = float(ordered["score"].sum())
            points_against = float(ordered["opp"].sum())
            games = int(len(ordered))
            team_records[name] = {
                "team": name, "wins": wins, "losses": losses, "ties": ties,
                "games": games, "pct": (wins + 0.5 * ties) / games if games else 0.0,
                "points_for": points_for, "points_against": points_against,
                "point_diff": points_for - points_against,
            }
            weekly_results[name] = [{
                "week": int(row.week), "points_for": round(float(row.score), 2),
                "points_against": round(float(row.opp), 2),
                "point_diff": round(float(row.score - row.opp), 2),
                "outcome": "W" if row.actual == 1 else ("L" if row.actual == 0 else "T"),
            } for row in ordered.itertuples()]

    for name in labels:
        team_records.setdefault(name, {
            "team": name, "wins": 0, "losses": 0, "ties": 0, "games": 0,
            "pct": 0.0, "points_for": 0.0, "points_against": 0.0, "point_diff": 0.0,
        })
    fallback_order = sorted(team_records.values(), key=lambda r: (-r["wins"], r["losses"], -r["points_for"], r["team"].lower()))
    try:
        official_teams = league.standings_weekly(completed_week) if completed_week > 0 else league.standings()
        official_order = [str(team.team_name) for team in official_teams]
    except Exception as exc:
        print(f"Could not load ESPN's weekly tiebreak standings; using points-for fallback: {exc}")
        official_order = [row["team"] for row in fallback_order]
    official_rank = {name: rank for rank, name in enumerate(official_order, 1)}
    leader = min(team_records.values(), key=lambda r: official_rank.get(r["team"], len(labels) + 1))
    standings = []
    for record in sorted(team_records.values(), key=lambda r: official_rank.get(r["team"], len(labels) + 1)):
        standings.append({**record, "espn_rank": official_rank.get(record["team"], len(standings) + 1),
                          "games_back": round(((leader["wins"] - record["wins"]) + (record["losses"] - leader["losses"])) / 2, 1)})

    recent_rows = {}
    for name in labels:
        team_rows = completed[completed["team"] == name].sort_values("week").tail(3) if not completed.empty else pd.DataFrame()
        recent_rows[name] = team_rows
    recent_points = {
        name: float(rows["score"].mean()) if not rows.empty else 0.0
        for name, rows in recent_rows.items()
    }
    recent_min = min(recent_points.values(), default=0.0)
    recent_max = max(recent_points.values(), default=0.0)
    power_rankings = []
    for name in labels:
        recent = recent_rows[name]
        recent_wins = int((recent["actual"] == 1).sum()) if not recent.empty else 0
        recent_ties = int((recent["actual"] == 0.5).sum()) if not recent.empty else 0
        recent_games = len(recent)
        recent_pct = (recent_wins + 0.5 * recent_ties) / recent_games if recent_games else 0.0
        scoring_score = ((recent_points[name] - recent_min) / (recent_max - recent_min)) if recent_max > recent_min else 0.5
        overall_pct = team_records[name]["pct"]
        score = 100 * (0.20 * overall_pct + 0.50 * recent_pct + 0.30 * scoring_score)
        power_rankings.append({
            "team": name, "score": round(score, 2), "overall_pct": round(overall_pct, 3),
            "recent_wins": recent_wins, "recent_losses": recent_games - recent_wins - recent_ties,
            "recent_ties": recent_ties, "recent_games": recent_games,
            "recent_points": round(recent_points[name], 2),
        })
    power_rankings.sort(key=lambda r: (-r["score"], r["team"].lower()))
    for rank, record in enumerate(power_rankings, 1):
        record["rank"] = rank

    # Keep snapshots of the same power-ranking formula for every completed
    # week so viewers can inspect how the rankings changed over the season.
    power_history = []
    for week in range(1, completed_week + 1):
        through_week = completed[completed["week"] <= week] if not completed.empty else pd.DataFrame()
        week_recent = {
            name: (through_week[through_week["team"] == name].sort_values("week").tail(3)
                   if not through_week.empty else pd.DataFrame())
            for name in labels
        }
        week_ppg = {name: float(rows["score"].mean()) if not rows.empty else 0.0
                    for name, rows in week_recent.items()}
        week_min, week_max = min(week_ppg.values(), default=0.0), max(week_ppg.values(), default=0.0)
        rankings = []
        for name in labels:
            rows = week_recent[name]
            wins = int((rows["actual"] == 1).sum()) if not rows.empty else 0
            ties = int((rows["actual"] == 0.5).sum()) if not rows.empty else 0
            games = len(rows)
            recent_pct = (wins + 0.5 * ties) / games if games else 0.0
            ppg_score = ((week_ppg[name] - week_min) / (week_max - week_min)) if week_max > week_min else 0.5
            overall_rows = through_week[through_week["team"] == name] if not through_week.empty else pd.DataFrame()
            overall_wins = int((overall_rows["actual"] == 1).sum()) if not overall_rows.empty else 0
            overall_ties = int((overall_rows["actual"] == 0.5).sum()) if not overall_rows.empty else 0
            overall_pct = (overall_wins + 0.5 * overall_ties) / len(overall_rows) if len(overall_rows) else 0.0
            score = 100 * (0.20 * overall_pct + 0.50 * recent_pct + 0.30 * ppg_score)
            rankings.append({"team": name, "score": round(score, 2)})
        rankings.sort(key=lambda row: (-row["score"], row["team"].lower()))
        for rank, record in enumerate(rankings, 1):
            record["rank"] = rank
        power_history.append({"week": week, "teams": rankings})

    # Reward wins over high-scoring opponents; penalize losses to opponents
    # who scored well below that week's league average. Normalize each result
    # by that week's scoring spread so weeks remain comparable.
    clutch_scores = defaultdict(float)
    if not completed.empty:
        league_by_week = completed.groupby("week")["score"].agg(["mean", "std"])
        for row in completed.itertuples():
            avg = float(league_by_week.loc[row.week, "mean"])
            spread = float(league_by_week.loc[row.week, "std"])
            z = (float(row.opp) - avg) / (spread if spread > 0 else 1.0)
            if row.actual == 1:
                clutch_scores[row.team] += max(z, 0.0)
            elif row.actual == 0:
                clutch_scores[row.team] -= max(-z, 0.0)
    clutch_rankings = [{"team": name, "rating": round(clutch_scores[name], 3)} for name in labels]
    clutch_rankings.sort(key=lambda r: (-r["rating"], r["team"].lower()))
    for rank, record in enumerate(clutch_rankings, 1):
        record["rank"] = rank

    # Use the in-progress regular-season scores as provisional results so the
    # published playoff picture can move while games are being played.
    bracket_records = {name: dict(record) for name, record in team_records.items()}
    current_week = int(league.current_week)
    regular_season_count = int(getattr(league.settings, "reg_season_count", 14))
    bracket_live_week = None
    if completed_week < current_week <= regular_season_count:
        for row in score_rows:
            if row["week"] != current_week or (row["score"] <= 0 and row["opp"] <= 0):
                continue
            bracket_live_week = current_week
            record = bracket_records[row["team"]]
            record["games"] += 1
            record["points_for"] += row["score"]
            record["points_against"] += row["opp"]
            record["point_diff"] += row["score"] - row["opp"]
            if row["score"] > row["opp"]:
                record["wins"] += 1
            elif row["score"] < row["opp"]:
                record["losses"] += 1
            else:
                record["ties"] += 1
            record["pct"] = (record["wins"] + 0.5 * record["ties"]) / record["games"]
    bracket_records = sorted(bracket_records.values(), key=lambda r: official_rank.get(r["team"], len(labels) + 1))
    try:
        playoff_team_count = max(0, min(len(labels), int(getattr(league.settings, "playoff_team_count", 0) or 0)))
    except (TypeError, ValueError):
        playoff_team_count = 0
    playoff_teams = [{"seed": seed, "team": record["team"], "record": f'{record["wins"]}-{record["losses"]}-{record["ties"]}'}
                     for seed, record in enumerate(bracket_records[:playoff_team_count], 1)]

    multiverse = _simulate_multiverse(
        league, labels, teams, score_rows, completed_week, regular_season_count,
        playoff_team_count, postseason_score_rows,
        {name: record["wins"] for name, record in team_records.items()},
    )

    weeks_for_stats = list(range(1, max_week + 1))
    weekly_positions = {
        name: [{"week": w, **{g: round(position_week[name][w][g], 2) for g in GROUPS}} for w in weeks_for_stats]
        for name in labels
    }
    weekly_sources = {
        name: [{"week": w, **{source: round(source_week[name][w][source], 2) for source in SOURCES}} for w in weeks_for_stats]
        for name in labels
    }
    heat = {name: [season_by_team.get(name, {}).get("weekly_xw", {}).get(str(w)) for w in range(1, completed_week + 1)] for name in labels}

    live_weeks = []
    for path in ROOT.glob("live_week_*.json"):
        try:
            live_weeks.append(int(path.stem.rsplit("_", 1)[1]))
        except (ValueError, IndexError):
            continue
    live_weeks = sorted(set(live_weeks))

    return {
        "year": YEAR, "league_id": LEAGUE_ID, "teams": labels, "groups": GROUPS, "sources": SOURCES,
        "current_week": int(league.current_week), "completed_week": completed_week,
        "weeks": weeks_for_stats, "season": season, "heat": heat,
        "standings": standings, "weekly_results": weekly_results,
        "fantasy_multiverse": multiverse,
        "power_rankings": power_rankings, "power_history": power_history,
        "clutch_rankings": clutch_rankings,
        "playoff_teams": playoff_teams, "playoff_team_count": playoff_team_count,
        "bracket_live_week": bracket_live_week,
        "position_totals": {name: position_totals[name] for name in labels},
        "weekly_positions": weekly_positions, "weekly_sources": weekly_sources,
        "live_weeks": live_weeks, "trades": trades, "trade_warning": trade_warning,
        "trade_ratings": ratings,
        "team_abbrevs": {str(t.team_name): str(getattr(t, "team_abbrev", "") or "") for t in teams},
        "rosters": current_rosters,
        "roster_team_ids": {str(t.team_name): str(t.team_id) for t in teams},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _html(payload):
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return r'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expected Wins - Fantasy League Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>.team-buttons{display:flex;gap:7px;flex-wrap:wrap}.team-button{min-width:48px;padding:7px 10px;font-size:13px;font-weight:700}.team-button.active{background:#183820;border-color:#3fb950;color:#9be5aa}.team-button:focus-visible{outline:2px solid var(--blue);outline-offset:2px}.trade-rating{margin-left:8px}.trade-card h3{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.subtabs{display:flex;gap:8px;margin:4px 0 14px;border-bottom:1px solid var(--line)}.subtab{border:0;border-radius:8px 8px 0 0;background:transparent;color:var(--muted);padding:10px 14px;font-weight:700}.subtab.active{color:var(--text);background:var(--panel);border-bottom:2px solid var(--blue)}.league-view,.team-view,.luck-view{display:none}.league-view.active,.team-view.active,.luck-view.active{display:block}#standingsTable th,#standingsTable td,#powerTable th,#powerTable td{text-align:right}#standingsTable th:nth-child(2),#standingsTable td:nth-child(2),#powerTable th:nth-child(2),#powerTable td:nth-child(2){text-align:left;white-space:nowrap}#standingsTable th:nth-child(8),#standingsTable td:nth-child(8),#standingsTable th:nth-child(9),#standingsTable td:nth-child(9){text-align:center}.standings-value{font-weight:700}.sortable{cursor:pointer;user-select:none}.sortable:hover{color:var(--text)}.power-highlight{text-align:center;min-height:22px;color:var(--muted);font-weight:700}.model-details{margin:4px 0 14px;color:var(--muted);font-size:13px}.model-details summary{display:inline-block;color:var(--blue);cursor:pointer;font-weight:650}.model-details p{max-width:900px;line-height:1.5;margin:8px 0 0}.live-team-name{text-align:center;font-size:clamp(20px,3vw,30px);font-weight:800;line-height:1.2;padding:8px 4px;color:var(--text)}.live-team-bottom{padding-top:0}.bracket-scroll{overflow-x:auto;padding:2px 0 10px}.bracket{display:flex;gap:16px;min-width:max-content;align-items:flex-start}.bracket-round{width:250px;display:flex;flex-direction:column;gap:12px}.bracket-round h3{font-size:14px;margin:0;color:var(--muted)}.bracket-round.bracket-final{align-self:center}.bracket-match{border:1px solid var(--line);background:var(--bg);border-radius:10px;overflow:hidden}.bracket-side{padding:9px 11px;border-bottom:1px solid var(--line);font-size:13px}.bracket-side:last-child{border-bottom:0}.bracket-side.muted{color:var(--muted)}</style>
<style>
:root{color-scheme:dark;--bg:#0f1419;--panel:#161d26;--line:#243040;--muted:#8b98a5;--text:#e6edf3;--blue:#4da3ff;--gold:#f5a623;--green:#3fb950;--red:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,Segoe UI,Arial,sans-serif}.wrap{max-width:1500px;margin:auto;padding:22px 16px 56px}h1{font-size:27px;margin:0}.sub{color:var(--muted);margin:4px 0 20px}.tabs{display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:18px}.tab{background:transparent;color:var(--muted);border:0;border-bottom:3px solid transparent;padding:12px 16px;font-weight:700;font-size:15px;cursor:pointer}.tab.active{color:var(--text);border-color:var(--blue)}.panel{display:none}.panel.active{display:block}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px;min-width:0}.card h2{font-size:18px;margin:0 0 4px}.card p{font-size:13px;color:var(--muted);margin:0 0 12px}.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 14px}select,button,input{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 11px;font:inherit}button{cursor:pointer}.chartbox{height:390px;position:relative}.radar-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.rcell{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px;min-width:0}.rcell h3{text-align:center;margin:0 0 7px;font-size:14px}.rbox{height:210px;position:relative}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:center;padding:8px;border-bottom:1px solid var(--line)}th{color:var(--muted)}td:first-child,th:first-child{text-align:left;white-space:nowrap}.notice{border:1px solid #745d24;background:#2a2418;color:#f3d58b;padding:11px 13px;border-radius:10px;margin:10px 0}.good{border-color:#2c6840;background:#17291c;color:#9be5aa}.trade-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.trade-card{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0}.trade-card-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,38%);gap:16px;align-items:center}.trade-card-info{min-width:0}.trade-mini-chart{height:190px;position:relative;min-width:0}.team-trade-chart{height:320px;position:relative}.badge{display:inline-block;border-radius:999px;padding:4px 9px;background:#233141;color:#a8d2ff;font-size:12px;font-weight:700}.badge.gm{background:#183820;color:#8cec9c}.badge.fleeced{background:#3a1d1d;color:#ff9c96}.badge.inmix{background:#25313b;color:#c8d1d9}.player-select{min-height:155px;width:100%}.metric{font-weight:700}.muted{color:var(--muted)}.calculator-result{font-size:17px;margin-top:12px}@media(max-width:760px){.trade-card-layout{grid-template-columns:1fr}.trade-mini-chart{height:210px}}@media(max-width:900px){.radar-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.chartbox{height:330px}}@media(max-width:520px){.wrap{padding:15px 10px}.tab{padding:10px 8px;font-size:13px}.radar-grid{grid-template-columns:1fr}.chartbox{height:290px}.team-trade-chart{height:280px}}
</style></head><body><main class="wrap">
<h1>Fielding of (football) dreams</h1><div class="sub" id="subtitle"></div>
<nav class="tabs" role="tablist" aria-label="Dashboard sections">
<button class="tab active" data-tab="team" role="tab" aria-selected="true">Team Stats</button><button class="tab" data-tab="league" role="tab" aria-selected="false">League Stats</button><button class="tab" data-tab="luck" role="tab" aria-selected="false">Luck</button><button class="tab" data-tab="live" role="tab" aria-selected="false">Live Win %</button><button class="tab" data-tab="trades" role="tab" aria-selected="false">Trades</button></nav>

<section class="panel active" id="panel-team" role="tabpanel">
<div class="subtabs" role="tablist" aria-label="Team statistics"><button class="subtab team-subtab active" type="button" data-team-view="position" aria-selected="true">Positional Breakdown</button><button class="subtab team-subtab" type="button" data-team-view="acquisition" aria-selected="false">Acquisitional Breakdown</button><button class="subtab team-subtab" type="button" data-team-view="differential" aria-selected="false">Win/Loss Differential</button></div>
<section class="team-view active" id="team-view-position">
<article class="card"><h2>Season points by position</h2><p>Starter points grouped by each player's position. Players in FLEX count toward their own position.</p><div id="radars" class="radar-grid"></div></article>
<article class="card"><h2>Weekly points by position</h2><p>All available weeks for one team. Each bar is stacked by the six positions.</p><div class="toolbar"><span>Team:</span><div id="positionTeams" class="team-buttons" aria-label="Choose team"></div></div><div class="chartbox"><canvas id="positionChart"></canvas></div></article>
</section>
<section class="team-view" id="team-view-acquisition"><article class="card"><h2>Weekly points by acquisition</h2><p>Starter points are grouped using ESPN roster tags, draft picks, and accepted trade history. If ESPN omits an acquisition tag and neither the draft nor trade records identify it, the player is counted under waivers/free agency.</p><div class="toolbar"><span>Team:</span><div id="sourceTeams" class="team-buttons" aria-label="Choose team"></div></div><div class="chartbox"><canvas id="sourceChart"></canvas></div></article></section>
<section class="team-view" id="team-view-differential">
<article class="card"><h2>Win/Loss Differential</h2><p>Weekly points scored minus points allowed. Wins are green and losses are red; bars are centered at zero.</p><div class="toolbar"><span>Team:</span><div id="differentialTeams" class="team-buttons" aria-label="Choose team"></div></div><div class="chartbox"><canvas id="differentialChart"></canvas></div></article>
<article class="card"><h2>Average Win/Loss Differential</h2><p>Average point differential in wins is above zero in green; average point differential in losses is below zero in red.</p><div class="chartbox"><canvas id="overallDifferentialChart"></canvas></div></article>
</section>
</section>

<section class="panel" id="panel-league" role="tabpanel">
<div class="subtabs" role="tablist" aria-label="League statistics"><button class="subtab league-subtab active" type="button" data-league-view="standings" aria-selected="true">Standings</button><button class="subtab league-subtab" type="button" data-league-view="power" aria-selected="false">Power Rankings</button><button class="subtab league-subtab" type="button" data-league-view="playoffs" aria-selected="false">Playoff Bracket</button></div>
<section class="league-view active" id="league-view-standings"><article class="card"><h2>League Standings</h2><p>Completed regular-season record, points for, points against, and point differential. Points columns use a red-to-green scale centered on the league average.</p><div class="scroll"><table id="standingsTable"></table></div></article></section>
<section class="league-view" id="league-view-power"><article class="card"><h2>Power Rankings</h2><div class="chartbox"><canvas id="powerHistoryChart"></canvas></div><div id="powerHighlight" class="power-highlight">Hover over or click a line to highlight a team.</div><div class="toolbar"><span>Week:</span><div id="powerWeeks" class="team-buttons" aria-label="Choose power ranking week"></div></div><div class="scroll"><table id="powerTable"></table></div></article></section>
<section class="league-view" id="league-view-playoffs"><article class="card"><h2>Playoff Bracket If the Season Ended Today</h2><p id="playoffBracketNote">Projected seeds use current standings, including live regular-season scores when available.</p><h3>Winners Bracket</h3><div id="playoffBracket" class="bracket-scroll"></div></article></section>
</section>

<section class="panel" id="panel-luck" role="tabpanel">
<div class="subtabs" role="tablist" aria-label="Luck statistics"><button class="subtab luck-subtab active" type="button" data-luck-view="luck" aria-selected="true">Luck</button><button class="subtab luck-subtab" type="button" data-luck-view="clutch" aria-selected="false">Clutch Rating</button><button class="subtab luck-subtab" type="button" data-luck-view="multiverse" aria-selected="false">Fantasy Multiverse</button></div>
<section class="luck-view active" id="luck-view-luck">
<article class="card"><h2>Expected wins and actual wins</h2><p>Expected wins compare each team's score with every other team that week. The shaded gap shows schedule luck.</p><div class="toolbar"><span>Team:</span><div id="luckTeams" class="team-buttons" aria-label="Choose team"></div><span id="luckNote" class="muted"></span></div><div class="chartbox"><canvas id="luckLine"></canvas></div></article>
<article class="card"><h2>Schedule luck (actual wins minus expected wins)</h2><p>Positive means a team won more often than its points would predict against the league; negative means fewer.</p><div class="chartbox"><canvas id="luckBars"></canvas></div></article>
<article class="card"><h2>Weekly expected win share</h2><p>Each cell is the share of league teams outscored that week. 1.00 is the top score.</p><div class="scroll"><table id="heat"></table></div></article>
</section>
<section class="luck-view" id="luck-view-clutch"><article class="card"><h2>Clutch Rating</h2><div class="chartbox"><canvas id="clutchChart"></canvas></div></article></section>
<section class="luck-view" id="luck-view-multiverse">
<article class="card"><h2>Fantasy Multiverse</h2><p>Win distributions compare each team’s actual weekly scores against 20,000 sampled valid schedules. Teams face every opponent once, then repeat the same early-season opponents for the remaining regular-season weeks.</p><div class="toolbar"><span>Team:</span><div id="multiverseTeams" class="team-buttons" aria-label="Choose multiverse team"></div><span id="multiverseTeamLabel" class="muted"></span></div><div class="chartbox"><canvas id="multiverseTeamChart"></canvas></div></article>
<article class="card"><h2>All Teams’ Win Distributions</h2><div id="multiverseAllLabel" class="power-highlight">Hover over or click a line to highlight a team.</div><div class="chartbox"><canvas id="multiverseAllChart"></canvas></div></article>
<article class="card"><h2>First Place Across Schedules</h2><div class="chartbox"><canvas id="multiverseFirstPie"></canvas></div></article>
<article class="card"><h2>Playoff Titles Across Schedules</h2><div id="multiversePlayoffNote" class="muted"></div><div class="chartbox"><canvas id="multiversePlayoffPie"></canvas></div></article>
</section>
</section>

<section class="panel" id="panel-live" role="tabpanel">
<article class="card"><h2>Live matchup win probability</h2><div class="toolbar"><span>Week:</span><div id="liveWeeks" class="team-buttons" aria-label="Choose week"></div><span>Matchup:</span><div id="liveMatchups" class="team-buttons" aria-label="Choose matchup"></div><span id="liveNote" class="muted"></span></div><div id="liveTopTeam" class="live-team-name live-team-top"></div><div class="chartbox"><canvas id="liveChart"></canvas></div><div id="liveBottomTeam" class="live-team-name live-team-bottom"></div></article>
</section>

<section class="panel" id="panel-trades" role="tabpanel">
<article class="card"><h2>Trades by team</h2><p>Accepted trades involving the selected team, with post-trade player points and an individual trade rating.</p><div class="toolbar"><span>Team:</span><div id="tradeTeams" class="team-buttons" aria-label="Choose team"></div><span id="teamRating"></span></div><div id="tradeNotice" class="notice" style="display:none"></div><h3 id="teamTradeTitle"></h3><div class="team-trade-chart"><canvas id="teamTradeChart"></canvas></div><div id="teamTrades"></div></article>
</section>
<div class="sub" id="updated"></div>
</main><script>
const D=__DATA__;
document.getElementById('subtitle').textContent=`${D.year} season · ${D.completed_week} completed week${D.completed_week===1?'':'s'} · ESPN league ${D.league_id}`;
document.getElementById('updated').textContent='Dashboard built '+new Date(D.generated_at).toLocaleString();
Chart.defaults.color='#c9d4de';Chart.defaults.borderColor='#243040';
const colors=['#4da3ff','#f5a623','#3fb950','#da70d6','#f85149','#a7b3c2'];
function fillTeams(id, list=D.teams){const s=document.getElementById(id);s.replaceChildren(...list.map(n=>{const o=document.createElement('option');o.value=n;o.textContent=n;return o;}));}
function teamAbbrev(team){const exact=D.team_abbrevs?.[team];if(exact)return exact;return team.split(/\s+/).map(w=>w[0]||'').join('').slice(0,5).toUpperCase();}
function fillTeamButtons(id,onChange,initial=D.teams[0]){const host=document.getElementById(id);const buttons=new Map();let selected=initial;D.teams.forEach(team=>{const b=document.createElement('button');b.type='button';b.className='team-button';b.textContent=teamAbbrev(team);b.title=team;b.setAttribute('aria-label',team);b.setAttribute('aria-pressed','false');b.addEventListener('click',()=>choose(team));host.append(b);buttons.set(team,b);});function choose(team){selected=team;buttons.forEach((b,n)=>{const active=n===team;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});onChange(team);}choose(selected);return{value:()=>selected,choose};}
document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>{const active=x===btn;x.classList.toggle('active',active);x.setAttribute('aria-selected',active)});document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id==='panel-'+btn.dataset.tab));requestAnimationFrame(()=>document.querySelectorAll('.panel.active canvas').forEach(c=>Chart.getChart(c)?.resize()));}));
const radars=document.getElementById('radars');const maxima=D.groups.map(g=>Math.max(1,...D.teams.map(t=>D.position_totals[t][g]||0)));const normalized=a=>a.map((v,i)=>100*v/maxima[i]);
D.teams.forEach((team,i)=>{const cell=document.createElement('div');cell.className='rcell';const title=document.createElement('h3');title.textContent=team;const box=document.createElement('div');box.className='rbox';const cv=document.createElement('canvas');box.append(cv);cell.append(title,box);radars.append(cell);new Chart(cv,{type:'radar',data:{labels:D.groups.map((g,j)=>[g,'(max '+maxima[j].toFixed(0)+')']),datasets:[{label:team,data:normalized(D.groups.map(g=>D.position_totals[team][g]||0)),borderColor:colors[0],backgroundColor:'rgba(77,163,255,.28)',borderWidth:2},{label:'League avg',data:normalized(D.groups.map(g=>D.teams.reduce((s,t)=>s+(D.position_totals[t][g]||0),0)/D.teams.length)),borderColor:'#8b98a5',borderDash:[5,4],backgroundColor:'transparent',pointRadius:0}]},options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label+': '+(c.datasetIndex===0?(D.position_totals[team][D.groups[c.dataIndex]]||0):(D.teams.reduce((s,t)=>s+(D.position_totals[t][D.groups[c.dataIndex]]||0),0)/D.teams.length)).toFixed(1)+' pts'}}},scales:{r:{min:0,max:100,ticks:{display:false},grid:{color:'#243040'},angleLines:{color:'#243040'}}}}});});

const positionGroups=['DEF',...D.groups.filter(group=>group!=='DEF')];
const positionRows=D.teams.flatMap(team=>D.weekly_positions[team]||[]);
const positionAxisMax=Math.max(20,Math.ceil(Math.max(0,...positionRows.map(row=>positionGroups.reduce((sum,group)=>sum+Math.max(0,Number(row[group]||0)),0)))/20)*20);
const positionAxisMin=Math.min(0,Math.floor(Math.min(0,...positionRows.map(row=>positionGroups.reduce((sum,group)=>sum+Math.min(0,Number(row[group]||0)),0)))/20)*20);
const posChart=new Chart(document.getElementById('positionChart'),{type:'bar',data:{labels:D.weeks.map(w=>'W'+w),datasets:positionGroups.map((g)=>({label:g,data:[],backgroundColor:colors[D.groups.indexOf(g)]}))},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:false,min:positionAxisMin,max:positionAxisMax,title:{display:true,text:'Fantasy points'}}}}});
function showPosition(t){const rows=D.weekly_positions[t]||[];posChart.options.plugins.title.text=t;posChart.data.datasets.forEach((ds,i)=>ds.data=rows.map(r=>r[positionGroups[i]]||0));posChart.update();}
const positionButtons=fillTeamButtons('positionTeams',showPosition);
const sourceColors={Drafted:'#4da3ff',Traded:'#da70d6',Waivers:'#f5a623',Unknown:'#8b98a5'};
const sourceChart=new Chart(document.getElementById('sourceChart'),{type:'bar',data:{labels:D.weeks.map(w=>'W'+w),datasets:D.sources.map(source=>({label:source,data:[],backgroundColor:sourceColors[source]}))},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:false,min:positionAxisMin,max:positionAxisMax,title:{display:true,text:'Fantasy points'}}}}});
function showSource(team){const rows=D.weekly_sources[team]||[];sourceChart.options.plugins.title.text=team;sourceChart.data.datasets.forEach((dataset,index)=>dataset.data=rows.map(row=>row[D.sources[index]]||0));sourceChart.update();}
const sourceButtons=fillTeamButtons('sourceTeams',showSource);
const differentialChart=new Chart(document.getElementById('differentialChart'),{type:'bar',data:{labels:[],datasets:[{label:'Points scored - points allowed',data:[],backgroundColor:[],borderRadius:4},{type:'line',label:'0 points',data:[],borderColor:'#a7b3c2',borderDash:[6,5],borderWidth:1.5,pointRadius:0,fill:false}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''},legend:{position:'bottom'}},scales:{x:{title:{display:true,text:'Week'}},y:{title:{display:true,text:'Point differential'},ticks:{precision:1}}}}});
function showDifferential(team){const rows=D.weekly_results[team]||[],values=rows.map(r=>r.point_diff),bound=Math.max(1,...values.map(v=>Math.abs(v)));differentialChart.options.plugins.title.text='Win/Loss Differential by week - '+team;differentialChart.options.scales.y.min=-bound;differentialChart.options.scales.y.max=bound;differentialChart.data.labels=rows.map(r=>'W'+r.week);differentialChart.data.datasets[0].data=values;differentialChart.data.datasets[0].backgroundColor=rows.map(r=>r.outcome==='W'?'#3fb950':r.outcome==='L'?'#f85149':'#a7b3c2');differentialChart.data.datasets[1].data=values.map(()=>0);differentialChart.update();}
const differentialButtons=fillTeamButtons('differentialTeams',showDifferential);
const winDiffTotals=D.teams.map(team=>{const rows=(D.weekly_results[team]||[]).filter(row=>row.outcome==='W');return rows.length?rows.reduce((sum,row)=>sum+Math.abs(Number(row.point_diff)||0),0)/rows.length:0;});
const lossDiffTotals=D.teams.map(team=>{const rows=(D.weekly_results[team]||[]).filter(row=>row.outcome==='L');return rows.length?-rows.reduce((sum,row)=>sum+Math.abs(Number(row.point_diff)||0),0)/rows.length:0;});
const overallDiffBound=Math.max(1,...winDiffTotals.map(Math.abs),...lossDiffTotals.map(Math.abs));
new Chart(document.getElementById('overallDifferentialChart'),{type:'bar',data:{labels:D.teams,datasets:[{label:'Point differential in wins',data:winDiffTotals,backgroundColor:'#3fb950',borderRadius:3,stack:'differential'},{label:'Point differential in losses',data:lossDiffTotals,backgroundColor:'#f85149',borderRadius:3,stack:'differential'},{type:'line',label:'0 points',data:D.teams.map(()=>0),borderColor:'#a7b3c2',borderDash:[6,5],borderWidth:1.5,pointRadius:0,fill:false}]},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom'}},scales:{x:{stacked:true,ticks:{maxRotation:45,minRotation:0}},y:{stacked:true,min:-overallDiffBound,max:overallDiffBound,title:{display:true,text:'Point differential'},ticks:{precision:0}}}}});

const standingsTable=document.getElementById('standingsTable');
function appendTableHeader(table,labels){const tr=document.createElement('tr');labels.forEach(label=>{const th=document.createElement('th');th.textContent=label;tr.append(th);});table.append(tr);}
function colorByLeagueAverage(value,values){const avg=values.reduce((sum,v)=>sum+v,0)/(values.length||1),lo=Math.min(...values,avg),hi=Math.max(...values,avg),red=[248,81,73],yellow=[245,214,108],green=[63,185,80];let a,b,t;if(value<=avg){a=red;b=yellow;t=avg===lo?1:(value-lo)/(avg-lo);}else{a=yellow;b=green;t=hi===avg?1:(value-avg)/(hi-avg);}t=Math.max(0,Math.min(1,t));return 'rgb('+a.map((channel,i)=>Math.round(channel+(b[i]-channel)*t)).join(',')+')';}
const sortKeys={wins:'wins',losses:'losses',ties:'ties',pct:'pct',games_back:'games_back',points_for:'points_for',points_against:'points_against',point_diff:'point_diff'};
const sortLabels={wins:'W',losses:'L',ties:'T',pct:'PCT',games_back:'GB',points_for:'PF (PPG)',points_against:'PA (PPG)',point_diff:'DIFF'};
const defaultSortDirection={wins:-1,losses:1,ties:-1,pct:-1,games_back:1,points_for:-1,points_against:1,point_diff:-1};let standingsSort='wins',standingsDirection=-1;
function renderStandings(){
  const key=sortKeys[standingsSort]||'wins',direction=standingsDirection;
  const rows=[...D.standings].sort((a,b)=>standingsSort==='wins'?(Number(a.espn_rank||999)-Number(b.espn_rank||999)):direction*(Number(a[key]||0)-Number(b[key]||0))||a.team.localeCompare(b.team));
  standingsTable.replaceChildren();const header=document.createElement('tr');
  [['Rank',null],['Team',null],...Object.entries(sortLabels).map(([k,label])=>[label,k])].forEach(([label,k])=>{const th=document.createElement('th');th.textContent=label+(k===standingsSort?(direction<0?' ▼':' ▲'):'');if(k){th.className='sortable';th.setAttribute('aria-sort',k===standingsSort?(direction<0?'descending':'ascending'):'none');th.title=k==='wins'?'Sort by ESPN standings order, including league tiebreakers':'Sort by '+label;th.addEventListener('click',()=>{if(k==='wins'){standingsSort='wins';standingsDirection=-1;}else if(standingsSort===k)standingsDirection*=-1;else{standingsSort=k;standingsDirection=defaultSortDirection[k];}renderStandings();});}header.append(th);});standingsTable.append(header);
  rows.forEach((row,index)=>{const tr=document.createElement('tr');[index+1,row.team,row.wins,row.losses,row.ties,Number(row.pct||0).toFixed(3),row.games_back===0?'—':Number(row.games_back||0).toFixed(1)].forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});
    const games=Number(row.games||0),pf=Number(row.points_for||0),pa=Number(row.points_against||0),diff=Number(row.point_diff||0);
    [[pf,'points_for',false],[pa,'points_against',true],[diff,'point_diff',false]].forEach(([value,metric,invert])=>{const td=document.createElement('td');td.className='standings-value';td.textContent=metric==='point_diff'?value.toFixed(1):`${value.toFixed(1)} (${games?(value/games).toFixed(1):'0.0'})`;const values=D.standings.map(r=>Number(r[metric]||0));td.style.color=colorByLeagueAverage(invert?-value:value,invert?values.map(v=>-v):values);tr.append(td);});
    standingsTable.append(tr);
  });
}
renderStandings();

const powerTable=document.getElementById('powerTable'),powerWeeksHost=document.getElementById('powerWeeks');appendTableHeader(powerTable,['Rank','Team','Score']);
function renderPowerRankings(rows){powerTable.replaceChildren();appendTableHeader(powerTable,['Rank','Team','Score']);const scores=rows.map(row=>row.score);rows.forEach(row=>{const tr=document.createElement('tr');[row.rank,row.team,row.score.toFixed(1)].forEach((value,index)=>{const td=document.createElement('td');td.textContent=value;if(index===2){td.className='standings-value';td.style.color=colorByLeagueAverage(row.score,scores);}tr.append(td);});powerTable.append(tr);});}
const powerHistory=D.power_history||[];const powerChoices=[...powerHistory,{week:'Current',teams:D.power_rankings}];
powerChoices.forEach((entry,index)=>makeChoiceButton(powerWeeksHost,entry.week==='Current'?'Current':'W'+entry.week,'Power rankings after week '+entry.week,index===powerChoices.length-1,()=>{[...powerWeeksHost.children].forEach((button,i)=>{const active=i===index;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});renderPowerRankings(entry.teams);}));renderPowerRankings(D.power_rankings);
const powerColors=D.teams.map((_,i)=>`hsl(${Math.round(360*i/Math.max(1,D.teams.length))} 78% 58%)`),powerHighlight=document.getElementById('powerHighlight');let pinnedPowerTeam=null;
const powerChart=new Chart(document.getElementById('powerHistoryChart'),{type:'line',data:{labels:powerHistory.map(row=>'W'+row.week),datasets:D.teams.map((team,index)=>({label:team,data:powerHistory.map(week=>week.teams.find(row=>row.team===team)?.rank??null),borderColor:powerColors[index],backgroundColor:powerColors[index],borderWidth:1.5,pointRadius:1,pointHoverRadius:5,tension:.18,spanGaps:false}))},options:{maintainAspectRatio:false,interaction:{mode:'nearest',intersect:false},plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>items.length?'Week '+powerHistory[items[0].dataIndex]?.week:'',label:item=>`${D.teams[item.datasetIndex]} · rank ${item.raw}`}}},scales:{y:{reverse:true,min:1,max:D.teams.length,ticks:{stepSize:1},title:{display:true,text:'Power rank (1 is best)'}},x:{title:{display:true,text:'Completed week'}}},onHover:(event,elements)=>{if(pinnedPowerTeam===null)highlightPowerTeam(elements[0]?.datasetIndex??null);},onClick:(event,elements)=>{const index=elements[0]?.datasetIndex;if(index===undefined){pinnedPowerTeam=null;highlightPowerTeam(null);}else{pinnedPowerTeam=pinnedPowerTeam===index?null:index;highlightPowerTeam(pinnedPowerTeam);}}}});
function highlightPowerTeam(index){powerChart.data.datasets.forEach((dataset,i)=>{dataset.borderColor=index===null?powerColors[i]:(i===index?powerColors[i]:'rgba(160,170,180,.18)');dataset.backgroundColor=dataset.borderColor;dataset.borderWidth=index===i?4:1.5;dataset.pointRadius=index===i?2:0;});powerHighlight.textContent=index===null?'Hover over or click a line to highlight a team.':D.teams[index];powerHighlight.style.color=index===null?'var(--muted)':powerColors[index];powerChart.update('none');}
const playoffBracket=document.getElementById('playoffBracket'),playoffNote=document.getElementById('playoffBracketNote');
function renderPlayoffBracket(){
  playoffBracket.replaceChildren();
  playoffNote.textContent=D.bracket_live_week?`Includes in-progress Week ${D.bracket_live_week} scores; refreshes with dashboard updates.`:'Based on current standings; refreshes with dashboard updates.';
  const entrants=D.playoff_teams||[];
  if(!D.playoff_team_count||!entrants.length){const p=document.createElement('p');p.className='muted';p.textContent='ESPN does not report playoff teams for this league.';playoffBracket.append(p);return;}
  let size=2;while(size<entrants.length)size*=2;
  let seedOrder=[1,2];for(let n=4;n<=size;n*=2)seedOrder=seedOrder.flatMap(seed=>[seed,n+1-seed]);
  const bySeed=new Map(entrants.map(row=>[row.seed,`#${row.seed} · ${row.team} (${row.record})`]));
  let nodes=seedOrder.map(seed=>bySeed.get(seed)||null),roundIndex=0,gameNumber=1;const bracket=document.createElement('div');bracket.className='bracket';
  while(nodes.length>1){
    const matches=[],next=[];
    for(let i=0;i<nodes.length;i+=2){
      const left=nodes[i],right=nodes[i+1],number=gameNumber++;
      const match={left:left||'BYE',right:right||'BYE',number};matches.push(match);
      next.push(left&&!right?left:right&&!left?right:`Winner of Game ${number}`);
    }
    const round=document.createElement('div');round.className='bracket-round';const heading=document.createElement('h3');
    if(matches.length===1)round.classList.add('bracket-final');
    heading.textContent=matches.length===1?'Championship':(roundIndex===0&&entrants.length<size?'First Round':matches.length===2?'Semifinals':matches.length===4?'Quarterfinals':`Round ${roundIndex+1}`);round.append(heading);
    matches.forEach(match=>{const card=document.createElement('div');card.className='bracket-match';[match.left,match.right].forEach(label=>{const side=document.createElement('div');side.className='bracket-side'+(label==='BYE'||label.startsWith('Winner of')?' muted':'');side.textContent=label;card.append(side);});round.append(card);});
    bracket.append(round);nodes=next;roundIndex++;
  }
  playoffBracket.append(bracket);
}
renderPlayoffBracket();
function bindSubtabs(buttonSelector,viewSelector,prefix,key){document.querySelectorAll(buttonSelector).forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll(buttonSelector).forEach(other=>{const active=other===button;other.classList.toggle('active',active);other.setAttribute('aria-selected',String(active));});document.querySelectorAll(viewSelector).forEach(view=>view.classList.toggle('active',view.id===prefix+button.dataset[key]));requestAnimationFrame(()=>document.querySelectorAll('.panel.active canvas').forEach(canvas=>Chart.getChart(canvas)?.resize()));}));}
bindSubtabs('.league-subtab','#panel-league .league-view','league-view-','leagueView');bindSubtabs('.team-subtab','#panel-team .team-view','team-view-','teamView');bindSubtabs('.luck-subtab','#panel-luck .luck-view','luck-view-','luckView');

const luckData=new Map(D.season.map(r=>[r.team,r]));const luckLine=new Chart(document.getElementById('luckLine'),{type:'line',data:{labels:[],datasets:[{label:'Expected wins',data:[],borderColor:colors[0],borderWidth:3,pointRadius:3},{label:'Actual wins',data:[],borderColor:colors[1],borderWidth:3,pointRadius:3,fill:{target:'-1',above:'rgba(63,185,80,.25)',below:'rgba(248,81,73,.25)'}}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{y:{beginAtZero:true,max:Math.max(1,Number(D.completed_week)||0),title:{display:true,text:'Cumulative wins (maximum possible = completed weeks)'}}}}});
function showLuck(team){const r=luckData.get(team);const weeks=r?.weeks||[];luckLine.options.plugins.title.text=team;luckLine.data.labels=['Start',...weeks.map(w=>'W'+w)];luckLine.data.datasets[0].data=[0,...(r?.cum_xw||[])];luckLine.data.datasets[1].data=[0,...(r?.cum_actual||[])];luckLine.update();document.getElementById('luckNote').textContent=r?`Actual ${r.actual.toFixed(1)} · Expected ${r.xw.toFixed(2)} · Luck ${r.luck>=0?'+':''}${r.luck.toFixed(2)} · Expected win rate ${(100*r.xwin_pct).toFixed(1)}%`:'';}const luckButtons=fillTeamButtons('luckTeams',showLuck);
new Chart(document.getElementById('luckBars'),{type:'bar',data:{labels:D.season.map(r=>r.team),datasets:[{data:D.season.map(r=>r.luck),backgroundColor:D.season.map(r=>r.luck>=0?'#3fb950':'#f85149'),borderRadius:4}]},options:{maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{title:{display:true,text:'Actual wins − expected wins'}}}}});
const clutchValues=D.clutch_rankings.map(row=>row.rating),clutchBound=Math.max(1,...clutchValues.map(value=>Math.abs(value)));
new Chart(document.getElementById('clutchChart'),{type:'bar',data:{labels:D.clutch_rankings.map(row=>row.team),datasets:[{label:'Clutch rating',data:clutchValues,backgroundColor:clutchValues.map(value=>value>0?'#3fb950':value<0?'#f85149':'#a7b3c2'),borderRadius:4}]},options:{maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{min:-clutchBound,max:clutchBound,title:{display:true,text:'Clutch rating (higher is better)'}},y:{reverse:true}}}});
const heat=document.getElementById('heat');const hr=document.createElement('tr');['Team',...Array.from({length:D.completed_week},(_,i)=>'W'+(i+1)),'xW','Actual','Luck'].forEach(x=>{const c=document.createElement('th');c.textContent=x;hr.append(c)});heat.append(hr);D.teams.forEach(team=>{const tr=document.createElement('tr');const name=document.createElement('td');name.textContent=team;tr.append(name);(D.heat[team]||[]).forEach(v=>{const td=document.createElement('td');td.textContent=v==null?'—':v.toFixed(2);if(v!=null)td.style.background=`hsl(${120*v},65%,45%)`;tr.append(td)});const r=luckData.get(team)||{xw:0,actual:0,luck:0};[r.xw.toFixed(2),r.actual.toFixed(1),(r.luck>=0?'+':'')+r.luck.toFixed(2)].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td)});heat.append(tr)});

let liveGameActivity=[];const gameActivityPlugin={id:'gameActivity',beforeDatasetsDraw(chart){if(chart.canvas.id!=='liveChart'||!chart.data.labels.length)return;const {ctx,chartArea,scales}=chart,xScale=scales.x;if(!chartArea||!xScale)return;ctx.save();ctx.fillStyle='rgba(167,179,194,.10)';for(let i=0;i<liveGameActivity.length;i++){if(!liveGameActivity[i])continue;const x=xScale.getPixelForValue(i),before=i?xScale.getPixelForValue(i-1):null,after=i<liveGameActivity.length-1?xScale.getPixelForValue(i+1):null,half=(before!==null?Math.abs(x-before)/2:after!==null?Math.abs(after-x)/2:4),left=Math.max(chartArea.left,before===null?x-half:(before+x)/2),right=Math.min(chartArea.right,after===null?x+half:(x+after)/2);ctx.fillRect(left,chartArea.top,Math.max(1,right-left),chartArea.bottom-chartArea.top);}ctx.restore();}};Chart.register(gameActivityPlugin);
let liveChart=new Chart(document.getElementById('liveChart'),{type:'line',data:{labels:[],datasets:[{label:'Home win probability',data:[],borderColor:colors[0],backgroundColor:colors[0],fill:{target:1,above:'rgba(245,166,35,.28)',below:'rgba(77,163,255,.25)'},pointRadius:0,borderWidth:2.5,tension:.18},{label:'50%',data:[],borderColor:'#c9d4de',borderDash:[7,5],borderWidth:1.5,pointRadius:0,fill:false}]},options:{maintainAspectRatio:false,animation:false,plugins:{legend:{display:false},title:{display:false}},interaction:{mode:'index',intersect:false},scales:{y:{min:0,max:100,title:{display:true,text:'Home team win probability (%)'}},x:{ticks:{maxTicksLimit:10,maxRotation:0}}}}});
const multiverse=D.fantasy_multiverse||{},multiverseTeamsHost=document.getElementById('multiverseTeams'),multiverseColors=D.teams.map((_,i)=>`hsl(${Math.round(360*i/Math.max(D.teams.length,1))} 78% 58%)`),multiverseBins=Array.from({length:Math.max(1,(multiverse.weeks_used||[]).length+1)},(_,i)=>i),multiverseCounts=multiverse.distributions||{};
function multiverseProbability(team){const rows=multiverseCounts[team]||[],total=multiverse.simulations||1;return multiverseBins.map(wins=>{const item=rows.find(row=>row.wins===wins);return item?100*item.count/total:0;});}
const multiverseMarker={id:'multiverseActualMarker',afterDraw(chart){const actual=chart.$actualWins;if(actual===undefined||actual===null)return;const x=chart.scales.x.getPixelForValue(actual);if(!Number.isFinite(x))return;const {ctx,chartArea}=chart;ctx.save();ctx.strokeStyle='#f5a623';ctx.lineWidth=2;ctx.setLineDash([7,5]);ctx.beginPath();ctx.moveTo(x,chartArea.top);ctx.lineTo(x,chartArea.bottom);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#f5a623';ctx.font='12px system-ui';ctx.textAlign='center';ctx.fillText('Actual: '+actual,x,chartArea.top+14);ctx.restore();}};Chart.register(multiverseMarker);
let selectedMultiverseTeam=D.teams[0]||'',pinnedMultiverseIndex=null;
const multiverseTeamChart=new Chart(document.getElementById('multiverseTeamChart'),{type:'bar',data:{labels:multiverseBins.map(String),datasets:[{label:'Probability',data:[],backgroundColor:'rgba(77,163,255,.65)',borderColor:'#4da3ff',borderWidth:1}]},options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:item=>item.raw.toFixed(2)+'% of simulated schedules'}}},scales:{x:{title:{display:true,text:'Wins'}},y:{beginAtZero:true,title:{display:true,text:'Schedule probability (%)'}}}}});
function showMultiverseTeam(team){selectedMultiverseTeam=team;const index=D.teams.indexOf(team),probabilities=multiverseProbability(team);multiverseTeamChart.data.datasets[0].data=probabilities;multiverseTeamChart.data.datasets[0].backgroundColor=`${multiverseColors[index]}`;multiverseTeamChart.data.datasets[0].borderColor=multiverseColors[index];multiverseTeamChart.$actualWins=multiverse.actual_wins?.[team]??null;multiverseTeamChart.options.plugins.title={display:true,text:team+' · wins under simulated schedules'};multiverseTeamChart.update();document.getElementById('multiverseTeamLabel').textContent=`Real schedule: ${multiverse.actual_wins?.[team]??0} wins · ${multiverse.simulations||0} schedules`;
 [...multiverseTeamsHost.children].forEach(button=>{const active=button.title===team;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});}
function highlightMultiverseLine(index){const team=index===null?null:D.teams[index];multiverseAllChart.data.datasets.forEach((dataset,i)=>{dataset.borderColor=index===null?multiverseColors[i]:(i===index?multiverseColors[i]:'rgba(160,170,180,.18)');dataset.backgroundColor=dataset.borderColor;dataset.borderWidth=i===index?4:1.5;dataset.pointRadius=i===index?3:0;});multiverseAllChart.$actualWins=team?multiverse.actual_wins?.[team]:null;const label=document.getElementById('multiverseAllLabel');label.textContent=team?`${team} · ${multiverse.actual_wins?.[team]??0} wins on the real schedule`:'Hover over or click a line to highlight a team.';label.style.color=index===null?'var(--muted)':multiverseColors[index];multiverseAllChart.update('none');}
const multiverseAllChart=new Chart(document.getElementById('multiverseAllChart'),{type:'line',data:{labels:multiverseBins.map(String),datasets:D.teams.map((team,index)=>({label:team,data:multiverseProbability(team),borderColor:multiverseColors[index],backgroundColor:multiverseColors[index],borderWidth:1.5,pointRadius:1,pointHoverRadius:5,tension:.18}))},options:{maintainAspectRatio:false,interaction:{mode:'nearest',intersect:false},plugins:{legend:{display:false},tooltip:{callbacks:{label:item=>`${D.teams[item.datasetIndex]}: ${item.raw.toFixed(2)}%`}}},scales:{x:{title:{display:true,text:'Wins'}},y:{beginAtZero:true,title:{display:true,text:'Schedule probability (%)'}}},onHover:(event,elements)=>{if(pinnedMultiverseIndex===null)highlightMultiverseLine(elements[0]?.datasetIndex??null);},onClick:(event,elements)=>{const index=elements[0]?.datasetIndex;if(index===undefined){pinnedMultiverseIndex=null;highlightMultiverseLine(null);}else{pinnedMultiverseIndex=pinnedMultiverseIndex===index?null:index;highlightMultiverseLine(pinnedMultiverseIndex);if(pinnedMultiverseIndex!==null)showMultiverseTeam(D.teams[pinnedMultiverseIndex]);}}}});
D.teams.forEach(team=>makeChoiceButton(multiverseTeamsHost,teamAbbrev(team),team,false,()=>{showMultiverseTeam(team);highlightMultiverseLine(D.teams.indexOf(team));}));
showMultiverseTeam(selectedMultiverseTeam);
function makePieChart(id,counts,title){const entries=D.teams.map(team=>[team,Number(counts[team]||0)]).filter(([,value])=>value>0);return new Chart(document.getElementById(id),{type:'pie',data:{labels:entries.map(([team])=>team),datasets:[{data:entries.map(([,value])=>value),backgroundColor:entries.map(([team])=>multiverseColors[D.teams.indexOf(team)]),borderColor:'#161d26',borderWidth:2}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:title},legend:{position:'bottom'},tooltip:{callbacks:{label:item=>{const total=item.dataset.data.reduce((sum,value)=>sum+value,0);return `${item.label}: ${item.raw.toLocaleString()} (${(100*item.raw/(total||1)).toFixed(1)}%)`;}}}}}});}
makePieChart('multiverseFirstPie',multiverse.first_place_counts||{},'First-place finishes');
const playoffPieCanvas=document.getElementById('multiversePlayoffPie'),multiversePlayoffNote=document.getElementById('multiversePlayoffNote');
if(multiverse.playoff?.finals_complete){multiversePlayoffNote.textContent='Based on completed playoff scoring weeks; semifinal and final matchup frequencies are retained in the generated dashboard data.';makePieChart('multiversePlayoffPie',multiverse.playoff.champions,'Potential playoff titles');}else{playoffPieCanvas.style.display='none';multiversePlayoffNote.textContent='Playoff semifinal, final, and champion schedule outcomes will be calculated as postseason scores become available.';}

const liveWeeksHost=document.getElementById('liveWeeks'),liveMatchupsHost=document.getElementById('liveMatchups'),liveNote=document.getElementById('liveNote'),liveCache={};let selectedLiveWeek='',selectedLiveMatchup=0;function makeChoiceButton(host,label,title,active,onClick){const b=document.createElement('button');b.type='button';b.className='team-button'+(active?' active':'');b.textContent=label;b.title=title;b.setAttribute('aria-label',title);b.setAttribute('aria-pressed',String(active));b.addEventListener('click',onClick);host.append(b);return b;}function fillLiveWeeks(){const keys=(D.live_weeks||[]).map(Number).filter(Number.isFinite).sort((a,b)=>b-a);if(!keys.length){makeChoiceButton(liveWeeksHost,'—','No snapshots recorded',false,()=>{});liveNote.textContent='No snapshots recorded';return;}selectedLiveWeek=String(keys[0]);keys.forEach(w=>makeChoiceButton(liveWeeksHost,'W'+w,'Week '+w+(w===D.current_week?' (current)':''),String(w)===selectedLiveWeek,()=>{selectedLiveWeek=String(w);[...liveWeeksHost.children].forEach((b,i)=>{const active=keys[i]===w;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});selectedLiveMatchup=0;loadLive();}));}
function showLive(){const snap=liveCache[selectedLiveWeek];if(!snap||!snap.matchups){liveNote.textContent='No recorded snapshots for this week.';return;}liveMatchupsHost.replaceChildren();const order=snap.matchups.map((_,i)=>i).sort((a,b)=>{const rank=i=>snap.matchups[i].some(n=>n.toLowerCase()==='league manager')?0:1;return rank(a)-rank(b)||a-b;});order.forEach(i=>{const m=snap.matchups[i],label=teamAbbrev(m[0])+' vs '+teamAbbrev(m[1]);makeChoiceButton(liveMatchupsHost,label,m[0]+' vs '+m[1],i===selectedLiveMatchup,()=>{selectedLiveMatchup=i;showLive();});});if(selectedLiveMatchup>=snap.matchups.length)selectedLiveMatchup=0;const i=selectedLiveMatchup;const rows=(snap.snaps||[]).filter(s=>s[1]?.[i]);const home=snap.matchups[i]?.[0]||'Home',away=snap.matchups[i]?.[1]||'Away';document.getElementById('liveTopTeam').textContent=home;document.getElementById('liveBottomTeam').textContent=away;liveChart.data.labels=rows.map(s=>new Date(s[0]*1000).toLocaleTimeString([],{weekday:'short',hour:'numeric',minute:'2-digit'}));liveGameActivity=rows.map(s=>Boolean(s[1][i][3]));liveChart.data.datasets[0].label=home+' win probability';liveChart.data.datasets[0].data=rows.map(s=>s[1][i][2]);liveChart.data.datasets[1].data=rows.map(()=>50);liveChart.update();liveNote.textContent=rows.length?`${rows.length} snapshots · latest ${new Date(rows.at(-1)[0]*1000).toLocaleString()}`:'Waiting for game snapshots.';}
async function loadLive(){const week=selectedLiveWeek;if(!week||!/^\d+$/.test(week)){showLive();return;}try{const res=await fetch(`live_week_${week}.json?t=${Date.now()}`,{cache:'no-store'});if(res.ok)liveCache[week]=await res.json();}catch(_){}showLive();}
fillLiveWeeks();loadLive();

const ratingByTeam=new Map(D.trade_ratings.map(r=>[r.team_id,r]));const teamIdByName=new Map(D.teams.map(n=>[n,D.roster_team_ids[n]]));
function ratingClass(label){return label==='GM of the Year'?'gm':label==='Fleeced'?'fleeced':'inmix';}
const teamTradeChart=new Chart(document.getElementById('teamTradeChart'),{type:'line',data:{labels:[],datasets:[{label:'Cumulative received - sent',data:[],borderColor:colors[0],fill:{target:{value:0},above:'rgba(63,185,80,.28)',below:'rgba(248,81,73,.28)'},borderWidth:2.5,pointRadius:3,tension:.2},{label:'0 points',data:[],borderColor:'#a7b3c2',borderDash:[6,5],borderWidth:1.5,pointRadius:0,fill:false}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''},legend:{position:'bottom'}},interaction:{mode:'index',intersect:false},scales:{y:{min:-1,max:1,title:{display:true,text:'Cumulative fantasy points'},ticks:{precision:1}},x:{title:{display:true,text:'Week'}}}}});
const individualTradeCharts=new Set();
function renderTrades(selected){
  const tid=teamIdByName.get(selected),rating=ratingByTeam.get(String(tid));
  const badge=document.getElementById('teamRating');badge.className='badge '+ratingClass(rating?.rating||'');
  badge.textContent=rating?`${rating.rating} · Net ${rating.score.toFixed(2)} pts/week · ${rating.wins} wins / ${rating.losses} losses`:'No rating yet';
  const area=document.getElementById('teamTrades');
  individualTradeCharts.forEach(chart=>chart.destroy());individualTradeCharts.clear();area.replaceChildren();
  const list=D.trades.filter(t=>t.sides.some(s=>s.team===selected));
  const weekCount=Math.max(0,Number(D.completed_week)||0),weeklyNet=new Map(Array.from({length:weekCount},(_,i)=>[i+1,0]));
  list.forEach(t=>{const side=t.sides.find(s=>s.team===selected);(side.weekly_points||[]).forEach(row=>weeklyNet.set(Number(row.week),(weeklyNet.get(Number(row.week))||0)+Number(row.net||0)));});const weeklyTotals=[...weeklyNet.values()];let runningNet=0;const cumulativeNet=weeklyTotals.map(points=>runningNet+=points);
  document.getElementById('teamTradeTitle').textContent=`${selected} - cumulative received players minus sent players by week`;
  teamTradeChart.data.labels=[...weeklyNet.keys()].map(w=>'W'+w);
  const yMin=Math.min(0,...cumulativeNet),yMax=Math.max(0,...cumulativeNet);teamTradeChart.options.scales.y.min=yMin===yMax?-1:yMin;teamTradeChart.options.scales.y.max=yMin===yMax?1:yMax;
  teamTradeChart.data.datasets[0].data=cumulativeNet;teamTradeChart.data.datasets[1].data=cumulativeNet.map(()=>0);teamTradeChart.update();
  if(!list.length){const p=document.createElement('p');p.className='muted';p.textContent='No accepted trade records are available for this team.';area.append(p);return;}
  list.sort((a,b)=>b.week-a.week).forEach(t=>{
    const side=t.sides.find(s=>s.team===selected),card=document.createElement('div');card.className='trade-card';
    const layout=document.createElement('div');layout.className='trade-card-layout';
    const info=document.createElement('div');info.className='trade-card-info';
    const h=document.createElement('h3'),title=document.createElement('span');title.textContent=`Week ${t.week} · ${t.date} · ${t.sides.map(s=>s.team).join(' ↔ ')}`;
    const tradeBadge=document.createElement('span');tradeBadge.className='badge trade-rating '+ratingClass(side.rating);tradeBadge.textContent=side.rating;h.append(title,tradeBadge);
    const meta=document.createElement('p');meta.textContent=`Received: ${side.incoming.map(p=>p.name).join(', ')||'—'} (${side.incoming_points.toFixed(1)} pts) · Sent: ${side.outgoing.map(p=>p.name).join(', ')||'—'} (${side.outgoing_points.toFixed(1)} pts)`;
    const score=document.createElement('p');score.className='metric';score.textContent=`Net yield: ${side.net_points>=0?'+':''}${side.net_points.toFixed(1)} pts${side.points_per_week===null?'':` · ${side.points_per_week>=0?'+':''}${side.points_per_week.toFixed(2)} pts/week`}`;
    info.append(h,meta,score);layout.append(info);
    const chartBox=document.createElement('div');chartBox.className='trade-mini-chart';
    const rows=side.weekly_points||[];
    if(rows.length){
      const canvas=document.createElement('canvas');chartBox.append(canvas);
      const chart=new Chart(canvas,{type:'line',data:{labels:rows.map(r=>'W'+r.week),datasets:[{label:'Received players',data:rows.map(r=>r.incoming),borderColor:colors[0],fill:{target:1,above:'rgba(63,185,80,.28)',below:'rgba(248,81,73,.28)'},pointRadius:2,borderWidth:2,tension:.2},{label:'Sent players',data:rows.map(r=>r.outgoing),borderColor:colors[1],pointRadius:2,borderWidth:2,tension:.2}]},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{boxWidth:10,font:{size:10}}},tooltip:{mode:'index',intersect:false}},interaction:{mode:'index',intersect:false},scales:{x:{ticks:{font:{size:9},maxRotation:0}},y:{beginAtZero:true,title:{display:true,text:'Points',font:{size:9}},ticks:{font:{size:9}}}}}});
      individualTradeCharts.add(chart);
    }else{chartBox.className+=' muted';chartBox.textContent='No post-trade scoring weeks yet.';chartBox.style.display='grid';chartBox.style.placeItems='center';chartBox.style.fontSize='12px';}
    layout.append(chartBox);card.append(layout);area.append(card);
  });
}document.getElementById('tradeNotice').textContent=D.trade_warning;document.getElementById('tradeNotice').style.display=D.trade_warning?'block':'none';const tradeButtons=fillTeamButtons('tradeTeams',renderTrades);
// Snapshot refresh: the workflow publishes at five-minute intervals.
setInterval(()=>{if(String(selectedLiveWeek)===String(D.current_week))loadLive();},60000);
</script></body></html>'''.replace("__DATA__", data)


def main():
    espn_s2 = os.getenv("ESPN_S2")
    swid = os.getenv("SWID")
    league = League(league_id=LEAGUE_ID, year=YEAR, espn_s2=espn_s2, swid=swid)
    payload = _build_payload(league)
    output = ROOT / "index.html"
    output.write_text(_html(payload), encoding="utf-8")
    print(f"Saved dashboard: {output}")
    if payload["trade_warning"]:
        print(payload["trade_warning"])


if __name__ == "__main__":
    main()
