"""Build the ESPN fantasy football dashboard for GitHub Pages.

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
    "DRAFT_PICK": "Drafted", "DRAFTPICK": "Drafted",
    "TRADE": "Traded", "TRADED": "Traded",
    "WAIVER": "Waivers", "WAIVERS": "Waivers", "WAIVER_WIRE": "Waivers",
    "WAIVERS_TRADITIONAL": "Waivers", "WAIVERS_ROLLING": "Waivers",
    "WAIVERS_FAAB": "Waivers", "FREE_AGENT": "Waivers",
    "FREEAGENT": "Waivers", "FREE_AGENCY": "Waivers",
    "FREEAGENCY": "Waivers", "FREE_AGENT_ACQUISITION": "Waivers",
    "FA": "Waivers", "ADD": "Waivers",
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
            if source != "Unknown":
                player_id = int(player_id)
                by_team_and_player[(team_id, player_id)] = source
                by_player[player_id] = source
    return by_team_and_player, by_player


def _draft_acquisition_sources(league):
    """Map draft picks to their original fantasy teams when ESPN provides them."""
    result = {}
    try:
        data = league.espn_request.league_get(params={"view": "mDraftDetail"})
    except Exception as exc:
        print(f"Could not load draft picks for acquisition fallback: {exc}")
        return result
    detail = data.get("draftDetail") or {}
    picks = detail.get("picks") or detail.get("draftPicks") or []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        team_id = pick.get("teamId") or pick.get("team_id")
        player_id = pick.get("playerId") or pick.get("player_id")
        if player_id is None:
            player = pick.get("player") or {}
            player_id = player.get("id") if isinstance(player, dict) else None
        if team_id is not None and player_id is not None:
            result[(str(team_id), int(player_id))] = "Drafted"
    return result


def _weekly_transaction_acquisitions(league, max_week):
    """Reconstruct week-by-week acquisition types from ESPN's event history.

    Historical mRoster and BoxPlayer records often omit acquisitionType.
    Draft picks provide the initial ownership, executed waiver/free-agent
    transactions provide adds and drops, and the authenticated activity feed
    supplies accepted trade legs that mTransactions2 omits.
    """
    ownership = _draft_acquisition_sources(league)
    draft_ownership = dict(ownership)
    by_week = {}
    changes_by_week = defaultdict(list)
    date_weeks = defaultdict(list)
    roster_snapshots = {}
    for week in range(1, max_week + 1):
        try:
            roster_data = league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": week})
            if isinstance(roster_data, dict) and isinstance(roster_data.get("teams"), list):
                observed = {}
                for team in roster_data.get("teams", []):
                    team_id = str(team.get("id", ""))
                    roster = team.get("roster") or {}
                    entries = roster.get("entries", []) if isinstance(roster, dict) else roster
                    for entry in entries or []:
                        if not isinstance(entry, dict):
                            continue
                        pool = entry.get("playerPoolEntry") or {}
                        player = pool.get("player") or {}
                        player_id = entry.get("playerId") or pool.get("id") or player.get("id")
                        if player_id is None or not team_id:
                            continue
                        source = _source_bucket(
                            entry.get("acquisitionType") or pool.get("acquisitionType")
                            or player.get("acquisitionType")
                        )
                        observed[(team_id, int(player_id))] = source
                roster_snapshots[week] = observed
        except Exception as exc:
            print(f"Could not reconcile roster history for acquisition week {week}: {exc}")
        try:
            data = league.espn_request.league_get(
                params={"view": "mTransactions2", "scoringPeriodId": week},
                headers={"x-fantasy-filter": json.dumps({"transactions": {
                    "filterType": {"value": ["TRADE_ACCEPT", "WAIVER", "FREEAGENT"]}
                }})},
            )
        except Exception as exc:
            print(f"Could not load acquisition transactions for week {week}: {exc}")
            continue
        for transaction in data.get("transactions", []) if isinstance(data, dict) else []:
            if not isinstance(transaction, dict):
                continue
            tx_type = str(transaction.get("type", "")).upper()
            status = str(transaction.get("status", "")).upper()
            try:
                date = int(transaction.get("processDate") or transaction.get("acceptedDate")
                           or transaction.get("proposedDate") or transaction.get("date") or 0)
            except (TypeError, ValueError):
                date = 0
            if tx_type == "TRADE_ACCEPT" and date:
                # Keep dates even when ESPN strips the accepted trade's player
                # items; recent_activity uses these to assign the trade week.
                date_weeks[week].append(date)
            if status not in {"EXECUTED", "PROCESSED"}:
                continue
            if tx_type not in {"TRADE_ACCEPT", "WAIVER", "FREEAGENT"}:
                continue
            for item in transaction.get("items", []) or []:
                if not isinstance(item, dict):
                    continue
                player_id = item.get("playerId") or item.get("player_id")
                if player_id is None:
                    continue
                try:
                    player_id = int(player_id)
                    from_id = str(item.get("fromTeamId") or item.get("from_team_id") or "")
                    to_id = str(item.get("toTeamId") or item.get("to_team_id")
                                 or transaction.get("teamId") or transaction.get("team_id") or "")
                except (TypeError, ValueError):
                    continue
                item_type = str(item.get("type", "")).upper()
                if tx_type == "TRADE_ACCEPT" and item_type == "TRADE" and from_id and to_id:
                    changes_by_week[week].append((date, "trade", [
                        {"id": player_id, "from": from_id, "to": to_id}
                    ]))
                elif tx_type in {"WAIVER", "FREEAGENT"}:
                    if item_type in {"DROP", "DROPPED"} and from_id and from_id != "0":
                        changes_by_week[week].append((date, "drop", player_id, from_id))
                    elif item_type in {"ADD", "ADDED"} and to_id and to_id != "0":
                        changes_by_week[week].append((date, "add", player_id, to_id))

    # TRADE_ACCEPT is present in public ESPN transaction responses, but its
    # items are commonly blank. The authenticated ACTIVITY_TRANSACTIONS feed
    # contains the paired team/player actions for accepted deals.
    try:
        activity_trades, _ = _activity_trade_rows(league, date_weeks)
    except Exception as exc:
        print(f"Could not load accepted trade movements for acquisition history: {exc}")
        activity_trades = []
    for trade in activity_trades:
        changes_by_week[int(trade["week"])].append(
            (int(trade.get("date_ms") or 0), "trade", trade["legs"])
        )

    for week in range(1, max_week + 1):
        for change in sorted(changes_by_week.get(week, []), key=lambda row: row[0]):
            date, action = change[0], change[1]
            if action == "drop":
                _, _, player_id, from_id = change
                ownership.pop((from_id, player_id), None)
            elif action == "add":
                _, _, player_id, to_id = change
                ownership[(to_id, player_id)] = "Waivers"
            elif action == "trade":
                for leg in change[2]:
                    player_id = int(leg["id"])
                    from_id, to_id = str(leg["from"]), str(leg["to"])
                    if from_id and from_id != "0":
                        ownership.pop((from_id, player_id), None)
                    if to_id and to_id != "0":
                        ownership[(to_id, player_id)] = "Traded"
        # Reconcile the event replay with ESPN's actual roster snapshot. It
        # catches add/drop movements that the transaction endpoint omits and
        # prevents stale ownership from leaking into later scoring weeks.
        if week in roster_snapshots:
            observed = roster_snapshots[week]
            reconciled = {}
            for key, explicit_source in observed.items():
                source = explicit_source if explicit_source != "Unknown" else ownership.get(key, "Unknown")
                if source == "Unknown":
                    source = draft_ownership.get(key, "Waivers")
                reconciled[key] = source
            ownership = reconciled
        by_week[week] = dict(ownership)
    return by_week


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
    if team_count < 2:
        return {"simulations": 0, "weeks_used": [], "actual_wins": actual_wins,
                "distributions": {}, "first_place_counts": {},
                "playoff": {"semifinals_complete": False, "finals_complete": False,
                            "semifinal_matchups": {}, "final_matchups": {}, "champions": {}}}
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
        # Every draw needs a fresh random opponent schedule. Reusing one
        # round-robin across all iterations collapses the win distributions.
        base_rounds = _round_robin_rounds(team_count, rng)
        repeats = max(0, regular_season_count - len(base_rounds))
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
    champion_mode = bool(champion_counts)
    return {"simulations": samples, "weeks_used": complete_weeks, "actual_wins": actual_wins,
            "distributions": dist, "first_place_counts": dict(champion_counts if champion_mode else first_counts),
            "first_place_label": "Potential playoff titles" if champion_mode else "First-place finishes",
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


def _slot_positions(slot):
    label = str(slot or "").upper().replace("D/ST", "DEF").strip()
    aliases = {
        "FLEX": {"RB", "WR", "TE"},
        "RB/WR": {"RB", "WR"}, "WR/RB": {"RB", "WR"},
        "WR/TE": {"WR", "TE"}, "TE/WR": {"WR", "TE"},
        "RB/WR/TE": {"RB", "WR", "TE"}, "WR/RB/TE": {"RB", "WR", "TE"},
        "OP": {"QB", "RB", "WR", "TE"},
        "SUPERFLEX": {"QB", "RB", "WR", "TE"},
    }
    if label in aliases:
        return aliases[label]
    if "/" in label:
        return {POSITIONS.get(part.strip(), part.strip()) for part in label.split("/")}
    return {POSITIONS.get(label, label)} if label else set()


def _eligible_for_lineup_slot(player, slot):
    position = POSITIONS.get(str(getattr(player, "position", "")).upper(), str(getattr(player, "position", "")).upper())
    eligible = getattr(player, "eligibleSlots", []) or []
    eligible_positions = {position}
    for eligible_slot in eligible:
        eligible_positions.update(_slot_positions(eligible_slot))
    return bool(eligible_positions & _slot_positions(slot))


def _optimal_lineup_points(lineup):
    """Maximize weekly points while filling the same starter-slot mix ESPN used."""
    players = [player for player in (lineup or []) if getattr(player, "slot_position", "") != "IR"]
    slots = [str(getattr(player, "slot_position", "") or "")
             for player in players if getattr(player, "slot_position", "") not in ("BE", "IR", "")]
    if not slots:
        return sum(_safe_float(getattr(player, "points", 0)) for player in players)

    # Dynamic programming across roster players and the league's actual starter slots.
    # A bitmask prevents the same player from filling two positions.
    states = {0: 0.0}
    for slot in slots:
        next_states = {}
        for used, total in states.items():
            for index, player in enumerate(players):
                bit = 1 << index
                if used & bit or not _eligible_for_lineup_slot(player, slot):
                    continue
                mask = used | bit
                score = total + _safe_float(getattr(player, "points", 0))
                if score > next_states.get(mask, float("-inf")):
                    next_states[mask] = score
        if not next_states:
            return sum(_safe_float(getattr(player, "points", 0))
                       for player in players if getattr(player, "slot_position", "") not in ("BE", "IR"))
        states = next_states
    return max(states.values(), default=0.0)


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


def _player_bye_week(player):
    explicit = getattr(player, "bye_week", None) or getattr(player, "byeWeek", None)
    try:
        if explicit:
            return int(explicit)
    except (TypeError, ValueError):
        pass
    schedule = getattr(player, "schedule", None) or {}
    if isinstance(schedule, dict):
        played_weeks = set()
        for key, game in schedule.items():
            try:
                week = int(key)
            except (TypeError, ValueError):
                continue
            if game:
                played_weeks.add(week)
    elif isinstance(schedule, (list, tuple)):
        played_weeks = {index + 1 for index, game in enumerate(schedule) if game}
    else:
        played_weeks = set()
    return next((week for week in range(1, 19) if week not in played_weeks), None) if played_weeks else None


def _current_rosters(league, completed_week, max_week, acquisition_history,
                     player_weekly_history, player_scoring_history):
    slot_names = {
        "0": "QB", "2": "RB", "4": "WR", "6": "TE", "16": "D/ST",
        "17": "K", "20": "BE", "21": "IR", "23": "FLEX", "24": "OP",
        "25": "DE", "26": "DT", "27": "LB", "28": "CB", "29": "S",
        "30": "DB", "31": "DP", "32": "DL",
    }
    slot_order = {name: index for index, name in enumerate((
        "QB", "RB", "WR", "TE", "FLEX", "OP", "K", "D/ST", "BE", "IR",
        "DE", "DT", "LB", "CB", "S", "DB", "DP", "DL",
    ))}

    def slot_label(player):
        raw = getattr(player, "lineupSlot", None)
        if raw is None or str(raw).strip() == "":
            raw = getattr(player, "slot_position", None)
        if raw is None or str(raw).strip() == "":
            raw = "BE"
        label = str(raw).strip().upper()
        return slot_names.get(label, label)

    result = {}
    for team in league.teams:
        players = []
        team_id = str(team.team_id)
        for player in getattr(team, "roster", []) or []:
            player_id = int(getattr(player, "playerId", 0) or 0)
            all_week_rows = dict(player_scoring_history.get(player_id, {}))
            player_stats = getattr(player, "stats", {}) or {}
            if isinstance(player_stats, dict):
                for week in range(1, int(completed_week) + 1):
                    weekly = player_stats.get(week, player_stats.get(str(week), {}))
                    if not isinstance(weekly, dict):
                        continue
                    actual = weekly.get("points")
                    if actual is None:
                        continue
                    projected = weekly.get("projected_points", weekly.get("projectedPoints"))
                    all_week_rows[week] = {
                        "actual": _safe_float(actual),
                        "projected": _safe_float(projected) if projected is not None and _safe_float(projected) > 0 else None,
                        "played": not bool(getattr(player, "on_bye_week", False)) and _player_bye_week(player) != week,
                    }
            weekly_rows = [all_week_rows[week] for week in sorted(all_week_rows)]
            actual_scores = [row["actual"] for row in weekly_rows if row["played"]]
            projection_residuals = [
                row["actual"] - row["projected"]
                for row in weekly_rows
                if row["played"] and row["projected"] is not None
            ]
            # Use the full league-wide weekly history for the current rostered
            # player, regardless of which fantasy team owned them that week.
            # Bye weeks are excluded when the weekly rows are collected.
            ppg = sum(actual_scores) / len(actual_scores) if actual_scores else 0.0
            points_vs_projection_ppg = (
                sum(projection_residuals) / len(projection_residuals) if projection_residuals else None
            )

            def sample_variance(values):
                if len(values) < 2:
                    return None
                mean = sum(values) / len(values)
                return sum((value - mean) ** 2 for value in values) / (len(values) - 1)

            projection_variance = sample_variance(projection_residuals)
            raw_variance = sample_variance(actual_scores)
            team_player_history = player_weekly_history.get((team_id, player_id), [])
            started_points = sum(row["points"] for row in team_player_history if row["started"])
            nonstarter_points = sum(row["points"] for row in team_player_history if not row["started"])
            team_points = started_points + nonstarter_points
            realized_pct = 100.0 * started_points / team_points if team_points > 0 else None

            ownership_rows = [
                (week, acquisition_history.get(week, {}).get((team_id, player_id)))
                for week in range(1, max(0, int(max_week)) + 1)
                if (team_id, player_id) in acquisition_history.get(week, {})
            ]
            acquisition = ownership_rows[-1][1] if ownership_rows else _source_bucket(
                getattr(player, "acquisitionType", "")
            )
            if acquisition == "Unknown":
                acquisition = "Waivers"
            acquisition_week = None
            if ownership_rows and acquisition != "Drafted":
                latest_week = ownership_rows[-1][0]
                acquisition_week = latest_week
                for week in range(latest_week - 1, 0, -1):
                    previous_source = acquisition_history.get(week, {}).get((team_id, player_id))
                    if previous_source != acquisition:
                        break
                    acquisition_week = week
            pos_rank = getattr(player, "posRank", None) or getattr(player, "position_rank", None)
            try:
                pos_rank = int(pos_rank) if pos_rank is not None else None
            except (TypeError, ValueError):
                pos_rank = None
            nfl_team = getattr(player, "proTeam", None) or getattr(player, "pro_team", None) or "—"
            nfl_logo_slugs = {
                "ARI":"ari", "ATL":"atl", "BAL":"bal", "BUF":"buf", "CAR":"car", "CHI":"chi",
                "CIN":"cin", "CLE":"cle", "DAL":"dal", "DEN":"den", "DET":"det", "GB":"gb",
                "HOU":"hou", "IND":"ind", "JAX":"jax", "JAC":"jax", "KC":"kc", "LAC":"lac",
                "LAR":"lar", "LV":"lv", "MIA":"mia", "MIN":"min", "NE":"ne", "NO":"no",
                "NYG":"nyg", "NYJ":"nyj", "PHI":"phi", "PIT":"pit", "SEA":"sea", "SF":"sf",
                "TB":"tb", "TEN":"ten", "WAS":"wsh", "WSH":"wsh",
                "ARIZONA CARDINALS":"ari", "ATLANTA FALCONS":"atl", "BALTIMORE RAVENS":"bal",
                "BUFFALO BILLS":"buf", "CAROLINA PANTHERS":"car", "CHICAGO BEARS":"chi",
                "CINCINNATI BENGALS":"cin", "CLEVELAND BROWNS":"cle", "DALLAS COWBOYS":"dal",
                "DENVER BRONCOS":"den", "DETROIT LIONS":"det", "GREEN BAY PACKERS":"gb",
                "HOUSTON TEXANS":"hou", "INDIANAPOLIS COLTS":"ind", "JACKSONVILLE JAGUARS":"jax",
                "KANSAS CITY CHIEFS":"kc", "LAS VEGAS RAIDERS":"lv", "LOS ANGELES CHARGERS":"lac",
                "LOS ANGELES RAMS":"lar", "MIAMI DOLPHINS":"mia", "MINNESOTA VIKINGS":"min",
                "NEW ENGLAND PATRIOTS":"ne", "NEW ORLEANS SAINTS":"no", "NEW YORK GIANTS":"nyg",
                "NEW YORK JETS":"nyj", "PHILADELPHIA EAGLES":"phi", "PITTSBURGH STEELERS":"pit",
                "SAN FRANCISCO 49ERS":"sf", "SEATTLE SEAHAWKS":"sea", "TAMPA BAY BUCCANEERS":"tb",
                "TENNESSEE TITANS":"ten", "WASHINGTON COMMANDERS":"wsh",
            }
            nfl_team_key = str(nfl_team).strip().upper()
            nfl_slug = nfl_logo_slugs.get(nfl_team_key)
            if not nfl_slug and str(getattr(player, "position", "")).upper() in ("D/ST", "DST", "DEF"):
                player_name_key = str(getattr(player, "name", "")).upper()
                nfl_slug = next((slug for name, slug in nfl_logo_slugs.items() if len(name) > 3 and name in player_name_key), None)
            players.append({
                "id": player_id,
                "name": str(getattr(player, "name", "Unknown player")),
                "position": str(getattr(player, "position", "—")),
                "slot": slot_label(player),
                "ppg": round(ppg, 2),
                "points_vs_projection_ppg": round(points_vs_projection_ppg, 2) if points_vs_projection_ppg is not None else None,
                "projection_sd": round(math.sqrt(projection_variance), 2) if projection_variance is not None else None,
                "raw_sd": round(math.sqrt(raw_variance), 2) if raw_variance is not None else None,
                "score_games": len(actual_scores),
                "projection_games": len(projection_residuals),
                "realized_pct": round(realized_pct, 1) if realized_pct is not None else None,
                "started_points": round(started_points, 2),
                "nonstarter_points": round(nonstarter_points, 2),
                "acquisition": acquisition,
                "acquisition_week": acquisition_week if acquisition != "Drafted" else None,
                "pos_rank": pos_rank,
                "bye_week": _player_bye_week(player),
                "nfl_team": str(nfl_team),
                "photo_url": f"https://a.espncdn.com/i/headshots/nfl/players/full/{player_id}.png" if player_id > 0 else "",
                "team_logo_url": f"https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/{nfl_slug}.png" if nfl_slug else "",
                "projected_remaining": round(max(
                    0.0,
                    _safe_float(getattr(player, "projected_total_points", 0))
                    - _safe_float(getattr(player, "total_points", 0)),
                ), 1),
            })
        # Keep every starter above the bench so the table can draw one clear
        # divider even when the league uses defensive individual player slots.
        players.sort(key=lambda item: (
            item["slot"] in ("BE", "IR"),
            slot_order.get(item["slot"], 100),
            item["name"].casefold(),
        ))
        result[str(team.team_id)] = players
    return result


NFL_WEEK_DATES = {
    14: ("2026-12-11", "2026-12-15"),
    15: ("2026-12-18", "2026-12-22"),
    16: ("2026-12-25", "2026-12-30"),
    17: ("2027-01-01", "2027-01-06"),
}


def _league_schedule_metadata(league, fallback_regular_weeks):
    """Read matchup periods and playoff rounds from ESPN's league settings."""
    try:
        data = league.espn_request.league_get(params={"view": "mSettings"})
        settings = (data.get("settings") or {}).get("scheduleSettings") or {}
    except Exception as exc:
        print(f"Could not read playoff period dates from ESPN settings: {exc}")
        settings = {}

    matchup_periods = settings.get("matchupPeriods") or {}
    try:
        regular_period_count = int(settings.get("matchupPeriodCount") or fallback_regular_weeks)
    except (TypeError, ValueError):
        regular_period_count = fallback_regular_weeks
    regular_weeks = sorted({
        int(week)
        for period, weeks in matchup_periods.items()
        if str(period).isdigit() and int(period) <= regular_period_count
        for week in (weeks or [])
    })
    regular_count = max(regular_weeks, default=int(fallback_regular_weeks))

    def date_label(weeks):
        dates = [NFL_WEEK_DATES[week] for week in weeks if week in NFL_WEEK_DATES]
        if not dates:
            return "dates set by NFL schedule"
        first = datetime.strptime(dates[0][0], "%Y-%m-%d").date()
        last = datetime.strptime(dates[-1][1], "%Y-%m-%d").date()
        if first.year == last.year:
            return f"{first.strftime('%b')} {first.day}–{last.strftime('%b')} {last.day}, {last.year}"
        return f"{first.strftime('%b')} {first.day}, {first.year}–{last.strftime('%b')} {last.day}, {last.year}"

    playoff_rounds = []
    playoff_periods = sorted(
        ((int(period), [int(week) for week in weeks])
         for period, weeks in matchup_periods.items()
         if str(period).isdigit() and int(period) > regular_period_count),
        key=lambda item: item[0],
    )
    for index, (period, weeks) in enumerate(playoff_periods):
        weeks = sorted(weeks)
        if index == 0:
            label = "Semifinals" if len(playoff_periods) == 2 else "Playoff Round 1"
        elif index == len(playoff_periods) - 1:
            label = "Championship and placement" if len(playoff_periods) == 2 else "Championship and placement"
        else:
            label = f"Playoff Round {index + 1}"
        playoff_rounds.append({
            "period": period,
            "label": label,
            "nfl_weeks": weeks,
            "date_range": date_label(weeks),
        })
    try:
        playoff_team_count = max(0, int(settings.get("playoffTeamCount") or 0))
    except (TypeError, ValueError):
        playoff_team_count = 0
    divisions = settings.get("divisions") or []
    return {
        "regular_season_count": regular_count,
        "playoff_team_count": playoff_team_count,
        "playoff_rounds": playoff_rounds,
        "division_count": len(divisions),
        "playoff_seeding_rule": settings.get("playoffSeedingRule"),
        "divisions": divisions,
        "matchup_periods": matchup_periods,
    }


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
            moved = {"id": leg["id"], "name": name,
                     "photo_url": f"https://a.espncdn.com/i/headshots/nfl/players/full/{leg['id']}.png",
                     "points": round(points_after, 2),
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
    schedule_meta = _league_schedule_metadata(league, regular_season_count)
    regular_season_count = schedule_meta["regular_season_count"]
    current_week = int(league.current_week)
    max_week = min(max(current_week, 1), regular_season_count)
    completed_week = min(max(int(league.current_week) - 1, 0), max_week)
    score_rows = []
    position_week = defaultdict(lambda: defaultdict(lambda: {g: 0.0 for g in GROUPS}))
    position_totals = defaultdict(lambda: {g: 0.0 for g in GROUPS})
    source_week = defaultdict(lambda: defaultdict(lambda: {source: 0.0 for source in SOURCES}))
    starter_bench_week = defaultdict(dict)
    postseason_score_rows = []
    source_audit = Counter()
    acquisition_history = _weekly_transaction_acquisitions(league, max_week)
    player_weekly_history = defaultdict(list)
    player_scoring_history = defaultdict(dict)

    for week in range(1, max_week + 1):
        matchups = league.box_scores(week)
        id_to_source, id_only_source = {}, {}
        try:
                    id_to_source, _ = _week_acquisition_sources(league, week)
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
                {"week": week, "team": home_name, "opponent": away_name, "score": home_score, "opp": away_score},
                {"week": week, "team": away_name, "opponent": home_name, "score": away_score, "opp": home_score},
            ])
            week_scores.extend([(home_name, home_score, away_score), (away_name, away_score, home_score)])
            for tid, name, lineup in (
                (home_id, home_name, getattr(box, "home_lineup", [])),
                (away_id, away_name, getattr(box, "away_lineup", [])),
            ):
                if week <= completed_week:
                    actual_points = home_score if name == home_name else away_score
                    optimal_points = _optimal_lineup_points(lineup)
                    bench_points = sum(
                        _safe_float(getattr(player, "points", 0))
                        for player in lineup or []
                        if getattr(player, "slot_position", "") == "BE"
                    )
                    starter_bench_week[name][week] = {
                        "actual": actual_points,
                        "bench": bench_points,
                        "optimal": max(actual_points, optimal_points),
                        "left": max(0.0, optimal_points - actual_points),
                    }
                for player in lineup or []:
                    slot = getattr(player, "slot_position", "")
                    points = _safe_float(getattr(player, "points", 0))
                    player_id = int(getattr(player, "playerId", 0) or 0)
                    if week <= completed_week and player_id > 0:
                        player_stats = getattr(player, "stats", {}) or {}
                        weekly_stats = (
                            player_stats.get(week, player_stats.get(str(week), {}))
                            if isinstance(player_stats, dict) else {}
                        )
                        weekly_stats = weekly_stats if isinstance(weekly_stats, dict) else {}
                        projected_value = weekly_stats.get(
                            "projected_points", weekly_stats.get("projectedPoints")
                        )
                        if projected_value is None:
                            projected_value = getattr(player, "projected_points", None)
                        projected_value = _safe_float(projected_value) if projected_value is not None else None
                        # ESPN uses a zero default when a weekly projection is absent.
                        # Treat that as missing so it cannot inflate the projection gap.
                        if projected_value is not None and projected_value <= 0:
                            projected_value = None
                        actual_value = weekly_stats.get("points")
                        actual_value = _safe_float(actual_value) if actual_value is not None else points
                        player_scoring_history[player_id].setdefault(week, {
                            "actual": actual_value,
                            "projected": projected_value,
                            "played": not bool(getattr(player, "on_bye_week", False))
                            and _player_bye_week(player) != week,
                        })
                        player_weekly_history[(str(tid), player_id)].append({
                            "week": week,
                            "points": points,
                            "started": slot not in ("BE", "IR", ""),
                        })
                    pos = _bucket(getattr(player, "position", ""), slot)
                    if pos is None:
                        continue
                    position_week[name][week][pos] += points
                    position_totals[name][pos] += points
                    source = (
                        acquisition_history.get(week, {}).get((str(tid), player_id))
                        or id_to_source.get((str(tid), player_id))
                        or _source_bucket(getattr(player, "acquisitionType", ""))
                    )
                    # Any rostered player still lacking an explicit ESPN label,
                    # draft pick, or transaction is treated as a free-agent / waiver
                    # pickup in this standard redraft league instead of showing Unknown.
                    if source == "Unknown":
                        source = "Waivers"
                        source_audit["assumed_waivers"] += 1
                    else:
                        source_audit[source] += 1
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

    current_rosters = _current_rosters(
        league, completed_week, max_week, acquisition_history,
        player_weekly_history, player_scoring_history,
    )
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

    # Score each result against that week's scoring spread. A win earns quality
    # credit only when the opponent scored above average; a loss is penalized
    # only when the opponent scored below average. Opponent record entering the
    # game adds a modest 0.75–1.25 multiplier.
    quality_by_week = defaultdict(lambda: defaultdict(float))
    quality_rows = {name: {"win_z": [], "loss_z": [], "opp_wins": [], "opp_losses": []} for name in labels}
    if not completed.empty:
        league_by_week = completed.groupby("week")["score"].agg(["mean", "std"])
        for row in completed.sort_values(["week", "team"]).itertuples():
            avg = float(league_by_week.loc[row.week, "mean"])
            spread = float(league_by_week.loc[row.week, "std"])
            z = (float(row.opp) - avg) / (spread if spread and spread > 0 else 1.0)
            previous_opponent_rows = completed[
                (completed["team"] == row.opponent) & (completed["week"] < row.week)
            ]
            if previous_opponent_rows.empty:
                opponent_pct = 0.5
            else:
                opponent_pct = float(previous_opponent_rows["actual"].mean())
            opponent_weight = 0.75 + 0.5 * opponent_pct
            quality = 0.0
            if row.actual == 1:
                quality = max(z, 0.0) * opponent_weight
                quality_rows[row.team]["win_z"].append(max(z, 0.0) * opponent_weight)
                quality_rows[row.team]["opp_wins"].append(float(row.opp))
            elif row.actual == 0:
                quality = -max(-z, 0.0) * opponent_weight
                quality_rows[row.team]["loss_z"].append(-max(-z, 0.0) * opponent_weight)
                quality_rows[row.team]["opp_losses"].append(float(row.opp))
            quality_by_week[row.team][int(row.week)] += quality

    quality_rankings = []
    for name in labels:
        values = quality_rows[name]
        win_quality = sum(values["win_z"]) / len(values["win_z"]) if values["win_z"] else 0.0
        loss_quality = sum(values["loss_z"]) / len(values["loss_z"]) if values["loss_z"] else 0.0
        quality_rankings.append({
            "team": name,
            "opponent_ppg_wins": round(sum(values["opp_wins"]) / len(values["opp_wins"]), 2) if values["opp_wins"] else None,
            "opponent_ppg_losses": round(sum(values["opp_losses"]) / len(values["opp_losses"]), 2) if values["opp_losses"] else None,
            "win_quality": round(win_quality, 3),
            "loss_quality": round(loss_quality, 3),
            "rating": round(win_quality + loss_quality, 3),
        })
    quality_rankings.sort(key=lambda row: (-row["rating"], row["team"].lower()))
    for rank, record in enumerate(quality_rankings, 1):
        record["rank"] = rank

    def normalized(values, value):
        low, high = min(values, default=0.0), max(values, default=0.0)
        return (float(value) - low) / (high - low) if high > low else 0.5

    def power_rankings_for(through_week):
        through = completed[completed["week"] <= through_week] if not completed.empty else pd.DataFrame()
        team_rows = {
            name: (through[through["team"] == name].sort_values("week") if not through.empty else pd.DataFrame())
            for name in labels
        }
        overall_points = {name: float(rows["score"].sum()) if not rows.empty else 0.0 for name, rows in team_rows.items()}
        recent_rows = {name: rows.tail(3) for name, rows in team_rows.items()}
        recent_points = {name: float(rows["score"].sum()) if not rows.empty else 0.0 for name, rows in recent_rows.items()}
        elo_raw = {name: sum(value for game_week, value in quality_by_week[name].items() if game_week <= through_week)
                   for name in labels}
        overall_pct = {}
        recent_pct = {}
        recent_wins = {}
        recent_losses = {}
        recent_ties = {}
        for name in labels:
            rows = team_rows[name]
            overall_pct[name] = float(rows["actual"].mean()) if not rows.empty else 0.0
            recent = recent_rows[name]
            recent_pct[name] = float(recent["actual"].mean()) if not recent.empty else 0.0
            recent_wins[name] = int((recent["actual"] == 1).sum()) if not recent.empty else 0
            recent_ties[name] = int((recent["actual"] == 0.5).sum()) if not recent.empty else 0
            recent_losses[name] = len(recent) - recent_wins[name] - recent_ties[name]
        point_values = list(overall_points.values())
        recent_values = list(recent_points.values())
        elo_values = list(elo_raw.values())
        rankings = []
        for name in labels:
            parts = {
                "overall_record_score": overall_pct[name],
                "overall_points_score": normalized(point_values, overall_points[name]),
                "recent_record_score": recent_pct[name],
                "recent_points_score": normalized(recent_values, recent_points[name]),
                "elo_score": normalized(elo_values, elo_raw[name]),
            }
            score = 100 * (
                0.20 * parts["overall_record_score"]
                + 0.10 * parts["overall_points_score"]
                + 0.30 * parts["recent_record_score"]
                + 0.20 * parts["recent_points_score"]
                + 0.20 * parts["elo_score"]
            )
            rankings.append({
                "team": name, "score": round(score, 2),
                "overall_pct": round(overall_pct[name], 3),
                "overall_points": round(overall_points[name], 2),
                "recent_wins": recent_wins[name], "recent_losses": recent_losses[name],
                "recent_ties": recent_ties[name], "recent_games": len(recent_rows[name]),
                "recent_points": round(recent_points[name], 2),
                "elo_raw": round(elo_raw[name], 3), **{key: round(value, 3) for key, value in parts.items()},
            })
        rankings.sort(key=lambda row: (-row["score"], row["team"].lower()))
        for rank, record in enumerate(rankings, 1):
            record["rank"] = rank
        return rankings

    power_rankings = power_rankings_for(completed_week)
    power_history = [
        {"week": week, "teams": power_rankings_for(week)}
        for week in range(1, completed_week + 1)
    ]

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
    if not playoff_team_count:
        playoff_team_count = max(0, min(len(labels), int(schedule_meta.get("playoff_team_count") or 0)))
    division_by_team = {
        str(team.team_name): getattr(team, "division_id", None)
        for team in teams
    }
    # ESPN leagues with divisions award playoff seeds to division winners
    # before assigning the remaining seeds by the league standings order.
    # This keeps the projected position and bracket aligned for those leagues.
    if playoff_team_count and schedule_meta.get("division_count", 0) > 1:
        rank_by_team = {row["team"]: rank for rank, row in enumerate(bracket_records, 1)}
        division_groups = defaultdict(list)
        for record in bracket_records:
            division_id = division_by_team.get(record["team"])
            if division_id is not None:
                division_groups[str(division_id)].append(record)
        division_winners = [
            min(group, key=lambda row: rank_by_team.get(row["team"], len(labels) + 1))
            for group in division_groups.values()
            if group
        ]
        division_winners.sort(key=lambda row: rank_by_team.get(row["team"], len(labels) + 1))
        # A custom league can have more divisions than playoff spots. In that
        # case only as many division winners as available spots can be promoted.
        division_winners = division_winners[:playoff_team_count]
        winners_by_name = {row["team"]: row for row in division_winners}
        bracket_records = sorted(
            division_winners,
            key=lambda row: rank_by_team.get(row["team"], len(labels) + 1),
        ) + [row for row in bracket_records if row["team"] not in winners_by_name]
    playoff_position_by_team = {
        row["team"]: position for position, row in enumerate(bracket_records, 1)
    }
    for row in standings:
        row["playoff_position"] = playoff_position_by_team.get(row["team"])
    max_final = {}
    current_record_points = {}
    for record in bracket_records:
        remaining = max(0, regular_season_count - int(record.get("games", 0)))
        current_record_points[record["team"]] = float(record["wins"]) + 0.5 * float(record["ties"])
        max_final[record["team"]] = current_record_points[record["team"]] + remaining
    clinching_by_team = {}
    next_bracket_size = 1
    while next_bracket_size < max(1, playoff_team_count):
        next_bracket_size *= 2
    first_round_byes = max(0, next_bracket_size - playoff_team_count)
    for record in bracket_records:
        name = record["team"]
        rivals_matching_floor = sum(
            1 for rival in bracket_records if rival["team"] != name
            and max_final[rival["team"]] >= current_record_points[name]
        )
        teams_certainly_ahead = sum(
            1 for rival in bracket_records if rival["team"] != name
            and current_record_points[rival["team"]] > max_final[name]
        )
        markers = []
        is_eliminated = bool(getattr(next((team for team in teams if str(team.team_name) == name), None), "eliminated", False))
        division_id = division_by_team.get(name)
        division_rivals = [row for row in bracket_records
                           if row["team"] != name and division_by_team.get(row["team"]) == division_id]
        has_divisions = schedule_meta.get("division_count", 0) > 1 and division_id is not None
        division_clinched = has_divisions and all(
            current_record_points[name] > max_final[row["team"]] for row in division_rivals
        )
        division_still_possible = has_divisions and not any(
            current_record_points[row["team"]] > max_final[name] for row in division_rivals
        )
        division_guarantees_berth = (
            division_clinched
            and schedule_meta.get("division_count", 0) <= playoff_team_count
        )
        mathematically_eliminated = (
            playoff_team_count and teams_certainly_ahead >= playoff_team_count
            and not (division_still_possible and schedule_meta.get("division_count", 0) <= playoff_team_count)
        )
        if is_eliminated or mathematically_eliminated:
            markers.append("e")
        else:
            if playoff_team_count and (rivals_matching_floor < playoff_team_count or division_guarantees_berth):
                markers.append("x")
            if division_clinched:
                markers.append("y")
            if playoff_team_count and first_round_byes and rivals_matching_floor < first_round_byes:
                markers.append("z")
        clinching_by_team[name] = markers
    for row in standings:
        row["clinching"] = clinching_by_team.get(row["team"], [])
    playoff_teams = [{"seed": seed, "team": record["team"], "record": f'{record["wins"]}-{record["losses"]}-{record["ties"]}'}
                     for seed, record in enumerate(bracket_records[:playoff_team_count], 1)]
    consolation_teams = [{"seed": seed, "team": record["team"], "record": f'{record["wins"]}-{record["losses"]}-{record["ties"]}'}
                         for seed, record in enumerate(bracket_records[playoff_team_count:], playoff_team_count + 1)]

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
    source_totals = {
        name: {source: round(sum(source_week[name][week][source] for week in weeks_for_stats), 2)
               for source in SOURCES}
        for name in labels
    }
    print(
        "Acquisition source audit: "
        + ", ".join(f"{source}={source_audit.get(source, 0)}" for source in SOURCES)
        + f", assumed-waiver fallback={source_audit.get('assumed_waivers', 0)}"
    )
    starter_bench_weekly = {
        name: [{"week": week, **{key: round(value, 2) for key, value in row.items()}}
               for week, row in sorted(starter_bench_week[name].items())]
        for name in labels
    }
    starter_bench_totals = {}
    for name in labels:
        rows = starter_bench_weekly[name]
        games = len(rows)
        actual = sum(row["actual"] for row in rows)
        bench = sum(row["bench"] for row in rows)
        optimal = sum(row["optimal"] for row in rows)
        left = sum(row["left"] for row in rows)
        starter_bench_totals[name] = {
            "games": games,
            "actual": round(actual, 2),
            "bench": round(bench, 2),
            "optimal": round(optimal, 2),
            "left": round(left, 2),
            "left_ppg": round(left / games, 2) if games else 0.0,
            "actual_ppg": round(actual / games, 2) if games else 0.0,
            "optimal_ppg": round(optimal / games, 2) if games else 0.0,
        }
    heat = {name: [season_by_team.get(name, {}).get("weekly_xw", {}).get(str(w)) for w in range(1, completed_week + 1)] for name in labels}

    live_weeks = []
    for path in ROOT.glob("live_week_*.json"):
        try:
            live_weeks.append(int(path.stem.rsplit("_", 1)[1]))
        except (ValueError, IndexError):
            continue
    live_weeks = sorted(set(live_weeks))

    trades, trade_warning, ratings = _trade_data(league, team_names, completed_week)
    return {
        "year": YEAR, "league_id": LEAGUE_ID, "teams": labels, "groups": GROUPS, "sources": SOURCES,
        "current_week": int(league.current_week), "completed_week": completed_week,
        "weeks": weeks_for_stats, "season": season, "heat": heat,
        "standings": standings, "weekly_results": weekly_results,
        "fantasy_multiverse": multiverse,
        "power_rankings": power_rankings, "power_history": power_history,
        "quality_rankings": quality_rankings,
        "playoff_teams": playoff_teams, "playoff_team_count": playoff_team_count,
        "consolation_teams": consolation_teams,
        "playoff_rounds": schedule_meta["playoff_rounds"],
        "division_count": schedule_meta["division_count"],
        "bracket_live_week": bracket_live_week,
        "position_totals": {name: position_totals[name] for name in labels},
        "weekly_positions": weekly_positions, "weekly_sources": weekly_sources,
        "source_totals": source_totals,
        "source_audit": dict(source_audit),
        "starter_bench_totals": starter_bench_totals,
        "starter_bench_weekly": starter_bench_weekly,
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
<style>.power-team-dot{display:inline-block;width:12px;height:12px;border-radius:50%;box-shadow:0 0 8px currentColor}.leaderboard-badge-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;align-items:center;justify-items:center;margin:14px 0}.leaderboard-badge{--accent:#f5a623;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:7px;width:min(100%,260px);aspect-ratio:1;border-radius:50%;padding:22px;background:radial-gradient(circle at 35% 25%,color-mix(in srgb,var(--accent),transparent 75%),transparent 50%),linear-gradient(145deg,#202a36,#10161e);border:2px solid color-mix(in srgb,var(--accent),white 15%);box-shadow:0 0 0 6px color-mix(in srgb,var(--accent),transparent 88%),0 12px 28px #0008}.leaderboard-badge .icon{font-size:34px}.leaderboard-badge .title{color:var(--accent);font-weight:850;letter-spacing:.06em;text-transform:uppercase}.leaderboard-badge .team{font-size:32px;font-weight:900}.leaderboard-badge .metric{font-size:12px;color:#c9d4de}.leaderboard-last .leaderboard-badge{--accent:#f06a6a;width:min(75%,190px);background:radial-gradient(circle at 35% 25%,rgba(248,81,73,.18),transparent 50%),linear-gradient(145deg,#321d22,#1a1318);box-shadow:0 0 0 4px rgba(248,81,73,.08),0 8px 20px #0007}.leaderboard-last .icon{font-size:28px}.leaderboard-last .team{font-size:26px}@media(max-width:900px){.leaderboard-badge-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.leaderboard-badge-grid{grid-template-columns:1fr}}</style>
<style>.scroll table th,.scroll table td{text-align:left!important}.ppg{font-size:.82em;color:var(--muted);font-weight:400}.clinching-markers{font-weight:800;letter-spacing:.08em;color:var(--gold)}.clinch-legend{font-size:12px;color:var(--muted);margin:0 0 10px}.bracket-round{min-height:240px}.bracket-round h3{line-height:1.4;min-height:44px;white-space:normal}.bracket-round .bracket-date{display:block;font-size:11px;font-weight:500;color:var(--muted)}.multiverse-summary{margin-top:14px}</style>
<style>
:root{color-scheme:dark;--bg:#0f1419;--panel:#161d26;--line:#243040;--muted:#8b98a5;--text:#e6edf3;--blue:#4da3ff;--gold:#f5a623;--green:#3fb950;--red:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,Segoe UI,Arial,sans-serif}.wrap{max-width:1500px;margin:auto;padding:22px 16px 56px}h1{font-size:27px;margin:0}.sub{color:var(--muted);margin:4px 0 20px}.tabs{display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:18px}.tab{background:transparent;color:var(--muted);border:0;border-bottom:3px solid transparent;padding:12px 16px;font-weight:700;font-size:15px;cursor:pointer}.tab.active{color:var(--text);border-color:var(--blue)}.panel{display:none}.panel.active{display:block}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px;min-width:0}.card h2{font-size:18px;margin:0 0 4px}.card p{font-size:13px;color:var(--muted);margin:0 0 12px}.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 14px}select,button,input{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 11px;font:inherit}button{cursor:pointer}.chartbox{height:390px;position:relative}.radar-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.rcell{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px;min-width:0}.rcell h3{text-align:center;margin:0 0 7px;font-size:14px}.rbox{height:210px;position:relative}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:center;padding:8px;border-bottom:1px solid var(--line)}th{color:var(--muted)}td:first-child,th:first-child{text-align:left;white-space:nowrap}.notice{border:1px solid #745d24;background:#2a2418;color:#f3d58b;padding:11px 13px;border-radius:10px;margin:10px 0}.good{border-color:#2c6840;background:#17291c;color:#9be5aa}.trade-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.trade-card{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0}.trade-card-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,38%);gap:16px;align-items:center}.trade-card-info{min-width:0}.trade-mini-chart{height:190px;position:relative;min-width:0}.team-trade-chart{height:320px;position:relative}.badge{display:inline-block;border-radius:999px;padding:4px 9px;background:#233141;color:#a8d2ff;font-size:12px;font-weight:700}.badge.gm{background:#183820;color:#8cec9c}.badge.fleeced{background:#3a1d1d;color:#ff9c96}.badge.inmix{background:#25313b;color:#c8d1d9}.player-select{min-height:155px;width:100%}.metric{font-weight:700}.muted{color:var(--muted)}.calculator-result{font-size:17px;margin-top:12px}@media(max-width:760px){.trade-card-layout{grid-template-columns:1fr}.trade-mini-chart{height:210px}}@media(max-width:900px){.radar-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.chartbox{height:330px}}@media(max-width:520px){.wrap{padding:15px 10px}.tab{padding:10px 8px;font-size:13px}.radar-grid{grid-template-columns:1fr}.chartbox{height:290px}.team-trade-chart{height:280px}}
<style>
.bracket-scroll.bracket{min-width:0}.trade-card-layout{grid-template-columns:minmax(0,1fr) minmax(390px,1.35fr)}.trade-visuals{display:grid;grid-template-columns:minmax(115px,.45fr) minmax(230px,1fr);gap:12px;align-items:center;min-width:0}.trade-player-flow{display:flex;align-items:center;justify-content:center;gap:7px;min-width:0}.trade-flow-group{display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0}.trade-flow-label{font-size:10px;color:var(--muted);font-weight:700;text-transform:uppercase}.trade-flow-players{display:flex;flex-direction:column;align-items:center;gap:5px}.trade-flow-player{display:flex;flex-direction:column;align-items:center;gap:2px;max-width:70px;text-align:center;font-size:9px;line-height:1.15}.trade-flow-player img,.roster-photo{width:38px;height:38px;object-fit:cover;border-radius:50%;background:#263342;border:1px solid var(--line)}.trade-arrow{font-size:22px;color:var(--blue);font-weight:800}.roster-table th,.roster-table td{text-align:left!important;white-space:nowrap}.roster-table .roster-player-name{min-width:150px}.roster-table .roster-slot{font-weight:700;color:var(--blue)}.roster-table tr.roster-bench{opacity:.78}.roster-player-cell{display:flex;align-items:center;gap:9px;min-width:180px}.roster-bench-start td{border-top:2px solid var(--text)!important}.acquisition-week{font-size:.8em;color:var(--muted)}.roster-photo-cell{width:54px}.roster-photo{display:block;width:42px;height:42px}.roster-empty{padding:18px;color:var(--muted)}
@media(max-width:900px){.trade-card-layout{grid-template-columns:1fr}.trade-visuals{grid-template-columns:minmax(130px,.4fr) minmax(220px,1fr)}}@media(max-width:560px){.trade-visuals{grid-template-columns:1fr}.trade-player-flow{justify-content:flex-start}.trade-flow-players{flex-direction:row;flex-wrap:wrap}.trade-mini-chart{height:210px}}
</style><style>table{table-layout:fixed;width:100%}th,td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.clinching-markers{display:flex;align-items:center;gap:4px}.clinching-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1}.clinching-tags{font-size:.9em;font-weight:800;color:#c9d4de;white-space:nowrap;flex:none}#standingsTable th:first-child,#standingsTable td:first-child{width:52px}#standingsTable th:nth-child(2),#standingsTable td:nth-child(2){width:74px}#standingsTable th:nth-child(3),#standingsTable td:nth-child(3){width:190px}#standingsTable .playoff-cut td{border-bottom:2px solid var(--blue)}.live-chartbox{height:520px}.roster-photo.defense-logo{object-fit:contain;background:#fff;padding:3px}.roster-bench-start td{border-top:2px solid var(--blue)}@media(max-width:900px){.live-chartbox{height:440px}}@media(max-width:560px){.live-chartbox{height:360px}}</style></head><body><main class="wrap">
<h1>Fielding of (football) dreams</h1><div class="sub" id="subtitle"></div>
<nav class="tabs" role="tablist" aria-label="Dashboard sections">
<button class="tab active" data-tab="league" role="tab" aria-selected="true">League Stats</button><button class="tab" data-tab="live" role="tab" aria-selected="false">Matchup stats</button><button class="tab" data-tab="team" role="tab" aria-selected="false">Team Stats</button><button class="tab" data-tab="luck" role="tab" aria-selected="false">Luck Stats</button><button class="tab" data-tab="trades" role="tab" aria-selected="false">Trades</button><button class="tab" data-tab="leaderboard" role="tab" aria-selected="false">Leaderboard</button></nav>

<section class="panel active" id="panel-league" role="tabpanel">
<div class="subtabs" role="tablist" aria-label="League statistics"><button class="subtab league-subtab active" type="button" data-league-view="standings" aria-selected="true">Standings</button><button class="subtab league-subtab" type="button" data-league-view="power" aria-selected="false">Power Rankings</button><button class="subtab league-subtab" type="button" data-league-view="playoffs" aria-selected="false">Playoff Bracket</button></div>
<section class="league-view active" id="league-view-standings"><article class="card"><h2>League Standings</h2><p>Completed regular-season record, points for, points against, and point differential. Points columns use a red-to-green scale centered on the league average.</p><p class="clinch-legend">Team markers: x clinched playoff berth · y clinched division · z clinched first-round bye · e eliminated. Markers follow common NFL standings shorthand.</p><div class="scroll"><table id="standingsTable"></table></div></article></section>
<section class="league-view" id="league-view-power"><article class="card"><h2>Power Rankings</h2><p class="model-details">Score weights: 20% overall record, 10% overall points, 30% record in the last 3 games, 20% points scored over the last 3 weeks, and 20% quality Elo. Quality Elo rewards wins over above-average scoring opponents, penalizes losses to below-average scoring opponents, stays neutral for easy wins and quality losses, and slightly favors opponents with better records entering that game.</p><div class="chartbox"><canvas id="powerHistoryChart"></canvas></div><div id="powerHighlight" class="power-highlight">Hover over or click a line to highlight a team.</div><div class="toolbar"><span>Week:</span><div id="powerWeeks" class="team-buttons" aria-label="Choose power ranking week"></div></div><div class="scroll"><table id="powerTable"></table></div></article></section>
<section class="league-view" id="league-view-playoffs"><article class="card"><h2>Playoff Bracket If the Season Ended Today</h2><p id="playoffBracketNote">Projected seeds use current standings, including live regular-season scores when available.</p><h3>Winners Bracket</h3><div id="playoffBracket" class="bracket-scroll"></div><h3>Consolation Bracket</h3><div id="consolationBracket" class="bracket-scroll"></div></article></section>
</section>

<section class="panel" id="panel-team" role="tabpanel">
<div class="subtabs" role="tablist" aria-label="Team statistics"><button class="subtab team-subtab active" type="button" data-team-view="position" aria-selected="true">Positional Breakdown</button><button class="subtab team-subtab" type="button" data-team-view="acquisition" aria-selected="false">Acquisitional Breakdown</button><button class="subtab team-subtab" type="button" data-team-view="starters" aria-selected="false">Starters vs. Bench</button><button class="subtab team-subtab" type="button" data-team-view="differential" aria-selected="false">Win/Loss Differential</button><button class="subtab team-subtab" type="button" data-team-view="roster" aria-selected="false">Rosters</button></div>
<section class="team-view active" id="team-view-position">
<article class="card"><h2>6-axis position breakdown</h2><p>Starter points grouped by each player's position. Players in FLEX count toward their own position.</p><div id="radars" class="radar-grid"></div></article>
<article class="card"><h2>Total points by position · all teams</h2><div class="chartbox"><canvas id="positionTotalsChart"></canvas></div></article>
<article class="card"><h2>Weekly points by position</h2><div class="toolbar"><span>Team:</span><div id="positionTeams" class="team-buttons" aria-label="Choose team"></div></div><div class="chartbox"><canvas id="positionChart"></canvas></div></article>
<article class="card"><h2>Total points by position</h2><div class="scroll"><table id="positionTotalsTable"></table></div></article>
</section>
<section class="team-view" id="team-view-acquisition"><article class="card"><h2>Total points by acquisition · all teams</h2><div class="chartbox"><canvas id="sourceTotalsChart"></canvas></div></article><article class="card"><h2>Weekly points by acquisition</h2><div class="toolbar"><span>Team:</span><div id="sourceTeams" class="team-buttons" aria-label="Choose team"></div></div><div class="chartbox"><canvas id="sourceChart"></canvas></div></article><article class="card"><h2>Total points by acquisition</h2><div class="scroll"><table id="sourceTotalsTable"></table></div></article></section>
<section class="team-view" id="team-view-starters"><article class="card"><h2>Starter lineup summary · all teams</h2><div class="scroll"><table id="starterBenchTable"></table></div></article><article class="card"><h2>Actual vs. Optimal Lineup Points</h2><div class="chartbox"><canvas id="actualOptimalChart"></canvas></div></article><article class="card"><h2>Starter and bench points · all teams</h2><div class="chartbox"><canvas id="starterBenchPointsChart"></canvas></div></article><article class="card"><h2>Points left on the bench per game · all teams</h2><div class="chartbox"><canvas id="benchLossChart"></canvas></div></article></section>
<section class="team-view" id="team-view-differential">
<article class="card"><h2>Win/Loss Differential</h2><p>Weekly points scored minus points allowed. Wins are green and losses are red; bars are centered at zero.</p><div class="toolbar"><span>Team:</span><div id="differentialTeams" class="team-buttons" aria-label="Choose team"></div></div><div class="chartbox"><canvas id="differentialChart"></canvas></div></article>
<article class="card"><h2>Average Win/Loss Differential</h2><p>Average point differential in wins is above zero in green; average point differential in losses is below zero in red.</p><div class="chartbox"><canvas id="overallDifferentialChart"></canvas></div></article>
</section>
 <section class="team-view" id="team-view-roster"><article class="card"><h2>Rosters</h2><div class="toolbar"><span>Team:</span><div id="rosterTeams" class="team-buttons" aria-label="Choose roster team"></div></div><div class="scroll"><table id="rosterStatsTable"></table></div></article></section>
</section>

<section class="panel" id="panel-luck" role="tabpanel">
<div class="subtabs" role="tablist" aria-label="Luck statistics"><button class="subtab luck-subtab active" type="button" data-luck-view="luck" aria-selected="true">Luck</button><button class="subtab luck-subtab" type="button" data-luck-view="quality" aria-selected="false">Win/Loss Quality</button><button class="subtab luck-subtab" type="button" data-luck-view="multiverse" aria-selected="false">Fantasy Multiverse</button></div>
<section class="luck-view active" id="luck-view-luck">
<article class="card"><h2>Expected wins and actual wins</h2><p>Expected wins compare each team's score with every other team that week. The shaded gap shows schedule luck.</p><div class="toolbar"><span>Team:</span><div id="luckTeams" class="team-buttons" aria-label="Choose team"></div><span id="luckNote" class="muted"></span></div><div class="chartbox"><canvas id="luckLine"></canvas></div></article>
<article class="card"><h2>Schedule luck (actual wins minus expected wins)</h2><p>Positive means a team won more often than its points would predict against the league; negative means fewer.</p><div class="chartbox"><canvas id="luckBars"></canvas></div></article>
<article class="card"><h2>Weekly expected win share</h2><p>Each cell is the share of league teams outscored that week. 1.00 is the top score.</p><div class="scroll"><table id="heat"></table></div></article>
</section>
<section class="luck-view" id="luck-view-quality"><article class="card"><h2>Win/Loss Quality</h2><div class="scroll"><table id="qualityTable"></table></div></article></section>
<section class="luck-view" id="luck-view-multiverse">
<article class="card multiverse-summary"><h2>Actual wins in the schedule multiverse</h2><div class="scroll"><table id="multiverseSummaryTable"></table></div></article>
<article class="card"><h2>Fantasy Multiverse</h2><p>Win distributions compare each team’s actual weekly scores against 20,000 sampled valid schedules. Teams face every opponent once, then repeat the same early-season opponents for the remaining regular-season weeks.</p><div class="toolbar"><span>Team:</span><div id="multiverseTeams" class="team-buttons" aria-label="Choose multiverse team"></div><span id="multiverseTeamLabel" class="muted"></span></div><div class="chartbox"><canvas id="multiverseTeamChart"></canvas></div></article>
<article class="card"><h2>All Teams’ Win Distributions</h2><div id="multiverseAllLabel" class="power-highlight">Hover over or click a line to highlight a team.</div><div class="chartbox"><canvas id="multiverseAllChart"></canvas></div></article>
<article class="card"><h2>First Place Across Schedules</h2><div class="chartbox"><canvas id="multiverseFirstPie"></canvas></div></article>
</section>
</section>

<section class="panel" id="panel-live" role="tabpanel">
 <article class="card"><h2>Matchup stats</h2><div class="toolbar"><span>Week:</span><div id="liveWeeks" class="team-buttons" aria-label="Choose week"></div><span>Matchup:</span><div id="liveMatchups" class="team-buttons" aria-label="Choose matchup"></div><span id="liveNote" class="muted"></span></div><div id="liveTopTeam" class="live-team-name live-team-top"></div><div class="chartbox live-chartbox"><canvas id="liveChart"></canvas></div><div id="liveBottomTeam" class="live-team-name live-team-bottom"></div></article>
</section>

<section class="panel" id="panel-trades" role="tabpanel">
<article class="card"><h2>Trades by team</h2><p>Accepted trades involving the selected team, with post-trade player points and an individual trade rating.</p><div class="toolbar"><span>Team:</span><div id="tradeTeams" class="team-buttons" aria-label="Choose team"></div><span id="teamRating"></span></div><div id="tradeNotice" class="notice" style="display:none"></div><h3 id="teamTradeTitle"></h3><div class="team-trade-chart"><canvas id="teamTradeChart"></canvas></div><div id="teamTrades"></div></article>
</section>
<section class="panel" id="panel-leaderboard" role="tabpanel"><article class="card"><h2>Leaderboard</h2><div class="leaderboard-badge-grid" id="leaderboardWinners"></div><div class="leaderboard-badge-grid leaderboard-last" id="leaderboardLast"></div></article></section>
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
 D.teams.forEach((team,i)=>{const cell=document.createElement('div');cell.className='rcell';const title=document.createElement('h3');title.textContent=teamAbbrev(team);title.title=team;const box=document.createElement('div');box.className='rbox';const cv=document.createElement('canvas');box.append(cv);cell.append(title,box);radars.append(cell);new Chart(cv,{type:'radar',data:{labels:D.groups.map((g,j)=>[g,'(max '+maxima[j].toFixed(0)+')']),datasets:[{label:teamAbbrev(team),data:normalized(D.groups.map(g=>D.position_totals[team][g]||0)),borderColor:colors[0],backgroundColor:'rgba(77,163,255,.28)',borderWidth:2},{label:'League avg',data:normalized(D.groups.map(g=>D.teams.reduce((s,t)=>s+(D.position_totals[t][g]||0),0)/D.teams.length)),borderColor:'#8b98a5',borderDash:[5,4],backgroundColor:'transparent',pointRadius:0}]},options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label+': '+(c.datasetIndex===0?(D.position_totals[team][D.groups[c.dataIndex]]||0):(D.teams.reduce((s,t)=>s+(D.position_totals[t][D.groups[c.dataIndex]]||0),0)/D.teams.length)).toFixed(1)+' pts'}}},scales:{r:{min:0,max:100,ticks:{display:false},grid:{color:'#243040'},angleLines:{color:'#243040'}}}}});});

