"""Build a four-tab ESPN fantasy football dashboard for GitHub Pages.

Run with: python expected_wins.py

League scores and roster data are public. ESPN_S2 and SWID environment
variables are optional for the dashboard, but ESPN's transaction history may
require them for complete trade records. Never put cookie values in this file.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from espn_api.football import League

LEAGUE_ID = 509033444
YEAR = 2026
ROOT = Path(__file__).resolve().parent
GROUPS = ["QB", "RB", "WR", "TE", "K", "DEF"]
POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "D/ST": "DEF"}


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


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
    for week in range(1, max(league.current_week, 1) + 1):
        try:
            transactions = league.transactions(
                scoring_period=week,
                types={"TRADE_ACCEPT"},
                fill_trade_items=True,
                fill_from="week",
            )
        except Exception as exc:  # keep the rest of the public dashboard available
            # The library raises this when a scoring period has no
            # transactions key. That is normal for quiet weeks; keep looking
            # because later weeks may contain trades.
            if "no transactions found" in str(exc).lower():
                continue
            # ESPN can return a transaction row without the status field
            # required by espn-api's Transaction parser. Skip that scoring
            # period instead of preventing later weeks from being inspected.
            if isinstance(exc, KeyError) and exc.args == ("status",):
                malformed_weeks.append(week)
                print(f"Skipping malformed ESPN transaction data for week {week}: missing status")
                continue
            warning = "ESPN returned an error while reading transaction history. Verify the ESPN_S2 and SWID secrets and check the Actions build log for details."
            print(f"Trade history unavailable for week {week}: {exc}")
            break
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
            if p is not None:
                stats = getattr(p, "stats", {}) or {}
                for scored_week in range(trade["week"] + 1, completed_week + 1):
                    weekly_points = _safe_float((stats.get(scored_week) or stats.get(str(scored_week)) or {}).get("points"))
                    points_after += weekly_points
            moved = {"id": leg["id"], "name": name, "points": round(points_after, 2)}
            by_team[leg["to"]]["incoming"].append(moved)
            by_team[leg["from"]]["outgoing"].append(moved)

        denominator = max(completed_week - trade["week"], 0)
        for tid, side in by_team.items():
            incoming_pts = sum(p["points"] for p in side["incoming"])
            outgoing_pts = sum(p["points"] for p in side["outgoing"])
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
        warning = (warning + " " if warning else "") + f"ESPN returned malformed transaction data for week(s) {skipped}; those week(s) were skipped while later weeks were checked."

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
    max_week = min(max(int(league.current_week), 1), int(getattr(league.settings, "reg_season_count", 14)))
    completed_week = min(max(int(league.current_week) - 1, 0), max_week)
    current_rosters = _current_rosters(league)

    score_rows = []
    position_week = defaultdict(lambda: defaultdict(lambda: {g: 0.0 for g in GROUPS}))
    position_totals = defaultdict(lambda: {g: 0.0 for g in GROUPS})

    for week in range(1, max_week + 1):
        matchups = league.box_scores(week)
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

    weeks_for_stats = list(range(1, max_week + 1))
    weekly_positions = {
        name: [{"week": w, **{g: round(position_week[name][w][g], 2) for g in GROUPS}} for w in weeks_for_stats]
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

    trades, trade_warning, ratings = _trade_data(league, team_names, completed_week)
    return {
        "year": YEAR, "league_id": LEAGUE_ID, "teams": labels, "groups": GROUPS,
        "current_week": int(league.current_week), "completed_week": completed_week,
        "weeks": weeks_for_stats, "season": season, "heat": heat,
        "position_totals": {name: position_totals[name] for name in labels},
        "weekly_positions": weekly_positions,
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
<style>.team-buttons{display:flex;gap:7px;flex-wrap:wrap}.team-button{min-width:48px;padding:7px 10px;font-size:13px;font-weight:700}.team-button.active{background:#183820;border-color:#3fb950;color:#9be5aa}.team-button:focus-visible{outline:2px solid var(--blue);outline-offset:2px}.trade-rating{margin-left:8px}.trade-card h3{display:flex;align-items:center;flex-wrap:wrap;gap:8px}</style>
<style>
:root{color-scheme:dark;--bg:#0f1419;--panel:#161d26;--line:#243040;--muted:#8b98a5;--text:#e6edf3;--blue:#4da3ff;--gold:#f5a623;--green:#3fb950;--red:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,Segoe UI,Arial,sans-serif}.wrap{max-width:1500px;margin:auto;padding:22px 16px 56px}h1{font-size:27px;margin:0}.sub{color:var(--muted);margin:4px 0 20px}.tabs{display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--line);margin-bottom:18px}.tab{background:transparent;color:var(--muted);border:0;border-bottom:3px solid transparent;padding:12px 16px;font-weight:700;font-size:15px;cursor:pointer}.tab.active{color:var(--text);border-color:var(--blue)}.panel{display:none}.panel.active{display:block}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px;min-width:0}.card h2{font-size:18px;margin:0 0 4px}.card p{font-size:13px;color:var(--muted);margin:0 0 12px}.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 14px}select,button,input{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 11px;font:inherit}button{cursor:pointer}.chartbox{height:390px;position:relative}.radar-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.rcell{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px;min-width:0}.rcell h3{text-align:center;margin:0 0 7px;font-size:14px}.rbox{height:210px;position:relative}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:center;padding:8px;border-bottom:1px solid var(--line)}th{color:var(--muted)}td:first-child,th:first-child{text-align:left;white-space:nowrap}.notice{border:1px solid #745d24;background:#2a2418;color:#f3d58b;padding:11px 13px;border-radius:10px;margin:10px 0}.good{border-color:#2c6840;background:#17291c;color:#9be5aa}.trade-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.trade-card{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0}.badge{display:inline-block;border-radius:999px;padding:4px 9px;background:#233141;color:#a8d2ff;font-size:12px;font-weight:700}.badge.gm{background:#183820;color:#8cec9c}.badge.fleeced{background:#3a1d1d;color:#ff9c96}.badge.inmix{background:#25313b;color:#c8d1d9}.player-select{min-height:155px;width:100%}.metric{font-weight:700}.muted{color:var(--muted)}.calculator-result{font-size:17px;margin-top:12px}@media(max-width:900px){.radar-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.chartbox{height:330px}}@media(max-width:520px){.wrap{padding:15px 10px}.tab{padding:10px 8px;font-size:13px}.radar-grid{grid-template-columns:1fr}.chartbox{height:290px}}
</style></head><body><main class="wrap">
<h1>Fielding of (football) dreams</h1><div class="sub" id="subtitle"></div>
<nav class="tabs" role="tablist" aria-label="Dashboard sections">
<button class="tab active" data-tab="team" role="tab" aria-selected="true">Team Stats</button><button class="tab" data-tab="luck" role="tab" aria-selected="false">Luck</button><button class="tab" data-tab="live" role="tab" aria-selected="false">Live Win %</button><button class="tab" data-tab="trades" role="tab" aria-selected="false">Trades</button></nav>

<section class="panel active" id="panel-team" role="tabpanel">
<article class="card"><h2>Season points by position</h2><p>Starter points grouped by each player's position. Players in FLEX count toward their own position.</p><div id="radars" class="radar-grid"></div></article>
<article class="card"><h2>Weekly points by position</h2><p>All available weeks for one team. Each bar is stacked by the six positions.</p><div class="toolbar"><span>Team:</span><div id="positionTeams" class="team-buttons" aria-label="Choose team"></div></div><div class="chartbox"><canvas id="positionChart"></canvas></div></article>
</section>

<section class="panel" id="panel-luck" role="tabpanel">
<article class="card"><h2>Expected wins and actual wins</h2><p>Expected wins compare each team's score with every other team that week. The shaded gap shows schedule luck.</p><div class="toolbar"><span>Team:</span><div id="luckTeams" class="team-buttons" aria-label="Choose team"></div><span id="luckNote" class="muted"></span></div><div class="chartbox"><canvas id="luckLine"></canvas></div></article>
<article class="card"><h2>Schedule luck (actual wins minus expected wins)</h2><p>Positive means a team won more often than its points would predict against the league; negative means fewer.</p><div class="chartbox"><canvas id="luckBars"></canvas></div></article>
<article class="card"><h2>Weekly expected win share</h2><p>Each cell is the share of league teams outscored that week. 1.00 is the top score.</p><div class="scroll"><table id="heat"></table></div></article>
</section>

<section class="panel" id="panel-live" role="tabpanel">
<article class="card"><h2>Live matchup win probability</h2><p>Estimated from live fantasy scores and remaining ESPN player projections. Past weeks appear when snapshots were recorded.</p><div class="toolbar"><span>Week:</span><div id="liveWeeks" class="team-buttons" aria-label="Choose week"></div><span>Matchup:</span><div id="liveMatchups" class="team-buttons" aria-label="Choose matchup"></div><span id="liveNote" class="muted"></span></div><div class="chartbox"><canvas id="liveChart"></canvas></div></article>
</section>

<section class="panel" id="panel-trades" role="tabpanel">
<article class="card"><h2>Trades by team</h2><p>Every completed trade involving the selected team, with incoming and outgoing players, points earned after the trade, and an individual trade rating.</p><div class="toolbar"><span>Team:</span><div id="tradeTeams" class="team-buttons" aria-label="Choose team"></div><span id="teamRating"></span></div><div id="tradeNotice" class="notice" style="display:none"></div><div id="teamTrades"></div></article>
<article class="card"><h2>Trade calculator</h2><p>Choose players from two rosters. The estimate compares their projected remaining season points from ESPN.</p><div class="trade-grid"><div><label for="calcTeamA">Your team</label><select id="calcTeamA"></select><label for="givePlayers">Players you give</label><select class="player-select" id="givePlayers" multiple></select></div><div><label for="calcTeamB">Other team</label><select id="calcTeamB"></select><label for="getPlayers">Players you receive</label><select class="player-select" id="getPlayers" multiple></select></div></div><div class="toolbar"><button id="calcTrade">Evaluate trade</button></div><div id="calcResult" class="calculator-result"></div></article>
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
fillTeams('calcTeamA');fillTeams('calcTeamB');

const radars=document.getElementById('radars');const maxima=D.groups.map(g=>Math.max(1,...D.teams.map(t=>D.position_totals[t][g]||0)));const normalized=a=>a.map((v,i)=>100*v/maxima[i]);
D.teams.forEach((team,i)=>{const cell=document.createElement('div');cell.className='rcell';const title=document.createElement('h3');title.textContent=team;const box=document.createElement('div');box.className='rbox';const cv=document.createElement('canvas');box.append(cv);cell.append(title,box);radars.append(cell);new Chart(cv,{type:'radar',data:{labels:D.groups.map((g,j)=>[g,'(max '+maxima[j].toFixed(0)+')']),datasets:[{label:team,data:normalized(D.groups.map(g=>D.position_totals[team][g]||0)),borderColor:colors[0],backgroundColor:'rgba(77,163,255,.28)',borderWidth:2},{label:'League avg',data:normalized(D.groups.map(g=>D.teams.reduce((s,t)=>s+(D.position_totals[t][g]||0),0)/D.teams.length)),borderColor:'#8b98a5',borderDash:[5,4],backgroundColor:'transparent',pointRadius:0}]},options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.dataset.label+': '+(c.datasetIndex===0?(D.position_totals[team][D.groups[c.dataIndex]]||0):(D.teams.reduce((s,t)=>s+(D.position_totals[t][D.groups[c.dataIndex]]||0),0)/D.teams.length)).toFixed(1)+' pts'}}},scales:{r:{min:0,max:100,ticks:{display:false},grid:{color:'#243040'},angleLines:{color:'#243040'}}}}});});

const posChart=new Chart(document.getElementById('positionChart'),{type:'bar',data:{labels:D.weeks.map(w=>'W'+w),datasets:D.groups.map((g,i)=>({label:g,data:[],backgroundColor:colors[i]}))},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true,title:{display:true,text:'Fantasy points'}}}}});
function showPosition(t){const rows=D.weekly_positions[t]||[];posChart.options.plugins.title.text=t;posChart.data.datasets.forEach((ds,i)=>ds.data=rows.map(r=>r[D.groups[i]]||0));posChart.update();}
const positionButtons=fillTeamButtons('positionTeams',showPosition);
const luckData=new Map(D.season.map(r=>[r.team,r]));const luckLine=new Chart(document.getElementById('luckLine'),{type:'line',data:{labels:[],datasets:[{label:'Expected wins',data:[],borderColor:colors[0],borderWidth:3,pointRadius:3},{label:'Actual wins',data:[],borderColor:colors[1],borderWidth:3,pointRadius:3,fill:{target:'-1',above:'rgba(63,185,80,.25)',below:'rgba(248,81,73,.25)'}}]},options:{maintainAspectRatio:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{y:{beginAtZero:true,title:{display:true,text:'Cumulative wins'}}}}});
function showLuck(team){const r=luckData.get(team);const weeks=r?.weeks||[];luckLine.options.plugins.title.text=team;luckLine.data.labels=['Start',...weeks.map(w=>'W'+w)];luckLine.data.datasets[0].data=[0,...(r?.cum_xw||[])];luckLine.data.datasets[1].data=[0,...(r?.cum_actual||[])];luckLine.update();document.getElementById('luckNote').textContent=r?`Actual ${r.actual.toFixed(1)} · Expected ${r.xw.toFixed(2)} · Luck ${r.luck>=0?'+':''}${r.luck.toFixed(2)} · Expected win rate ${(100*r.xwin_pct).toFixed(1)}%`:'';}const luckButtons=fillTeamButtons('luckTeams',showLuck);
new Chart(document.getElementById('luckBars'),{type:'bar',data:{labels:D.season.map(r=>r.team),datasets:[{data:D.season.map(r=>r.luck),backgroundColor:D.season.map(r=>r.luck>=0?'#3fb950':'#f85149'),borderRadius:4}]},options:{maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{title:{display:true,text:'Actual wins − expected wins'}}}}});
const heat=document.getElementById('heat');const hr=document.createElement('tr');['Team',...Array.from({length:D.completed_week},(_,i)=>'W'+(i+1)),'xW','Actual','Luck'].forEach(x=>{const c=document.createElement('th');c.textContent=x;hr.append(c)});heat.append(hr);D.teams.forEach(team=>{const tr=document.createElement('tr');const name=document.createElement('td');name.textContent=team;tr.append(name);(D.heat[team]||[]).forEach(v=>{const td=document.createElement('td');td.textContent=v==null?'—':v.toFixed(2);if(v!=null)td.style.background=`hsl(${120*v},65%,45%)`;tr.append(td)});const r=luckData.get(team)||{xw:0,actual:0,luck:0};[r.xw.toFixed(2),r.actual.toFixed(1),(r.luck>=0?'+':'')+r.luck.toFixed(2)].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td)});heat.append(tr)});

let liveChart=new Chart(document.getElementById('liveChart'),{type:'line',data:{labels:[],datasets:[{label:'',data:[],borderColor:colors[0],pointRadius:0,borderWidth:2.5},{label:'',data:[],borderColor:colors[1],pointRadius:0,borderWidth:2.5}]},options:{maintainAspectRatio:false,animation:false,plugins:{title:{display:true,text:''}},interaction:{mode:'index',intersect:false},scales:{y:{min:0,max:100,title:{display:true,text:'Win probability (%)'}},x:{ticks:{maxTicksLimit:10,maxRotation:0}}}}});
const liveWeeksHost=document.getElementById('liveWeeks'),liveMatchupsHost=document.getElementById('liveMatchups'),liveNote=document.getElementById('liveNote'),liveCache={};let selectedLiveWeek='',selectedLiveMatchup=0;function makeChoiceButton(host,label,title,active,onClick){const b=document.createElement('button');b.type='button';b.className='team-button'+(active?' active':'');b.textContent=label;b.title=title;b.setAttribute('aria-label',title);b.setAttribute('aria-pressed',String(active));b.addEventListener('click',onClick);host.append(b);return b;}function fillLiveWeeks(){const keys=(D.live_weeks||[]).map(Number).filter(Number.isFinite).sort((a,b)=>b-a);if(!keys.length){makeChoiceButton(liveWeeksHost,'—','No snapshots recorded',false,()=>{});liveNote.textContent='No snapshots recorded';return;}selectedLiveWeek=String(keys[0]);keys.forEach(w=>makeChoiceButton(liveWeeksHost,'W'+w,'Week '+w+(w===D.current_week?' (current)':''),String(w)===selectedLiveWeek,()=>{selectedLiveWeek=String(w);[...liveWeeksHost.children].forEach((b,i)=>{const active=keys[i]===w;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});selectedLiveMatchup=0;loadLive();}));}
function showLive(){const snap=liveCache[selectedLiveWeek];if(!snap||!snap.matchups){liveNote.textContent='No recorded snapshots for this week.';return;}liveMatchupsHost.replaceChildren();const order=snap.matchups.map((_,i)=>i).sort((a,b)=>{const rank=i=>snap.matchups[i].some(n=>n.toLowerCase()==='league manager')?0:1;return rank(a)-rank(b)||a-b;});order.forEach(i=>{const m=snap.matchups[i],label=teamAbbrev(m[0])+' vs '+teamAbbrev(m[1]);makeChoiceButton(liveMatchupsHost,label,m[0]+' vs '+m[1],i===selectedLiveMatchup,()=>{selectedLiveMatchup=i;showLive();});});if(selectedLiveMatchup>=snap.matchups.length)selectedLiveMatchup=0;const i=selectedLiveMatchup;const rows=(snap.snaps||[]).filter(s=>s[1]?.[i]);const home=snap.matchups[i]?.[0]||'Home',away=snap.matchups[i]?.[1]||'Away';liveChart.options.plugins.title.text=home+' vs '+away;liveChart.data.labels=rows.map(s=>new Date(s[0]*1000).toLocaleTimeString([],{weekday:'short',hour:'numeric',minute:'2-digit'}));liveChart.data.datasets[0].label=home;liveChart.data.datasets[1].label=away;liveChart.data.datasets[0].data=rows.map(s=>s[1][i][2]);liveChart.data.datasets[1].data=rows.map(s=>100-s[1][i][2]);liveChart.update();liveNote.textContent=rows.length?`${rows.length} snapshots · latest ${new Date(rows.at(-1)[0]*1000).toLocaleString()}`:'Waiting for game snapshots.';}
async function loadLive(){const week=selectedLiveWeek;if(!week||!/^\d+$/.test(week)){showLive();return;}try{const res=await fetch(`live_week_${week}.json?t=${Date.now()}`,{cache:'no-store'});if(res.ok)liveCache[week]=await res.json();}catch(_){}showLive();}
fillLiveWeeks();loadLive();

const ratingByTeam=new Map(D.trade_ratings.map(r=>[r.team_id,r]));const teamIdByName=new Map(D.teams.map(n=>[n,D.roster_team_ids[n]]));
function ratingClass(label){return label==='GM of the Year'?'gm':label==='Fleeced'?'fleeced':'inmix';}
function renderTrades(selected){const tid=teamIdByName.get(selected);const rating=ratingByTeam.get(String(tid));const badge=document.getElementById('teamRating');badge.className='badge '+ratingClass(rating?.rating||'');badge.textContent=rating?`${rating.rating} · Net ${rating.score.toFixed(2)} pts/week · ${rating.wins} wins / ${rating.losses} losses`:'No rating yet';const area=document.getElementById('teamTrades');area.replaceChildren();const list=D.trades.filter(t=>t.sides.some(s=>s.team===selected));if(!list.length){const p=document.createElement('p');p.className='muted';p.textContent='No trade records are available for this team.';area.append(p);return;}list.sort((a,b)=>b.week-a.week).forEach(t=>{const side=t.sides.find(s=>s.team===selected);const card=document.createElement('div');card.className='trade-card';const h=document.createElement('h3');const title=document.createElement('span');title.textContent=`Week ${t.week} · ${t.date} · ${t.sides.map(s=>s.team).join(' ↔ ')}`;const tradeBadge=document.createElement('span');tradeBadge.className='badge trade-rating '+ratingClass(side.rating);tradeBadge.textContent=side.rating;h.append(title,tradeBadge);const meta=document.createElement('p');meta.textContent=`Received: ${side.incoming.map(p=>p.name).join(', ')||'—'} (${side.incoming_points.toFixed(1)} pts) · Sent: ${side.outgoing.map(p=>p.name).join(', ')||'—'} (${side.outgoing_points.toFixed(1)} pts)`;const score=document.createElement('p');score.className='metric';score.textContent=`Net yield: ${side.net_points>=0?'+':''}${side.net_points.toFixed(1)} pts${side.points_per_week===null?'':` · ${side.points_per_week>=0?'+':''}${side.points_per_week.toFixed(2)} pts/week`}`;card.append(h,meta,score);area.append(card)});}
document.getElementById('tradeNotice').textContent=D.trade_warning;document.getElementById('tradeNotice').style.display=D.trade_warning?'block':'none';const tradeButtons=fillTeamButtons('tradeTeams',renderTrades);
function populatePlayers(teamSelect,playersSelect){const team=teamSelect.value;const players=D.rosters[teamIdByName.get(team)]||[];playersSelect.replaceChildren(...players.map(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=`${p.name} (${p.position}) · ${p.projected_remaining.toFixed(1)} projected remaining`;o.dataset.points=p.projected_remaining;return o;}));}const calcA=document.getElementById('calcTeamA'),calcB=document.getElementById('calcTeamB'),give=document.getElementById('givePlayers'),get=document.getElementById('getPlayers');calcA.addEventListener('change',()=>populatePlayers(calcA,give));calcB.addEventListener('change',()=>populatePlayers(calcB,get));populatePlayers(calcA,give);populatePlayers(calcB,get);document.getElementById('calcTrade').addEventListener('click',()=>{const a=[...give.selectedOptions].reduce((s,o)=>s+Number(o.dataset.points),0);const b=[...get.selectedOptions].reduce((s,o)=>s+Number(o.dataset.points),0);const d=b-a;document.getElementById('calcResult').textContent=`You give ${a.toFixed(1)} projected points and receive ${b.toFixed(1)}. Estimated rest-of-season difference: ${d>=0?'+':''}${d.toFixed(1)} points ${d>0?'in your favor':d<0?'against you':'— even'}.`;});

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
