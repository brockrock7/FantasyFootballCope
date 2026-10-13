"""
Expected wins for ESPN fantasy football league 509033444 (2026 season).

Setup (once):   pip install espn-api pandas
Run:            python expected_wins.py
Optional:       python expected_wins.py 8     (only use weeks 1-8)

Outputs two CSVs next to the script:
  xwins_weekly_2026.csv  - every team, every week
  xwins_season_2026.csv  - season summary with xW, rounded xW, and luck
"""
import sys
import pandas as pd
from espn_api.football import League

LEAGUE_ID = 509033444
YEAR = 2026

league = League(league_id=LEAGUE_ID, year=YEAR)  # public league: no cookies needed

# Decide which weeks to include (completed regular-season weeks only)
reg_weeks = getattr(league.settings, "reg_season_count", 14)
if len(sys.argv) > 1:
    last_week = int(sys.argv[1])
elif league.current_week > reg_weeks:
    last_week = reg_weeks  # season is over (or in playoffs): use every regular-season week
else:
    last_week = league.current_week - 1  # in progress: only completed weeks
last_week = min(last_week, reg_weeks)

# Position groups for the radar charts. Points are credited by the PLAYER'S position,
# so a RB started in the FLEX spot counts toward RB, and a TE in FLEX counts toward TE.
GROUPS = ["QB", "RB", "WR", "TE", "K", "DEF"]
POS_TO_GROUP = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "D/ST": "DEF"}
SLOT_TO_GROUP = dict(POS_TO_GROUP)  # fallback if a player's position is missing
unknown_slots = set()


def bucket_points(lineup):
    out = {g: 0.0 for g in GROUPS}
    for p in lineup:
        slot = getattr(p, "slot_position", "")
        if slot in ("BE", "IR"):
            continue  # bench points don't count toward the team score
        group = POS_TO_GROUP.get(getattr(p, "position", "")) or SLOT_TO_GROUP.get(slot)
        if group is None:
            unknown_slots.add(slot or getattr(p, "position", "?"))
            continue
        out[group] += p.points or 0
    return out


rows = []
pos_rows = []
for week in range(1, last_week + 1):
    for m in league.box_scores(week):
        # Skip bye matchups (no opposing team)
        if not hasattr(m.home_team, "team_name") or not hasattr(m.away_team, "team_name"):
            continue
        rows.append({"week": week, "team": m.home_team.team_name, "score": m.home_score,
                     "opp_score": m.away_score})
        rows.append({"week": week, "team": m.away_team.team_name, "score": m.away_score,
                     "opp_score": m.home_score})
        for team, lineup in (
            (m.home_team.team_name, getattr(m, "home_lineup", [])),
            (m.away_team.team_name, getattr(m, "away_lineup", [])),
        ):
            pos_rows.append({"week": week, "team": team, **bucket_points(lineup)})

df = pd.DataFrame(rows)

# Drop any week where nothing has been scored yet
played = df.groupby("week")["score"].transform("sum") > 0
df = df[played].copy()

pos = pd.DataFrame(pos_rows, columns=["week", "team"] + GROUPS)
pos = pos.merge(df[["week", "team"]], on=["week", "team"])  # same weeks as df
if unknown_slots:
    print("Note: ignored unrecognized lineup slots:", sorted(unknown_slots))


df = df.reset_index(drop=True)
df["xW"] = 0.0
for week in df["week"].unique():
    mask = df["week"] == week
    scores = df.loc[mask, "score"].tolist()
    n_others = len(scores) - 1
    df.loc[mask, "xW"] = [
        (sum(1 for o in scores if o < s) + 0.5 * (sum(1 for o in scores if o == s) - 1)) / n_others
        for s in scores
    ]

# Actual result of the real matchup (ties count as half a win)
df["actual_win"] = df.apply(
    lambda r: 1.0 if r.score > r.opp_score else (0.5 if r.score == r.opp_score else 0.0), axis=1
)

df = df.sort_values(["team", "week"])
df["cum_xW"] = df.groupby("team")["xW"].cumsum()
df["cum_actual"] = df.groupby("team")["actual_win"].cumsum()