const positionGroups=['DEF',...D.groups.filter(group=>group!=='DEF')];
const positionRows=D.teams.flatMap(team=>D.weekly_positions[team]||[]);
const positionAxisMax=Math.max(20,Math.ceil(Math.max(0,...positionRows.map(row=>positionGroups.reduce((sum,group)=>sum+Math.max(0,Number(row[group]||0)),0)))/20)*20);
const positionAxisMin=Math.min(0,Math.floor(Math.min(0,...positionRows.map(row=>positionGroups.reduce((sum,group)=>sum+Math.min(0,Number(row[group]||0)),0)))/20)*20);
function setTableCellText(cell,value){const text=String(value??'');const match=text.match(/^(.*?)(\s+\([^()]*\bPPG\))$/);if(!match){cell.textContent=text;return;}const main=document.createElement('span');main.textContent=match[1];const ppg=document.createElement('span');ppg.className='ppg';ppg.textContent=match[2];cell.append(main,ppg);}
function renderSortableStatsTable(id,columns,rows,initialSortKey=columns[0].key,initialDirection=1){const table=document.getElementById(id);let sortKey=initialSortKey,direction=initialDirection;function draw(){const ordered=[...rows].sort((a,b)=>{const left=a[sortKey],right=b[sortKey];return (typeof left==='number'&&typeof right==='number'?direction*(left-right):direction*String(left??'').localeCompare(String(right??'')))||String(a.team).localeCompare(String(b.team));});table.replaceChildren();const head=document.createElement('tr');columns.forEach(column=>{const th=document.createElement('th');th.className='sortable';th.textContent=column.label+(column.key===sortKey?(direction<0?' ▼':' ▲'):'');th.title='Sort by '+column.label;th.addEventListener('click',()=>{if(sortKey===column.key)direction*=-1;else{sortKey=column.key;direction=column.key==='team'?1:-1;}draw();});head.append(th);});table.append(head);ordered.forEach(row=>{const tr=document.createElement('tr');columns.forEach(column=>{const td=document.createElement('td');setTableCellText(td,column.render?column.render(row):row[column.key]);if(column.key==='team')td.title=row.team;if(column.cellStyle)column.cellStyle(td,row);tr.append(td);});table.append(tr);});}draw();}
const positionStatRows=D.teams.map(team=>{const played=(D.weekly_positions[team]||[]).filter(row=>positionGroups.reduce((sum,group)=>sum+Math.abs(Number(row[group]||0)),0)>0).length||1;const row={team};positionGroups.forEach(group=>{row[group]=Number(D.position_totals[team]?.[group]||0);row[group+'_display']=`${row[group].toFixed(1)} (${(row[group]/played).toFixed(1)} PPG)`;});return row;});
renderSortableStatsTable('positionTotalsTable',[{key:'team',label:'Team',render:row=>teamAbbrev(row.team)},...positionGroups.map(group=>({key:group,label:group,render:row=>row[group+'_display']}))],positionStatRows);
new Chart(document.getElementById('positionTotalsChart'),{type:'bar',data:{labels:D.teams.map(teamAbbrev),datasets:positionGroups.map(group=>({label:group,data:D.teams.map(team=>Number(D.position_totals[team]?.[group]||0)),backgroundColor:colors[D.groups.indexOf(group)]}))},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom'},tooltip:{callbacks:{title:items=>items.length?D.teams[items[0].dataIndex]:''}},},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,title:{display:true,text:'Fantasy points'}}}}});
const posChart=new Chart(document.getElementById('positionChart'),{type:'bar',data:{labels:D.weeks.map(w=>'W'+w),datasets:positionGroups.map((g)=>({label:g,data:[],backgroundColor:colors[D.groups.indexOf(g)]}))},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:false,min:positionAxisMin,max:positionAxisMax,title:{display:true,text:'Fantasy points'}}}}});
function showPosition(t){const rows=D.weekly_positions[t]||[];posChart.options.plugins.title.text=teamAbbrev(t);posChart.data.datasets.forEach((ds,i)=>ds.data=rows.map(r=>r[positionGroups[i]]||0));posChart.update();}
const positionButtons=fillTeamButtons('positionTeams',showPosition);
const sourceColors={Drafted:'#4da3ff',Traded:'#da70d6',Waivers:'#f5a623'};
const sourceStatRows=D.teams.map(team=>{const games=Math.max(1,Number(D.completed_week)||0);const row={team};D.sources.forEach(source=>{row[source]=Number(D.source_totals?.[team]?.[source]||0);row[source+'_display']=`${row[source].toFixed(1)} (${(row[source]/games).toFixed(1)} PPG)`;});return row;});
renderSortableStatsTable('sourceTotalsTable',[{key:'team',label:'Team',render:row=>teamAbbrev(row.team)},...D.sources.map(source=>({key:source,label:source,render:row=>row[source+'_display']}))],sourceStatRows);
new Chart(document.getElementById('sourceTotalsChart'),{type:'bar',data:{labels:D.teams.map(teamAbbrev),datasets:D.sources.map(source=>({label:source,data:D.teams.map(team=>Number(D.source_totals?.[team]?.[source]||0)),backgroundColor:sourceColors[source]}))},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom'},tooltip:{callbacks:{title:items=>items.length?D.teams[items[0].dataIndex]:''}}},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,title:{display:true,text:'Fantasy points'}}}}});
const sourceChart=new Chart(document.getElementById('sourceChart'),{type:'bar',data:{labels:D.weeks.map(w=>'W'+w),datasets:D.sources.map(source=>({label:source,data:[],backgroundColor:sourceColors[source]}))},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:false,min:positionAxisMin,max:positionAxisMax,title:{display:true,text:'Fantasy points'}}}}});
function showSource(team){const rows=D.weekly_sources[team]||[];sourceChart.options.plugins.title.text=teamAbbrev(team);sourceChart.data.datasets.forEach((dataset,index)=>dataset.data=rows.map(row=>row[D.sources[index]]||0));sourceChart.update();}
const sourceButtons=fillTeamButtons('sourceTeams',showSource);
const starterBenchRows=D.teams.map(team=>{const values=D.starter_bench_totals?.[team]||{actual:0,bench:0,optimal:0,left:0,left_ppg:0,actual_ppg:0,optimal_ppg:0,games:0};return{team,...values,optimal_pct:values.optimal>0?100*values.actual/values.optimal:0};});
const realizationMin=Math.min(...starterBenchRows.map(row=>row.optimal_pct));
renderSortableStatsTable('starterBenchTable',[{key:'team',label:'Team',render:row=>teamAbbrev(row.team)},{key:'actual',label:'Actual points (PPG)',render:row=>`${row.actual.toFixed(1)} (${row.actual_ppg.toFixed(1)} PPG)`},{key:'bench',label:'Bench points',render:row=>row.bench.toFixed(1)},{key:'optimal',label:'Optimal lineup points (PPG)',render:row=>`${row.optimal.toFixed(1)} (${row.optimal_ppg.toFixed(1)} PPG)`},{key:'optimal_pct',label:'Realization of Optimal',render:row=>`${row.optimal_pct.toFixed(1)}%`,cellStyle:(cell,row)=>{const hue=realizationMin>=100?120:120*(row.optimal_pct-realizationMin)/Math.max(1,100-realizationMin);cell.style.color=`hsl(${Math.max(0,Math.min(120,hue))},90%,58%)`;cell.style.fontWeight='700';}}],starterBenchRows,'optimal_pct',-1);
new Chart(document.getElementById('actualOptimalChart'),{type:'bar',data:{labels:D.teams.map(teamAbbrev),datasets:[{label:'Actual points',data:starterBenchRows.map(row=>row.actual),backgroundColor:'#4da3ff',stack:'lineup'},{label:'Optimal points added',data:starterBenchRows.map(row=>Math.max(0,row.optimal-row.actual)),backgroundColor:'#f5a623',stack:'lineup'}]},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom'},tooltip:{callbacks:{title:items=>items.length?D.teams[items[0].dataIndex]:''}}},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,title:{display:true,text:'Fantasy points'}}}}});
const starterBenchMax=Math.max(0,...starterBenchRows.flatMap(row=>[row.actual,row.bench]));
new Chart(document.getElementById('starterBenchPointsChart'),{type:'bar',data:{labels:D.teams.map(teamAbbrev),datasets:[{label:'Starter points',data:starterBenchRows.map(row=>row.actual),backgroundColor:'#4da3ff'},{label:'Bench points',data:starterBenchRows.map(row=>row.bench),backgroundColor:'#f5a623'}]},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom'},tooltip:{callbacks:{title:items=>items.length?D.teams[items[0].dataIndex]:''}}},scales:{x:{},y:{beginAtZero:true,max:starterBenchMax?starterBenchMax*1.08:1,title:{display:true,text:'Fantasy points'}}}}});
const benchLossRows=[...starterBenchRows].sort((a,b)=>a.left_ppg-b.left_ppg),benchLossValues=benchLossRows.map(row=>row.left_ppg),benchLossMax=Math.max(0,...benchLossValues),benchLossColor=value=>{const low=Math.min(...benchLossValues),high=Math.max(...benchLossValues),ratio=high>low?(value-low)/(high-low):.5;return `hsl(${120*(1-ratio)},75%,48%)`;};
new Chart(document.getElementById('benchLossChart'),{type:'bar',data:{labels:benchLossRows.map(row=>teamAbbrev(row.team)),datasets:[{label:'Points left on bench per game',data:benchLossValues,backgroundColor:benchLossValues.map(benchLossColor),borderRadius:5}]},options:{maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>items.length?benchLossRows[items[0].dataIndex]?.team:''}}},scales:{x:{beginAtZero:true,max:benchLossMax?benchLossMax*1.08:1,title:{display:true,text:'Points per game'}}}}});
const differentialChart=new Chart(document.getElementById('differentialChart'),{type:'bar',data:{labels:[],datasets:[{label:'Points scored - points allowed',data:[],backgroundColor:[],borderRadius:4},{type:'line',label:'0 points',data:[],borderColor:'#a7b3c2',borderDash:[6,5],borderWidth:1.5,pointRadius:0,fill:false}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''},legend:{position:'bottom'}},scales:{x:{title:{display:true,text:'Week'}},y:{title:{display:true,text:'Point differential'},ticks:{precision:1}}}}});
function showDifferential(team){const rows=D.weekly_results[team]||[],values=rows.map(r=>r.point_diff),bound=Math.max(1,...values.map(v=>Math.abs(v)));differentialChart.options.plugins.title.text='Win/Loss Differential by week - '+teamAbbrev(team);differentialChart.options.scales.y.min=-bound;differentialChart.options.scales.y.max=bound;differentialChart.data.labels=rows.map(r=>'W'+r.week);differentialChart.data.datasets[0].data=values;differentialChart.data.datasets[0].backgroundColor=rows.map(r=>r.outcome==='W'?'#3fb950':r.outcome==='L'?'#f85149':'#a7b3c2');differentialChart.data.datasets[1].data=values.map(()=>0);differentialChart.update();}
const differentialButtons=fillTeamButtons('differentialTeams',showDifferential);
const rosterStatsTable=document.getElementById('rosterStatsTable');
const allRosterPlayers=D.teams.flatMap(team=>D.rosters?.[D.roster_team_ids?.[team]]||[]);
const maxProjectionPpg=Math.max(1,...allRosterPlayers.map(player=>Math.abs(Number(player.points_vs_projection_ppg)||0)));
function projectionPpgColor(value){const normalized=Math.max(-1,Math.min(1,Number(value||0)/maxProjectionPpg));return `hsl(${60+60*normalized},88%,58%)`;}
function renderRosterStats(team){
  const teamId=D.roster_team_ids?.[team],players=D.rosters?.[teamId]||[];
  rosterStatsTable.className='roster-table';rosterStatsTable.replaceChildren();
   const caption=document.createElement('caption');caption.className='muted';caption.style.textAlign='left';caption.style.padding='0 8px 8px';caption.textContent='Season averages and standard deviations use each player’s completed scoring weeks across all fantasy teams, excluding NFL bye weeks. Projection SD uses weeks with ESPN projections; scoring SD uses completed scoring weeks. A dash means fewer than two observations.';rosterStatsTable.append(caption);
  const header=document.createElement('tr');
   const columns=[['Player','Player name or NFL team logo'],['Slot','Current lineup slot; BE and IR rows are bench/nonstarters'],['Position','Player position'],['PPG','Season points per game across all league-owned weeks, excluding bye weeks'],['Points vs. Initial Projections (PPG)','Average signed actual points minus initial projection per played week'],['Projection SD','Standard deviation of weekly actual-minus-projection points'],['Scoring SD','Standard deviation of weekly fantasy points'],['Pos. Rank','ESPN position rank'],['Acquisition','How the player joined this roster and acquisition week when known'],['NFL Team','Player’s NFL team'],['Bye','NFL bye week']];
  columns.forEach(([label,title])=>{const th=document.createElement('th');th.textContent=label;th.title=title;header.append(th);});rosterStatsTable.append(header);
   if(!players.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=columns.length;cell.className='roster-empty';cell.textContent='No roster data was returned for this team.';row.append(cell);rosterStatsTable.append(row);return;}
  let hasStarter=false,dividerAdded=false;
  players.forEach(player=>{
    const slot=player.slot||'BE',isNonstarter=slot==='BE'||slot==='IR';
    if(!isNonstarter)hasStarter=true;
    const row=document.createElement('tr');if(isNonstarter)row.classList.add('roster-bench');
    if(isNonstarter&&hasStarter&&!dividerAdded){row.classList.add('roster-bench-start');dividerAdded=true;}
    const playerCell=document.createElement('td'),playerDisplay=document.createElement('div');playerDisplay.className='roster-player-cell';
     const isDefense=['D/ST','DST','DEF'].includes(String(player.position||'').toUpperCase());const image=document.createElement('img');image.className='roster-photo'+(isDefense?' defense-logo':'');image.alt=isDefense?(player.nfl_team||'NFL defense'):player.name;image.loading='lazy';
     image.src=(isDefense?player.team_logo_url:player.photo_url)||'';image.onerror=()=>{const initials=document.createElement('span');initials.className='roster-photo roster-avatar-fallback';initials.textContent=String(isDefense?player.nfl_team:player.name||'?').split(/\s+/).map(part=>part[0]||'').join('').slice(0,2).toUpperCase();playerDisplay.replaceChild(initials,image);};
    const playerName=document.createElement('span');playerName.textContent=player.name;playerDisplay.append(image,playerName);playerCell.append(playerDisplay);row.append(playerCell);
     const values=[slot,player.position||'—',`${Number(player.ppg||0).toFixed(2)}`,player.points_vs_projection_ppg==null?'—':`${Number(player.points_vs_projection_ppg)>=0?'+':''}${Number(player.points_vs_projection_ppg).toFixed(2)}`,player.projection_sd==null?'—':`${Number(player.projection_sd).toFixed(2)} pts`,player.raw_sd==null?'—':`${Number(player.raw_sd).toFixed(2)} pts`,player.pos_rank==null?'—':player.pos_rank];
     values.forEach((value,index)=>{const cell=document.createElement('td');cell.textContent=value;if(index===0)cell.className='roster-slot';if(index===3&&player.points_vs_projection_ppg!=null){cell.style.color=projectionPpgColor(player.points_vs_projection_ppg);cell.style.fontWeight='750';}if(index===4)cell.title=`Projection SD from ${Number(player.projection_games||0)} completed game week(s)`;if(index===5)cell.title=`Scoring SD from ${Number(player.score_games||0)} completed game week(s)`;row.append(cell);});
    const acquisitionCell=document.createElement('td'),acquisition=document.createElement('span');acquisition.textContent=player.acquisition==='Traded'?'Traded for':player.acquisition==='Waivers'?'Acquired':(player.acquisition||'—');acquisitionCell.append(acquisition);if(player.acquisition&&player.acquisition!=='Drafted'&&player.acquisition_week!=null){const week=document.createElement('span');week.className='acquisition-week';week.textContent=` (W${player.acquisition_week})`;acquisitionCell.append(week);}acquisitionCell.title=player.acquisition_week==null?'':`Joined this roster in Week ${player.acquisition_week}`;row.append(acquisitionCell);
    const nflCell=document.createElement('td');nflCell.textContent=player.nfl_team||'—';row.append(nflCell);
    const byeCell=document.createElement('td');byeCell.textContent=player.bye_week==null?'—':player.bye_week;if(Number(player.bye_week)===Number(D.current_week)){byeCell.style.color='#f85149';byeCell.style.fontWeight='800';byeCell.title='Bye week is this week';}row.append(byeCell);
    rosterStatsTable.append(row);
  });
}
const rosterButtons=fillTeamButtons('rosterTeams',renderRosterStats);
const winDiffTotals=D.teams.map(team=>{const rows=(D.weekly_results[team]||[]).filter(row=>row.outcome==='W');return rows.length?rows.reduce((sum,row)=>sum+Math.abs(Number(row.point_diff)||0),0)/rows.length:0;});
const lossDiffTotals=D.teams.map(team=>{const rows=(D.weekly_results[team]||[]).filter(row=>row.outcome==='L');return rows.length?-rows.reduce((sum,row)=>sum+Math.abs(Number(row.point_diff)||0),0)/rows.length:0;});
const overallDiffBound=Math.max(1,...winDiffTotals.map(Math.abs),...lossDiffTotals.map(Math.abs));
new Chart(document.getElementById('overallDifferentialChart'),{type:'bar',data:{labels:D.teams.map(teamAbbrev),datasets:[{label:'Point differential in wins',data:winDiffTotals,backgroundColor:'#3fb950',borderRadius:3,stack:'differential'},{label:'Point differential in losses',data:lossDiffTotals,backgroundColor:'#f85149',borderRadius:3,stack:'differential'},{type:'line',label:'0 points',data:D.teams.map(()=>0),borderColor:'#a7b3c2',borderDash:[6,5],borderWidth:1.5,pointRadius:0,fill:false}]},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom'},tooltip:{callbacks:{title:items=>items.length?D.teams[items[0].dataIndex]:''}}},scales:{x:{stacked:true,ticks:{maxRotation:45,minRotation:0}},y:{stacked:true,min:-overallDiffBound,max:overallDiffBound,title:{display:true,text:'Point differential'},ticks:{precision:0}}}}});

const standingsTable=document.getElementById('standingsTable');
function appendTableHeader(table,labels){const tr=document.createElement('tr');labels.forEach(label=>{const th=document.createElement('th');th.textContent=label;tr.append(th);});table.append(tr);}
function colorByLeagueAverage(value,values){const avg=values.reduce((sum,v)=>sum+v,0)/(values.length||1),lo=Math.min(...values,avg),hi=Math.max(...values,avg),red=[248,81,73],yellow=[245,214,108],green=[63,185,80];let a,b,t;if(value<=avg){a=red;b=yellow;t=avg===lo?1:(value-lo)/(avg-lo);}else{a=yellow;b=green;t=hi===avg?1:(value-avg)/(hi-avg);}t=Math.max(0,Math.min(1,t));return 'rgb('+a.map((channel,i)=>Math.round(channel+(b[i]-channel)*t)).join(',')+')';}
const playoffOrder=[...(D.playoff_teams||[]),...(D.consolation_teams||[])],playoffPositionByTeam=new Map(playoffOrder.map(row=>[row.team,Number(row.seed)]));
const sortKeys={position:'playoff_position',wins:'wins',losses:'losses',ties:'ties',pct:'pct',games_back:'games_back',points_for:'points_for',points_against:'points_against',point_diff:'point_diff'};
const sortLabels={position:'Position',wins:'W',losses:'L',ties:'T',pct:'PCT',games_back:'GB',points_for:'PF (PPG)',points_against:'PA (PPG)',point_diff:'DIFF'};
const defaultSortDirection={position:1,wins:-1,losses:1,ties:-1,pct:-1,games_back:1,points_for:-1,points_against:1,point_diff:-1};let standingsSort='position',standingsDirection=1;
function renderStandings(){
  const key=sortKeys[standingsSort]||'wins',direction=standingsDirection;
  const positionOf=row=>Number(row.playoff_position??playoffPositionByTeam.get(row.team)??row.espn_rank??999);
  const rows=[...D.standings].sort((a,b)=>standingsSort==='position'?direction*(positionOf(a)-positionOf(b))||a.team.localeCompare(b.team):standingsSort==='wins'?(Number(a.espn_rank||999)-Number(b.espn_rank||999)):direction*(Number(a[key]||0)-Number(b[key]||0))||positionOf(a)-positionOf(b));
  standingsTable.replaceChildren();const header=document.createElement('tr');
  [['Rank',null],['Position','position'],['Team',null],...Object.entries(sortLabels).filter(([k])=>k!=='position')].forEach(([label,k])=>{const th=document.createElement('th');th.textContent=label+(k===standingsSort?(direction<0?' ▼':' ▲'):'');if(k){th.className='sortable';th.setAttribute('aria-sort',k===standingsSort?(direction<0?'descending':'ascending'):'none');th.title=k==='wins'?'Sort by ESPN standings order, including league tiebreakers':'Sort by '+label;th.addEventListener('click',()=>{if(standingsSort===k)standingsDirection*=-1;else{standingsSort=k;standingsDirection=defaultSortDirection[k];}renderStandings();});}header.append(th);});standingsTable.append(header);
  const playoffCount=Number(D.playoff_team_count||D.playoff_teams?.length||0);
   rows.forEach((row,index)=>{const playoffBoundary=standingsDirection>0?playoffCount-1:rows.length-playoffCount-1;const tr=document.createElement('tr');if(standingsSort==='position'&&playoffCount>0&&playoffCount<rows.length&&index===playoffBoundary)tr.classList.add('playoff-cut');const rank=document.createElement('td');rank.textContent=row.espn_rank||index+1;tr.append(rank);const position=document.createElement('td');position.textContent=positionOf(row);tr.append(position);const team=document.createElement('td');team.className='clinching-markers';const markers=row.clinching||[];const teamName=document.createElement('span');teamName.className='clinching-name';teamName.textContent=row.team;teamName.title=row.team;team.append(teamName);if(markers.length){const tags=document.createElement('span');tags.className='clinching-tags';tags.textContent=markers.join(' ');tags.title=markers.map(mark=>({x:'Clinched playoff berth',y:'Clinched division',z:'Clinched first-round bye',e:'Eliminated'}[mark]||mark)).join(' · ');team.append(tags);}team.title=row.team+(markers.length?' · '+markers.map(mark=>({x:'Clinched playoff berth',y:'Clinched division',z:'Clinched first-round bye',e:'Eliminated'}[mark]||mark)).join(' · '):'');tr.append(team);const values=[row.wins,row.losses,row.ties,Number(row.pct||0).toFixed(3),row.games_back===0?'—':Number(row.games_back||0).toFixed(1)];values.forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});
    const games=Number(row.games||0),pf=Number(row.points_for||0),pa=Number(row.points_against||0),diff=Number(row.point_diff||0);
    [[pf,'points_for',false],[pa,'points_against',true],[diff,'point_diff',false]].forEach(([value,metric,invert])=>{const td=document.createElement('td');td.className='standings-value';setTableCellText(td,metric==='point_diff'?value.toFixed(1):`${value.toFixed(1)} (${games?(value/games).toFixed(1):'0.0'} PPG)`);const values=D.standings.map(r=>Number(r[metric]||0));td.style.color=colorByLeagueAverage(invert?-value:value,invert?values.map(v=>-v):values);tr.append(td);});
    standingsTable.append(tr);
  });
}
renderStandings();

const leaderboardWinnerHost=document.getElementById('leaderboardWinners'),leaderboardLastHost=document.getElementById('leaderboardLast'),seasonLookup=new Map((D.season||[]).map(row=>[row.team,row])),tradeLookup=new Map((D.trade_ratings||[]).map(row=>[row.team,row]));
const leaderboardCategories=[
  {title:'Highest Scoring',last:'Anemic',icon:'🏆',value:team=>Number(D.standings.find(row=>row.team===team)?.points_for||0),metric:v=>`${v.toFixed(1)} points scored`,best:'max',accent:'#f5c451'},
  {title:'Luckiest',last:'Hated by God',icon:'🍀',value:team=>Number(seasonLookup.get(team)?.luck||0),metric:v=>`${v>=0?'+':''}${v.toFixed(2)} schedule luck`,best:'max',accent:'#58d68d'},
  {title:'COTY',last:'Loser of Men',icon:'🧠',value:team=>Number(D.starter_bench_totals?.[team]?.left_ppg||0),metric:v=>`${v.toFixed(2)} PPG left on the bench`,best:'min',accent:'#65b8ff'},
  {title:'GM of the Year',last:'Fleeced 24/7',icon:'🤝',value:team=>Number(tradeLookup.get(team)?.score||0),metric:v=>`${v>=0?'+':''}${v.toFixed(2)} trade points/week`,best:'max',accent:'#d58bff'},
];
function drawLeaderboardBadge(host,category,result,label,icon,accent){const badge=document.createElement('div');badge.className='leaderboard-badge';badge.style.setProperty('--accent',accent);badge.title=result.team;const iconNode=document.createElement('div');iconNode.className='icon';iconNode.textContent=icon;const titleNode=document.createElement('div');titleNode.className='title';titleNode.textContent=label;const teamNode=document.createElement('div');teamNode.className='team';teamNode.textContent=teamAbbrev(result.team);const metricNode=document.createElement('div');metricNode.className='metric';metricNode.textContent=category.metric(result.value);badge.append(iconNode,titleNode,teamNode,metricNode);host.append(badge);}
leaderboardCategories.forEach(category=>{const ranked=D.teams.map(team=>({team,value:category.value(team)}));ranked.sort((a,b)=>(category.best==='min'?a.value-b.value:b.value-a.value)||D.teams.indexOf(a.team)-D.teams.indexOf(b.team));drawLeaderboardBadge(leaderboardWinnerHost,category,ranked[0],category.title,category.icon,category.accent);drawLeaderboardBadge(leaderboardLastHost,category,ranked[ranked.length-1],category.last,'💩','#f06a6a');});