season = df.groupby("team").agg(
    games=("week", "count"),
    points_for=("score", "sum"),
    actual_wins=("actual_win", "sum"),
    xW=("xW", "sum"),
).reset_index()
season["xWin_pct"] = season["xW"] / season["games"]
season["xW_rounded"] = season["xW"].round(0).astype(int)
season["luck_(W - xW)"] = season["actual_wins"] - season["xW"]
# "League Manager" goes first in every list and chart (matches team name or owner name)
PINNED_TEAM = "league manager"


def _labels(t):
    out = [str(getattr(t, "team_name", ""))]
    for o in getattr(t, "owners", None) or []:
        if isinstance(o, dict):
            first, last = o.get("firstName", ""), o.get("lastName", "")
            out += [str(o.get("displayName", "")), str(first), str(last), f"{first} {last}"]
        else:
            out.append(str(o))
    return out


pinned_names = [t.team_name for t in league.teams
                if any(PINNED_TEAM in s.lower() for s in _labels(t))]
if not pinned_names:
    print(f"Note: no team or owner matching '{PINNED_TEAM}' found; sorting by expected wins only.")
season["_rest"] = ~season["team"].isin(pinned_names)
season = (season.sort_values(["_rest", "xW"], ascending=[True, False])
          .drop(columns="_rest").round(3))

df.round(3).to_csv("xwins_weekly_2026.csv", index=False)
season.to_csv("xwins_season_2026.csv", index=False)

print(season.to_string(index=False))
print("\nSaved xwins_weekly_2026.csv and xwins_season_2026.csv")

# ---------------------------------------------------------------
# Visual dashboard (opens in your browser; needs internet for Chart.js)
# ---------------------------------------------------------------
import json
import os
import webbrowser

teams = season["team"].tolist()  # already sorted by xW, high to low
pivot = df.pivot(index="team", columns="week", values="xW").reindex(teams)
payload = {
    "teams": teams,
    "xW": season["xW"].tolist(),
    "actual": season["actual_wins"].tolist(),
    "luck": season["luck_(W - xW)"].tolist(),
    "weeks": [int(w) for w in pivot.columns],
    "heat": pivot.round(3).values.tolist(),
    "last_week": int(df["week"].max()),
}
cum_x = df.pivot(index="team", columns="week", values="cum_xW").reindex(teams)
cum_a = df.pivot(index="team", columns="week", values="cum_actual").reindex(teams)
pos_tot = pos.groupby("team")[GROUPS].sum().reindex(teams).fillna(0)
live = None  # written by live_logger.py during games
live_file = f"live_week_{league.current_week}.json"
if os.path.exists(live_file):
    with open(live_file, encoding="utf-8") as f:
        live = json.load(f)
payload.update({
    "pinned": pinned_names,
    "current_week": int(league.current_week),
    "live": live,
    "cumX": cum_x.round(3).values.tolist(),
    "cumA": cum_a.round(3).values.tolist(),
    "groups": GROUPS,
    "pos": pos_tot.round(1).values.tolist(),
    "posAvg": pos_tot.mean().round(1).tolist(),
})
data_json = json.dumps(payload).replace("</", "<\\/")

HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expected Wins - Fielding of (football) dreams</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  body { margin:0; background:#0f1419; color:#e6edf3; font-family:system-ui,Segoe UI,Arial,sans-serif; }
  .wrap { max-width:1500px; margin:0 auto; padding:24px 16px 60px; }
  h1 { margin:0 0 4px; font-size:28px; }
  .sub { color:#8b98a5; margin-bottom:24px; }
  .card { background:#161d26; border:1px solid #243040; border-radius:14px; padding:18px; margin-bottom:20px; }
  .card h2 { margin:0 0 4px; font-size:18px; }
  .card p { margin:0 0 12px; color:#8b98a5; font-size:13px; }
  .chartbox { position:relative; height:420px; }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th, td { padding:7px 8px; text-align:center; }
  th { color:#8b98a5; font-weight:600; }
  td.team { text-align:left; white-space:nowrap; font-weight:600; }
  td.cell { color:#0f1419; font-weight:600; border-radius:4px; }
  .scroll { overflow-x:auto; }
  .toolbar { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
  select { background:#0f1419; color:#e6edf3; border:1px solid #243040; border-radius:8px;
           padding:8px 12px; font-size:15px; }
  #lineNote, #liveNote { color:#8b98a5; font-size:14px; }
  .card { min-width:0; }
  .radar-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; width:100%; }
  @media (max-width:900px) { .radar-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
  .rcell { background:#0f1419; border:1px solid #243040; border-radius:12px; padding:10px;
           min-width:0; box-sizing:border-box; overflow:hidden; }
  .rcell h3 { margin:0 0 6px; font-size:14px; text-align:center; }
  .rbox { position:relative; height:210px; width:100%; min-width:0; }
  .rbox canvas { max-width:100%; }
  .banner { background:#1f2a1c; border:1px solid #3fb950; color:#7ee787; border-radius:12px;
            padding:12px 16px; margin-bottom:18px; font-size:17px; font-weight:600; font-style:italic;
            text-align:center; }
</style></head>
<body><div class="wrap">
  <div class="banner">My team actually should be in first place and maybe never lose too.</div>
  <h1>Fielding of (football) dreams</h1>
  <div class="sub" id="sub"></div>

  <div class="card">
    <h2>Season trajectory: expected vs. actual wins</h2>
    <p>Cumulative wins by week. Expected wins = how many teams you would have outscored each week, summed. The shaded gap is green when the team has more actual wins than expected, red when fewer.</p>
    <div class="toolbar">
      <label for="teamSel">Team:</label>
      <select id="teamSel"></select>
      <span id="lineNote"></span>
    </div>
    <div class="chartbox"><canvas id="c3"></canvas></div>
  </div>

  <div class="card">
    <h2>Schedule luck (actual wins minus expected wins)</h2>
    <p>Green = lucky schedule, red = unlucky schedule.</p>
    <div class="chartbox"><canvas id="c2"></canvas></div>
  </div>

  <div class="card">
    <h2>Weekly expected wins heatmap</h2>
    <p>Each cell is the share of the league that team outscored that week (1.00 = highest score of the week).</p>
    <div class="scroll"><table id="heat"></table></div>
  </div>

  <div class="card">
    <h2>Where the points come from</h2>
    <p>Season starter points by position (points from the FLEX spot go to that player's own position). Blue = team, dashed gray = league average. Each axis is scaled so the league's top total at that position (shown in parentheses) reaches the outer edge. Hover for exact points.</p>
    <div class="radar-grid" id="radars"></div>
  </div>

  <div class="card">
    <h2>Live win probability</h2>
    <p>Each team's chance of winning its matchup, recorded through the week's games from current scores plus projections for players still to play. Pick a matchup from the list.</p>
    <div class="toolbar">
      <label for="liveSel">Matchup:</label>
      <select id="liveSel"></select>
      <span id="liveNote"></span>
    </div>
    <div class="chartbox"><canvas id="c4"></canvas></div>
  </div>
</div>
<script>
const D = __DATA__;
document.getElementById('sub').textContent =
  '2026 season, weeks 1-' + D.last_week + '. League Manager first, then sorted by expected wins.';

Chart.defaults.color = '#c9d4de';
Chart.defaults.borderColor = '#243040';

new Chart(document.getElementById('c2'), {
  type: 'bar',
  data: { labels: D.teams, datasets: [{
    label: 'Luck (W - xW)', data: D.luck, borderRadius: 4,
    backgroundColor: D.luck.map(v => v >= 0 ? '#3fb950' : '#f85149')
  }]},
  options: { maintainAspectRatio:false, indexAxis:'y', plugins:{ legend:{ display:false } } }
});

// Heatmap
const t = document.getElementById('heat');
let html = '<tr><th style="text-align:left">Team</th>' +
  D.weeks.map(w => '<th>W' + w + '</th>').join('') + '<th>Total</th></tr>';
D.teams.forEach((name, i) => {
  html += '<tr><td class="team">' + name.replace(/</g,'&lt;') + '</td>';
  D.heat[i].forEach(v => {
    const hue = 120 * v;  // 0 = red, 1 = green
    html += '<td class="cell" style="background:hsl(' + hue + ',65%,55%)">' + v.toFixed(2) + '</td>';
  });
  html += '<td><b>' + D.xW[i].toFixed(2) + '</b></td></tr>';
});
t.innerHTML = html;

// Line chart: cumulative expected vs actual wins, one team at a time
const lineLabels = ['Start'].concat(D.weeks.map(w => 'W' + w));
const sel = document.getElementById('teamSel');
D.teams.forEach((name, i) => {
  const o = document.createElement('option');
  o.value = i; o.textContent = name; sel.appendChild(o);
});
const lineChart = new Chart(document.getElementById('c3'), {
  type: 'line',
  data: { labels: lineLabels, datasets: [
    { label: 'Expected wins', data: [], borderColor: '#4da3ff', backgroundColor: '#4da3ff',
      pointRadius: 4, borderWidth: 3, tension: 0, fill: false },
    { label: 'Actual wins', data: [], borderColor: '#f5a623', backgroundColor: '#f5a623',
      pointRadius: 4, borderWidth: 3, tension: 0,
      fill: { target: '-1', above: 'rgba(63,185,80,0.30)', below: 'rgba(248,81,73,0.30)' } }
  ]},
  options: { maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    scales: { y: { min: 0, max: D.last_week, ticks: { stepSize: 1 },
      title: { display: true, text: 'Cumulative wins' } } } }
});
function showTeam(i) {
  lineChart.data.datasets[0].data = [0].concat(D.cumX[i]);
  lineChart.data.datasets[1].data = [0].concat(D.cumA[i]);
  lineChart.update();
  const x = D.cumX[i][D.cumX[i].length - 1], a = D.cumA[i][D.cumA[i].length - 1];
  const d = a - x;
  document.getElementById('lineNote').textContent =
    'Actual ' + a.toFixed(1) + ' vs expected ' + x.toFixed(2) + ' (' + (d >= 0 ? '+' : '') + d.toFixed(2) + ')';
}
sel.addEventListener('change', () => showTeam(+sel.value));
showTeam(0);

// Radar grid: points by position group, same scale for every team
// Each axis is scaled to the league's best total at that position (outer edge = league max)
const gmax = D.groups.map((g, j) => Math.max(...D.pos.map(r => r[j])));
const norm = vals => vals.map((v, j) => gmax[j] > 0 ? 100 * v / gmax[j] : 0);
const axisLabels = D.groups.map((g, j) => [g, '(max ' + gmax[j].toFixed(0) + ')']);
const radars = document.getElementById('radars');
D.teams.forEach((name, i) => {
  const cell = document.createElement('div');
  cell.className = 'rcell';
  const h = document.createElement('h3'); h.textContent = name;
  const box = document.createElement('div'); box.className = 'rbox';
  const cv = document.createElement('canvas');
  box.appendChild(cv); cell.appendChild(h); cell.appendChild(box); radars.appendChild(cell);
  new Chart(cv, {
    type: 'radar',
    data: { labels: axisLabels, datasets: [
      { label: name, data: norm(D.pos[i]), borderColor: '#4da3ff', backgroundColor: 'rgba(77,163,255,0.30)',
        pointBackgroundColor: '#4da3ff', borderWidth: 2 },
      { label: 'League avg', data: norm(D.posAvg), borderColor: '#8b98a5', borderDash: [5, 4],
        backgroundColor: 'transparent', pointRadius: 0, borderWidth: 1.5 }
    ]},
    options: { maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: items => D.groups[items[0].dataIndex],
        label: ctx => ctx.dataset.label + ': ' +
          (ctx.datasetIndex === 0 ? D.pos[i][ctx.dataIndex] : D.posAvg[ctx.dataIndex]).toFixed(1) + ' pts'
      } } },
      scales: { r: { min: 0, max: 100, ticks: { display: false },
        grid: { color: '#243040' }, angleLines: { color: '#243040' },
        pointLabels: { font: { size: 10 } } } } }
  });
});

// Live win probability (bottom of page)
let L = D.live;  // snapshot embedded when the page was built (may be null)
const liveSel = document.getElementById('liveSel');
const liveNote = document.getElementById('liveNote');
const liveChart = new Chart(document.getElementById('c4'), {
  type: 'line',
  data: { labels: [], datasets: [
    { label: '', data: [], borderColor: '#4da3ff', backgroundColor: '#4da3ff', borderWidth: 2.5, pointRadius: 0, tension: 0 },
    { label: '', data: [], borderColor: '#f5a623', backgroundColor: '#f5a623', borderWidth: 2.5, pointRadius: 0, tension: 0 }
  ]},
  options: { maintainAspectRatio: false, animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { tooltip: { callbacks: { label: c => c.dataset.label + ': ' + c.parsed.y.toFixed(1) + '%' } } },
    scales: { y: { min: 0, max: 100, title: { display: true, text: 'Win probability (%)' } },
              x: { ticks: { maxTicksLimit: 10, maxRotation: 0 } } } }
});
const fmtTime = s => new Date(s * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
const hasPinned = m => D.pinned.includes(m[0]) || D.pinned.includes(m[1]);

function fillLiveSelect() {
  const keep = liveSel.value;
  liveSel.innerHTML = '';
  if (!L || !L.matchups) return;
  L.matchups.map((m, i) => i)
    .sort((a, b) => (hasPinned(L.matchups[b]) - hasPinned(L.matchups[a])) || (a - b))
    .forEach(i => {
      const o = document.createElement('option');
      o.value = i; o.textContent = L.matchups[i][0] + ' vs ' + L.matchups[i][1];
      liveSel.appendChild(o);
    });
  if (keep !== '' && [...liveSel.options].some(o => o.value === keep)) liveSel.value = keep;
}
function showLive() {
  if (!L || !L.snaps || !L.snaps.length) {
    liveChart.data.labels = [];
    liveChart.data.datasets.forEach(d => { d.data = []; d.label = ''; });
    liveChart.update();
    liveNote.textContent = 'No live data yet. It fills in once the logger records snapshots during games.';
    return;
  }
  const i = +liveSel.value, home = L.matchups[i][0], away = L.matchups[i][1];
  const rows = L.snaps.filter(s => s[1][i]);
  liveChart.data.labels = rows.map(s => fmtTime(s[0]));
  liveChart.data.datasets[0].label = home;
  liveChart.data.datasets[0].data = rows.map(s => s[1][i][2]);
  liveChart.data.datasets[1].label = away;
  liveChart.data.datasets[1].data = rows.map(s => 100 - s[1][i][2]);
  liveChart.update();
  const last = rows[rows.length - 1], v = last[1][i];
  liveNote.textContent = 'Week ' + L.week + ': ' + home + ' ' + v[0].toFixed(1) + ' - ' + v[1].toFixed(1) + ' ' + away +
    ' | win prob ' + v[2].toFixed(1) + '% / ' + (100 - v[2]).toFixed(1) + '% | updated ' + fmtTime(last[0]) +
    ' (' + rows.length + ' snapshots)';
}
liveSel.addEventListener('change', showLive);
fillLiveSelect();
showLive();

// When served from a web address (GitHub Pages), check for fresh data every minute
if (location.protocol.startsWith('http')) {
  const poll = () => fetch('live_week_' + D.current_week + '.json?_=' + Date.now(), { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(j => { if (j && j.snaps) { L = j; fillLiveSelect(); showLive(); } })
    .catch(() => {});
  poll();
  setInterval(poll, 60000);
}
</script></body></html>"""

out_path = os.path.abspath("xwins_dashboard_2026.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(HTML.replace("__DATA__", data_json))
print("Saved dashboard:", out_path)
webbrowser.open("file://" + out_path)