const powerTable=document.getElementById('powerTable'),powerWeeksHost=document.getElementById('powerWeeks'),powerColors=D.teams.map((_,i)=>`hsl(${Math.round(360*i/Math.max(1,D.teams.length))} 78% 58%)`);appendTableHeader(powerTable,['Rank','Team','Score','Team Color']);
function renderPowerRankings(rows){powerTable.replaceChildren();appendTableHeader(powerTable,['Rank','Team','Score','Team Color']);const scores=rows.map(row=>row.score);rows.forEach(row=>{const tr=document.createElement('tr');[row.rank,teamAbbrev(row.team),row.score.toFixed(1)].forEach((value,index)=>{const td=document.createElement('td');td.textContent=value;if(index===2){td.className='standings-value';td.style.color=colorByLeagueAverage(row.score,scores);}if(index===1)td.title=row.team;tr.append(td);});const colorCell=document.createElement('td');const dot=document.createElement('span');dot.className='power-team-dot';const teamIndex=D.teams.indexOf(row.team);dot.style.backgroundColor=powerColors[teamIndex];dot.title=row.team+' line color';dot.setAttribute('aria-label',row.team+' line color');colorCell.append(dot);tr.append(colorCell);powerTable.append(tr);});}
const powerHistory=D.power_history||[];const powerChoices=[...powerHistory,{week:'Current',teams:D.power_rankings}];
powerChoices.forEach((entry,index)=>makeChoiceButton(powerWeeksHost,entry.week==='Current'?'Current':'W'+entry.week,'Power rankings after week '+entry.week,index===powerChoices.length-1,()=>{[...powerWeeksHost.children].forEach((button,i)=>{const active=i===index;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});renderPowerRankings(entry.teams);}));renderPowerRankings(D.power_rankings);
const powerHighlight=document.getElementById('powerHighlight');let pinnedPowerTeam=null;
const powerEndLabels={id:'powerEndLabels',afterDatasetsDraw(chart){if(chart.canvas.id!=='powerHistoryChart'||!chart.chartArea||!chart.data.labels.length)return;const ctx=chart.ctx,area=chart.chartArea,scale=chart.scales.y;const labels=chart.data.datasets.map((dataset,index)=>{let pointIndex=dataset.data.length-1;while(pointIndex>=0&&(dataset.data[pointIndex]==null||!Number.isFinite(Number(dataset.data[pointIndex]))))pointIndex--;return pointIndex<0?null:{index,y:scale.getPixelForValue(Number(dataset.data[pointIndex])),text:teamAbbrev(D.teams[index])};}).filter(Boolean).sort((a,b)=>a.y-b.y);const low=area.top+5,high=area.bottom-4,gap=labels.length>1?Math.min(10,(high-low)/(labels.length-1)):10,fontSize=Math.max(6,Math.min(9,gap-1));labels.forEach((label,index)=>{label.y=Math.max(low,label.y);if(index)label.y=Math.max(label.y,labels[index-1].y+gap);});for(let i=labels.length-1;i>=0;i--)labels[i].y=Math.min(labels[i].y,high-(labels.length-1-i)*gap);ctx.save();ctx.font=`600 ${fontSize}px system-ui,Segoe UI,Arial,sans-serif`;ctx.textAlign='left';ctx.textBaseline='middle';labels.forEach(label=>{ctx.fillStyle=powerColors[label.index];ctx.fillText(label.text,area.right+5,label.y);});ctx.restore();}};Chart.register(powerEndLabels);
const powerChart=new Chart(document.getElementById('powerHistoryChart'),{type:'line',data:{labels:powerHistory.map(row=>'W'+row.week),datasets:D.teams.map((team,index)=>({label:team,data:powerHistory.map(week=>week.teams.find(row=>row.team===team)?.rank??null),borderColor:powerColors[index],backgroundColor:powerColors[index],borderWidth:1.5,pointRadius:1,pointHoverRadius:5,tension:.18,spanGaps:false}))},options:{maintainAspectRatio:false,layout:{padding:{right:36}},interaction:{mode:'nearest',intersect:false},plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>items.length?'Week '+powerHistory[items[0].dataIndex]?.week:'',label:item=>`${D.teams[item.datasetIndex]} · rank ${item.raw}`}}},scales:{y:{reverse:true,min:1,max:D.teams.length,ticks:{stepSize:1},title:{display:true,text:'Power rank (1 is best)'}},x:{title:{display:true,text:'Completed week'}}},onHover:(event,elements)=>{if(pinnedPowerTeam===null)highlightPowerTeam(elements[0]?.datasetIndex??null);},onClick:(event,elements)=>{const index=elements[0]?.datasetIndex;if(index===undefined){pinnedPowerTeam=null;highlightPowerTeam(null);}else{pinnedPowerTeam=pinnedPowerTeam===index?null:index;highlightPowerTeam(pinnedPowerTeam);}}}});
function highlightPowerTeam(index){powerChart.data.datasets.forEach((dataset,i)=>{dataset.borderColor=index===null?powerColors[i]:(i===index?powerColors[i]:'rgba(160,170,180,.18)');dataset.backgroundColor=dataset.borderColor;dataset.borderWidth=index===i?4:1.5;dataset.pointRadius=index===i?2:0;});powerHighlight.textContent=index===null?'Hover over or click a line to highlight a team.':D.teams[index];powerHighlight.style.color=index===null?'var(--muted)':powerColors[index];powerChart.update('none');}
 const playoffBracket=document.getElementById('playoffBracket'),consolationBracket=document.getElementById('consolationBracket'),playoffNote=document.getElementById('playoffBracketNote');
function renderPlayoffBracket(){
  playoffBracket.replaceChildren();consolationBracket.replaceChildren();playoffBracket.classList.add('bracket');consolationBracket.classList.add('bracket');
  playoffNote.textContent=D.bracket_live_week?`Includes in-progress Week ${D.bracket_live_week} scores; refreshes with dashboard updates.`:'Based on current standings; refreshes with dashboard updates.';
  const entrants=D.playoff_teams||[];
  const consolation=D.consolation_teams||[];
  if(!D.playoff_team_count||!entrants.length){const p=document.createElement('p');p.className='muted';p.textContent='ESPN does not report playoff teams for this league.';playoffBracket.append(p);}
  const roundInfo=D.playoff_rounds||[];
  function roundHeading(round,index,fallback,isConsolation=false){const h=document.createElement('h3');const info=roundInfo[index];h.textContent=isConsolation?`Consolation Round ${index+1}`:(info?.label||fallback);if(info){const date=document.createElement('span');date.className='bracket-date';date.textContent=`NFL week${info.nfl_weeks?.length===1?'':'s'} ${(info.nfl_weeks||[]).join(', ')} · ${info.date_range}`;h.append(date);}return h;}
  function teamLabel(team){return team?`#${team.seed} · ${teamAbbrev(team.team)} (${team.record})`:null;}
  function addColumn(host,index,label,matches,final=false,isConsolation=false){const col=document.createElement('div');col.className='bracket-round'+(final?' bracket-final':'');col.append(roundHeading(roundInfo[index],index,label,isConsolation));matches.forEach(match=>{const card=document.createElement('div');card.className='bracket-match';match.forEach(sideLabel=>{const side=document.createElement('div');side.className='bracket-side'+(!sideLabel||/^(Winner|Loser) of/.test(sideLabel)||sideLabel==='BYE'?' muted':'');side.textContent=sideLabel||'TBD';card.append(side);});col.append(card);});host.append(col);}
  function buildSingleElimination(host,rows,isConsolation=false){
    if(rows.length<2){const p=document.createElement('p');p.className='muted';p.textContent='Not enough teams are available to draw this bracket.';host.append(p);return;}
    let size=1;while(size<rows.length)size*=2;let seedOrder=[1,2];for(let n=4;n<=size;n*=2)seedOrder=seedOrder.flatMap(seed=>[seed,n+1-seed]);
     const bySeed=new Map(rows.map((row,index)=>[Number(isConsolation?index+1:row.seed),teamLabel(row)]));let nodes=seedOrder.map(seed=>bySeed.get(seed)||null),roundIndex=0,gameNumber=1;
    while(nodes.length>1){const matches=[],next=[];for(let i=0;i<nodes.length;i+=2){const left=nodes[i],right=nodes[i+1],number=gameNumber++;matches.push([left||'BYE',right||'BYE']);next.push(left&&!right?left:right&&!left?right:`Winner of Game ${number}`);}const fallback=roundIndex===0?(isConsolation?'Consolation Round 1':(size===4?'Semifinals':'First Round')):matches.length===1?(isConsolation?'Consolation Championship':'Championship'):`Round ${roundIndex+1}`;addColumn(host,roundIndex,fallback,matches,matches.length===1,isConsolation);nodes=next;roundIndex++;}
  }
  if(entrants.length)buildSingleElimination(playoffBracket,entrants);
  if(consolation.length)buildSingleElimination(consolationBracket,consolation,true);
  else{const p=document.createElement('p');p.className='muted';p.textContent='No consolation-round teams are available.';consolationBracket.append(p);}
}
renderPlayoffBracket();
function bindSubtabs(buttonSelector,viewSelector,prefix,key){document.querySelectorAll(buttonSelector).forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll(buttonSelector).forEach(other=>{const active=other===button;other.classList.toggle('active',active);other.setAttribute('aria-selected',String(active));});document.querySelectorAll(viewSelector).forEach(view=>view.classList.toggle('active',view.id===prefix+button.dataset[key]));requestAnimationFrame(()=>document.querySelectorAll('.panel.active canvas').forEach(canvas=>Chart.getChart(canvas)?.resize()));}));}
bindSubtabs('.league-subtab','#panel-league .league-view','league-view-','leagueView');bindSubtabs('.team-subtab','#panel-team .team-view','team-view-','teamView');bindSubtabs('.luck-subtab','#panel-luck .luck-view','luck-view-','luckView');

const luckData=new Map(D.season.map(r=>[r.team,r]));const luckLine=new Chart(document.getElementById('luckLine'),{type:'line',data:{labels:[],datasets:[{label:'Expected wins',data:[],borderColor:colors[0],borderWidth:3,pointRadius:3},{label:'Actual wins',data:[],borderColor:colors[1],borderWidth:3,pointRadius:3,fill:{target:'-1',above:'rgba(63,185,80,.25)',below:'rgba(248,81,73,.25)'}}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{y:{beginAtZero:true,max:Math.max(1,Number(D.completed_week)||0),title:{display:true,text:'Cumulative wins (maximum possible = completed weeks)'}}}}});
function showLuck(team){const r=luckData.get(team);const weeks=r?.weeks||[];luckLine.options.plugins.title.text=teamAbbrev(team);luckLine.data.labels=['Start',...weeks.map(w=>'W'+w)];luckLine.data.datasets[0].data=[0,...(r?.cum_xw||[])];luckLine.data.datasets[1].data=[0,...(r?.cum_actual||[])];luckLine.update();document.getElementById('luckNote').textContent=r?`Actual ${r.actual.toFixed(1)} · Expected ${r.xw.toFixed(2)} · Luck ${r.luck>=0?'+':''}${r.luck.toFixed(2)} · Expected win rate ${(100*r.xwin_pct).toFixed(1)}%`:'';}const luckButtons=fillTeamButtons('luckTeams',showLuck);
new Chart(document.getElementById('luckBars'),{type:'bar',data:{labels:D.season.map(r=>teamAbbrev(r.team)),datasets:[{data:D.season.map(r=>r.luck),backgroundColor:D.season.map(r=>r.luck>=0?'#3fb950':'#f85149'),borderRadius:4}]},options:{maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>items.length?D.season[items[0].dataIndex]?.team:''}}},scales:{x:{title:{display:true,text:'Actual wins − expected wins'}}}}});
const qualityRows=(D.quality_rankings||[]).map(row=>({...row,opponent_ppg_wins_display:row.opponent_ppg_wins==null?'—':`${Number(row.opponent_ppg_wins).toFixed(1)} PPG`,opponent_ppg_losses_display:row.opponent_ppg_losses==null?'—':`${Number(row.opponent_ppg_losses).toFixed(1)} PPG`}));
renderSortableStatsTable('qualityTable',[{key:'team',label:'Team',render:row=>teamAbbrev(row.team)},{key:'opponent_ppg_wins',label:'Opponent PPG in wins',render:row=>row.opponent_ppg_wins_display},{key:'opponent_ppg_losses',label:'Opponent PPG in losses',render:row=>row.opponent_ppg_losses_display},{key:'win_quality',label:'Win quality'},{key:'loss_quality',label:'Loss quality'},{key:'rating',label:'Net quality'}],qualityRows);
const heat=document.getElementById('heat');const hr=document.createElement('tr');['Team',...Array.from({length:D.completed_week},(_,i)=>'W'+(i+1)),'xW','Actual','Luck'].forEach(x=>{const c=document.createElement('th');c.textContent=x;hr.append(c)});heat.append(hr);D.teams.forEach(team=>{const tr=document.createElement('tr');const name=document.createElement('td');name.textContent=teamAbbrev(team);name.title=team;tr.append(name);(D.heat[team]||[]).forEach(v=>{const td=document.createElement('td');td.textContent=v==null?'—':v.toFixed(2);if(v!=null)td.style.background=`hsl(${120*v},65%,45%)`;tr.append(td)});const r=luckData.get(team)||{xw:0,actual:0,luck:0};[r.xw.toFixed(2),r.actual.toFixed(1),(r.luck>=0?'+':'')+r.luck.toFixed(2)].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td)});heat.append(tr)});

let liveGameWindows=[],liveSamplingWindows=[],liveShadedSpans=[],liveTimestamps=[],liveKickoffTicks=[];const gameActivityPlugin={id:'gameActivity',beforeDatasetsDraw(chart){if(chart.canvas.id!=='liveChart'||!liveShadedSpans.length)return;const {ctx,chartArea,scales}=chart,xScale=scales.x;if(!chartArea||!xScale)return;ctx.save();ctx.fillStyle='rgba(77,163,255,.08)';for(const [start,end] of liveShadedSpans){const x1=xScale.getPixelForValue(start),x2=xScale.getPixelForValue(end),left=Math.max(chartArea.left,Math.min(x1,x2)),right=Math.min(chartArea.right,Math.max(x1,x2));if(right>=left)ctx.fillRect(left,chartArea.top,Math.max(1,right-left),chartArea.bottom-chartArea.top);}ctx.restore();}};Chart.register(gameActivityPlugin);
 let liveChart=new Chart(document.getElementById('liveChart'),{type:'line',data:{datasets:[{label:'Win probability',data:[],borderColor:colors[0],backgroundColor:colors[0],fill:{target:{value:50},above:'rgba(245,166,35,.28)',below:'rgba(77,163,255,.25)'},pointRadius:0,borderWidth:2.5,tension:.18},{label:'50%',data:[],borderColor:'#c9d4de',borderDash:[7,5],borderWidth:1.5,pointRadius:0,fill:false}]},options:{maintainAspectRatio:false,animation:false,plugins:{legend:{display:false},title:{display:false},tooltip:{filter:item=>item.datasetIndex===0,callbacks:{title:items=>{const index=Math.max(0,Math.min(liveTimestamps.length-1,Math.round(items[0]?.parsed.x||0)));return liveTimestamps.length?new Date(liveTimestamps[index]).toLocaleString([],{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'';},label:item=>`${document.getElementById('liveTopTeam').textContent} win probability: ${Number(item.parsed.y).toFixed(1)}%`}}},interaction:{mode:'index',intersect:false},scales:{y:{min:0,max:100,title:{display:true,text:'Home team win probability (%)'}},x:{type:'linear',ticks:{autoSkip:true,maxTicksLimit:12,maxRotation:45,callback:value=>{const kickoff=liveKickoffTicks.find(item=>Math.abs(item.x-Number(value))<.02);const index=Math.max(0,Math.min(liveTimestamps.length-1,Math.round(Number(value))));const date=liveTimestamps[index]?new Date(liveTimestamps[index]):null;if(kickoff)return 'Start · '+new Date(kickoff.time).toLocaleString([],{weekday:'short',hour:'numeric',minute:'2-digit'});return date?date.toLocaleString([],{weekday:'short',hour:'numeric',minute:'2-digit'}):'';}}}}}});
 const multiverse=D.fantasy_multiverse||{},multiverseTeamsHost=document.getElementById('multiverseTeams'),multiverseColors=D.teams.map((_,i)=>`hsl(${Math.round(360*i/Math.max(D.teams.length,1))} 78% 58%)`),multiverseBins=Array.from({length:Math.max(1,(multiverse.weeks_used||[]).length+1)},(_,i)=>i),multiverseCounts=multiverse.distributions||{};
 const multiverseSummaryRows=D.teams.map(team=>{const actual=Number(multiverse.actual_wins?.[team]||0),counts=multiverseCounts[team]||[],total=Number(multiverse.simulations||counts.reduce((sum,row)=>sum+Number(row.count||0),0)),weighted=counts.reduce((sum,row)=>sum+Number(row.wins||0)*Number(row.count||0),0),atOrBelow=counts.reduce((sum,row)=>sum+(Number(row.wins||0)<=actual?Number(row.count||0):0),0);return{team,actual_wins:actual,average_wins:total?weighted/total:null,win_percentile:total?100*atOrBelow/total:null};});
function renderMultiverseSummary(){const table=document.getElementById('multiverseSummaryTable'),cols=[['team','Team'],['actual_wins','Actual wins'],['average_wins','Average wins'],['win_percentile','Wins percentile']];let key='win_percentile',direction=-1;const hueColor=(value,values)=>{if(value===null||value===undefined)return 'var(--muted)';const lo=Math.min(...values.filter(v=>v!=null)),hi=Math.max(...values.filter(v=>v!=null)),ratio=hi>lo?(value-lo)/(hi-lo):1;return `hsl(${120*Math.max(0,Math.min(1,ratio))},85%,58%)`;};function draw(){const ordered=[...multiverseSummaryRows].sort((a,b)=>{const av=a[key],bv=b[key];return (key==='team'?direction*String(av).localeCompare(String(bv)):direction*((av??-Infinity)-(bv??-Infinity)))||D.teams.indexOf(a.team)-D.teams.indexOf(b.team);});table.replaceChildren();const head=document.createElement('tr');cols.forEach(([column,label])=>{const th=document.createElement('th');th.textContent=label+(column===key?(direction<0?' ▼':' ▲'):'');th.className='sortable';th.title='Sort by '+label;th.addEventListener('click',()=>{if(key===column)direction*=-1;else{key=column;direction=column==='team'?1:-1;}draw();});head.append(th);});table.append(head);const colorKeys=['win_percentile'],colorValues=Object.fromEntries(colorKeys.map(k=>[k,multiverseSummaryRows.map(row=>row[k])]));ordered.forEach(row=>{const tr=document.createElement('tr');cols.forEach(([column])=>{const td=document.createElement('td');if(column==='team'){td.textContent=teamAbbrev(row.team);td.title=row.team;}else{const value=row[column];td.textContent=value==null?'—':column==='win_percentile'?`${value.toFixed(1)}%`:value.toFixed(2);if(colorKeys.includes(column)){td.style.color=hueColor(value,colorValues[column]);td.style.fontWeight='700';}}tr.append(td);});table.append(tr);});}draw();}
 renderMultiverseSummary();
function multiverseProbability(team){const rows=multiverseCounts[team]||[],total=multiverse.simulations||1;return multiverseBins.map(wins=>{const item=rows.find(row=>row.wins===wins);return item?100*item.count/total:0;});}
const multiverseMarker={id:'multiverseActualMarker',afterDraw(chart){const actual=chart.$actualWins;if(actual===undefined||actual===null)return;const x=chart.scales.x.getPixelForValue(actual);if(!Number.isFinite(x))return;const {ctx,chartArea}=chart;ctx.save();ctx.strokeStyle='#f5a623';ctx.lineWidth=2;ctx.setLineDash([7,5]);ctx.beginPath();ctx.moveTo(x,chartArea.top);ctx.lineTo(x,chartArea.bottom);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#f5a623';ctx.font='12px system-ui';ctx.textAlign='center';ctx.fillText('Actual: '+actual,x,chartArea.top+14);ctx.restore();}};Chart.register(multiverseMarker);
let selectedMultiverseTeam=D.teams[0]||'',pinnedMultiverseIndex=null;
const multiverseTeamChart=new Chart(document.getElementById('multiverseTeamChart'),{type:'bar',data:{labels:multiverseBins.map(String),datasets:[{label:'Probability',data:[],backgroundColor:'rgba(77,163,255,.65)',borderColor:'#4da3ff',borderWidth:1}]},options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:item=>item.raw.toFixed(2)+'% of simulated schedules'}}},scales:{x:{title:{display:true,text:'Wins'}},y:{beginAtZero:true,title:{display:true,text:'Schedule probability (%)'}}}}});
function showMultiverseTeam(team){selectedMultiverseTeam=team;const index=D.teams.indexOf(team),probabilities=multiverseProbability(team);multiverseTeamChart.data.datasets[0].data=probabilities;multiverseTeamChart.data.datasets[0].backgroundColor=`${multiverseColors[index]}`;multiverseTeamChart.data.datasets[0].borderColor=multiverseColors[index];multiverseTeamChart.$actualWins=multiverse.actual_wins?.[team]??null;multiverseTeamChart.options.plugins.title={display:true,text:teamAbbrev(team)+' · wins under simulated schedules'};multiverseTeamChart.update();document.getElementById('multiverseTeamLabel').textContent=`Real schedule: ${multiverse.actual_wins?.[team]??0} wins · ${multiverse.simulations||0} schedules`;
 [...multiverseTeamsHost.children].forEach(button=>{const active=button.title===team;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});}
function highlightMultiverseLine(index){const team=index===null?null:D.teams[index];multiverseAllChart.data.datasets.forEach((dataset,i)=>{dataset.borderColor=index===null?multiverseColors[i]:(i===index?multiverseColors[i]:'rgba(160,170,180,.18)');dataset.backgroundColor=dataset.borderColor;dataset.borderWidth=i===index?4:1.5;dataset.pointRadius=i===index?3:0;});multiverseAllChart.$actualWins=team?multiverse.actual_wins?.[team]:null;const label=document.getElementById('multiverseAllLabel');label.textContent=team?`${team} · ${multiverse.actual_wins?.[team]??0} wins on the real schedule`:'Hover over or click a line to highlight a team.';label.style.color=index===null?'var(--muted)':multiverseColors[index];multiverseAllChart.update('none');}
const multiverseAllChart=new Chart(document.getElementById('multiverseAllChart'),{type:'line',data:{labels:multiverseBins.map(String),datasets:D.teams.map((team,index)=>({label:team,data:multiverseProbability(team),borderColor:multiverseColors[index],backgroundColor:multiverseColors[index],borderWidth:1.5,pointRadius:1,pointHoverRadius:5,tension:.18}))},options:{maintainAspectRatio:false,interaction:{mode:'nearest',intersect:false},plugins:{legend:{display:false},tooltip:{callbacks:{label:item=>`${D.teams[item.datasetIndex]}: ${item.raw.toFixed(2)}%`}}},scales:{x:{title:{display:true,text:'Wins'}},y:{beginAtZero:true,title:{display:true,text:'Schedule probability (%)'}}},onHover:(event,elements)=>{if(pinnedMultiverseIndex===null)highlightMultiverseLine(elements[0]?.datasetIndex??null);},onClick:(event,elements)=>{const index=elements[0]?.datasetIndex;if(index===undefined){pinnedMultiverseIndex=null;highlightMultiverseLine(null);}else{pinnedMultiverseIndex=pinnedMultiverseIndex===index?null:index;highlightMultiverseLine(pinnedMultiverseIndex);if(pinnedMultiverseIndex!==null)showMultiverseTeam(D.teams[pinnedMultiverseIndex]);}}}});
D.teams.forEach(team=>makeChoiceButton(multiverseTeamsHost,teamAbbrev(team),team,false,()=>{showMultiverseTeam(team);highlightMultiverseLine(D.teams.indexOf(team));}));
showMultiverseTeam(selectedMultiverseTeam);
const pieAcronyms={id:'pieAcronyms',afterDatasetsDraw(chart){if(chart.canvas.id!=='multiverseFirstPie'||!chart.chartArea)return;const arcs=chart.getDatasetMeta(0).data,ctx=chart.ctx;ctx.save();ctx.font='16px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#f4f7fa';arcs.forEach((arc,index)=>{const acronym=chart.$acronyms?.[index]||'',angle=arc.endAngle-arc.startAngle;if(!acronym||angle<=0)return;const radius=arc.outerRadius*.64,chord=2*radius*Math.sin(angle/2),needed=ctx.measureText(acronym).width+10;if(chord<needed)return;const mid=(arc.startAngle+arc.endAngle)/2,x=arc.x+Math.cos(mid)*radius,y=arc.y+Math.sin(mid)*radius;ctx.fillText(acronym,x,y);});ctx.restore();}};Chart.register(pieAcronyms);
function makePieChart(id,counts,title){const entries=D.teams.map(team=>[team,Number(counts[team]||0)]).filter(([,value])=>value>0);const chart=new Chart(document.getElementById(id),{type:'pie',data:{labels:entries.map(([team])=>teamAbbrev(team)),datasets:[{data:entries.map(([,value])=>value),backgroundColor:entries.map(([team])=>multiverseColors[D.teams.indexOf(team)]),borderColor:'#161d26',borderWidth:2}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:title,font:{size:16}},legend:{position:'bottom'},tooltip:{callbacks:{label:item=>{const total=item.dataset.data.reduce((sum,value)=>sum+value,0),team=entries[item.dataIndex]?.[0]||item.label;return `${team}: ${item.raw.toLocaleString()} (${(100*item.raw/(total||1)).toFixed(1)}%)`;}}}}}});chart.$acronyms=entries.map(([team])=>teamAbbrev(team));return chart;}
makePieChart('multiverseFirstPie',multiverse.first_place_counts||{},multiverse.first_place_label||'First-place finishes');

const liveWeeksHost=document.getElementById('liveWeeks'),liveMatchupsHost=document.getElementById('liveMatchups'),liveNote=document.getElementById('liveNote'),liveCache={};let selectedLiveWeek='',selectedLiveMatchup=0;function makeChoiceButton(host,label,title,active,onClick){const b=document.createElement('button');b.type='button';b.className='team-button'+(active?' active':'');b.textContent=label;b.title=title;b.setAttribute('aria-label',title);b.setAttribute('aria-pressed',String(active));b.addEventListener('click',onClick);host.append(b);return b;}function fillLiveWeeks(){const keys=(D.live_weeks||[]).map(Number).filter(Number.isFinite).sort((a,b)=>b-a);if(!keys.length){makeChoiceButton(liveWeeksHost,'—','No snapshots recorded',false,()=>{});liveNote.textContent='No snapshots recorded';return;}selectedLiveWeek=String(keys[0]);keys.forEach(w=>makeChoiceButton(liveWeeksHost,'W'+w,'Week '+w+(w===D.current_week?' (current)':''),String(w)===selectedLiveWeek,()=>{selectedLiveWeek=String(w);[...liveWeeksHost.children].forEach((b,i)=>{const active=keys[i]===w;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});selectedLiveMatchup=0;loadLive();}));}
function showLive(){
 const snap=liveCache[selectedLiveWeek];
 if(!snap||!snap.matchups){liveNote.textContent='No snapshots recorded for this week.';return;}
 liveMatchupsHost.replaceChildren();
 const order=snap.matchups.map((_,i)=>i).sort((a,b)=>{const rank=i=>snap.matchups[i].some(n=>n.toLowerCase()==='league manager')?0:1;return rank(a)-rank(b)||a-b;});
 order.forEach(i=>{const m=snap.matchups[i],label=teamAbbrev(m[0])+' vs '+teamAbbrev(m[1]);makeChoiceButton(liveMatchupsHost,label,m[0]+' vs '+m[1],i===selectedLiveMatchup,()=>{selectedLiveMatchup=i;showLive();});});
 if(selectedLiveMatchup>=snap.matchups.length)selectedLiveMatchup=0;
 const i=selectedLiveMatchup,rows=(snap.snaps||[]).filter(s=>s[1]?.[i]).sort((a,b)=>a[0]-b[0]);
 const home=snap.matchups[i]?.[0]||'Home',away=snap.matchups[i]?.[1]||'Away';
 document.getElementById('liveTopTeam').textContent=teamAbbrev(home);document.getElementById('liveTopTeam').title=home;
 document.getElementById('liveBottomTeam').textContent=teamAbbrev(away);document.getElementById('liveBottomTeam').title=away;
 liveGameWindows=(snap.game_windows?.[i]||[]).map(window=>[Number(window[0]),Number(window[1])]);
 liveSamplingWindows=(snap.sampling_windows||[]).map(window=>[Number(window[0]),Number(window[1])]);
 liveTimestamps=rows.map(row=>Number(row[0])*1000);
 const indexAt=timestamp=>{
  if(liveTimestamps.length<2)return 0;
  if(timestamp<=liveTimestamps[0])return 0;
  const last=liveTimestamps.length-1;
  if(timestamp>=liveTimestamps[last])return last;
  let low=0,high=last;
  while(high-low>1){const mid=(low+high)>>1;if(liveTimestamps[mid]<=timestamp)low=mid;else high=mid;}
  return low+(timestamp-liveTimestamps[low])/(liveTimestamps[high]-liveTimestamps[low]);
 };
 const asMillis=value=>value<1e12?value*1000:value;
 const rawWindows=[...liveGameWindows,...liveSamplingWindows].map(window=>[asMillis(window[0]),asMillis(window[1])]).sort((a,b)=>a[0]-b[0]);
 const merged=[];
 rawWindows.forEach(([start,end])=>{const last=merged.at(-1);if(last&&start<=last[1])last[1]=Math.max(last[1],end);else merged.push([start,end]);});
 liveShadedSpans=merged.map(([start,end])=>[indexAt(start),indexAt(end)]);
 const kickoffTimes=[...new Set(liveGameWindows.map(window=>asMillis(window[0])).filter(time=>time>=liveTimestamps[0]&&time<=liveTimestamps.at(-1)))];
 liveKickoffTicks=kickoffTimes.map(time=>({x:indexAt(time),time}));
 const points=rows.map((row,index)=>({x:index,y:Number(row[1][i][2])}));
 liveChart.data.datasets[0].label=home+' win probability';liveChart.data.datasets[0].data=points;
 liveChart.data.datasets[1].data=points.map(point=>({x:point.x,y:50}));
 liveChart.options.scales.x.min=0;liveChart.options.scales.x.max=Math.max(1,points.length-1);
 liveChart.options.scales.x.afterBuildTicks=scale=>{
  const values=[...scale.ticks.map(tick=>Number(tick.value)),...liveKickoffTicks.map(tick=>tick.x)].filter(Number.isFinite);
  scale.ticks=[...new Set(values.map(value=>Math.round(value*100)/100))].sort((a,b)=>a-b).map(value=>({value}));
 };
 liveChart.update();
 liveNote.textContent=rows.length?`${rows.length} snapshots · latest ${new Date(rows.at(-1)[0]*1000).toLocaleString()}`:'Waiting for game snapshots.';
}async function loadLive(){const week=selectedLiveWeek;if(!week||!/^\d+$/.test(week)){showLive();return;}try{const res=await fetch(`live_week_${week}.json?t=${Date.now()}`,{cache:'no-store'});if(res.ok)liveCache[week]=await res.json();}catch(_){}showLive();}
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
   document.getElementById('teamTradeTitle').textContent=`${teamAbbrev(selected)} - cumulative received players minus sent players by week`;
  teamTradeChart.data.labels=[...weeklyNet.keys()].map(w=>'W'+w);
  const yMin=Math.min(0,...cumulativeNet),yMax=Math.max(0,...cumulativeNet);teamTradeChart.options.scales.y.min=yMin===yMax?-1:yMin;teamTradeChart.options.scales.y.max=yMin===yMax?1:yMax;
  teamTradeChart.data.datasets[0].data=cumulativeNet;teamTradeChart.data.datasets[1].data=cumulativeNet.map(()=>0);teamTradeChart.update();
  if(!list.length){const p=document.createElement('p');p.className='muted';p.textContent='No accepted trade records are available for this team.';area.append(p);return;}
  list.sort((a,b)=>b.week-a.week).forEach(t=>{
     const side=t.sides.find(s=>s.team===selected),card=document.createElement('div');card.className='trade-card';
    const layout=document.createElement('div');layout.className='trade-card-layout';
    const info=document.createElement('div');info.className='trade-card-info';
     const h=document.createElement('h3'),title=document.createElement('span');title.textContent=`Week ${t.week} · ${t.date} · ${t.sides.map(s=>teamAbbrev(s.team)).join(' ↔ ')}`;title.title=t.sides.map(s=>s.team).join(' ↔ ');
    const tradeBadge=document.createElement('span');tradeBadge.className='badge trade-rating '+ratingClass(side.rating);tradeBadge.textContent=side.rating;h.append(title,tradeBadge);
    const meta=document.createElement('p');meta.textContent=`Received: ${side.incoming.map(p=>p.name).join(', ')||'—'} (${side.incoming_points.toFixed(1)} pts) · Sent: ${side.outgoing.map(p=>p.name).join(', ')||'—'} (${side.outgoing_points.toFixed(1)} pts)`;
    const score=document.createElement('p');score.className='metric';score.textContent=`Net yield: ${side.net_points>=0?'+':''}${side.net_points.toFixed(1)} pts${side.points_per_week===null?'':` · ${side.points_per_week>=0?'+':''}${side.points_per_week.toFixed(2)} pts/week`}`;
    info.append(h,meta,score);layout.append(info);
    const visuals=document.createElement('div');visuals.className='trade-visuals';
    const flow=document.createElement('div');flow.className='trade-player-flow';
    function addFlowGroup(label,players){const group=document.createElement('div');group.className='trade-flow-group';const caption=document.createElement('span');caption.className='trade-flow-label';caption.textContent=label;const list=document.createElement('div');list.className='trade-flow-players';(players||[]).forEach(player=>{const item=document.createElement('div');item.className='trade-flow-player';const image=document.createElement('img');image.src=player.photo_url||'';image.alt=player.name;image.loading='lazy';image.onerror=()=>{const fallback=document.createElement('span');fallback.className='roster-photo';fallback.style.display='grid';fallback.style.placeItems='center';fallback.textContent=String(player.name||'?').split(/\s+/).map(part=>part[0]||'').join('').slice(0,2).toUpperCase();item.replaceChildren(fallback,name);};const name=document.createElement('span');name.textContent=player.name;item.append(image,name);list.append(item);});if(!players?.length){const empty=document.createElement('span');empty.className='muted';empty.textContent='—';list.append(empty);}group.append(caption,list);flow.append(group);}
    addFlowGroup('Sent',side.outgoing);const arrow=document.createElement('span');arrow.className='trade-arrow';arrow.setAttribute('aria-label','traded for');arrow.textContent='→';flow.append(arrow);addFlowGroup('Received',side.incoming);visuals.append(flow);
    const chartBox=document.createElement('div');chartBox.className='trade-mini-chart';
    const rows=side.weekly_points||[];
    if(rows.length){
      const canvas=document.createElement('canvas');chartBox.append(canvas);
      const chart=new Chart(canvas,{type:'line',data:{labels:rows.map(r=>'W'+r.week),datasets:[{label:'Received players',data:rows.map(r=>r.incoming),borderColor:colors[0],fill:{target:1,above:'rgba(63,185,80,.28)',below:'rgba(248,81,73,.28)'},pointRadius:2,borderWidth:2,tension:.2},{label:'Sent players',data:rows.map(r=>r.outgoing),borderColor:colors[1],pointRadius:2,borderWidth:2,tension:.2}]},options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{boxWidth:10,font:{size:10}}},tooltip:{mode:'index',intersect:false}},interaction:{mode:'index',intersect:false},scales:{x:{ticks:{font:{size:9},maxRotation:0}},y:{beginAtZero:true,title:{display:true,text:'Points',font:{size:9}},ticks:{font:{size:9}}}}}});
      individualTradeCharts.add(chart);
    }else{chartBox.className+=' muted';chartBox.textContent='No post-trade scoring weeks yet.';chartBox.style.display='grid';chartBox.style.placeItems='center';chartBox.style.fontSize='12px';}
    visuals.append(chartBox);layout.append(visuals);card.append(layout);area.append(card);
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
