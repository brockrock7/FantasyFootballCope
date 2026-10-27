# Fantasy football dashboard

This ESPN Fantasy dashboard includes:

- **League Stats**: standings sorted by playoff position, playoff markers, power rankings, and a projected playoff/consolation bracket.
- **Matchup stats**: win-probability history from live snapshots. GitHub Actions collects snapshots every five minutes, and the page checks for fresh data once per minute.
- **Team Stats**: positional and acquisition breakdowns, starter/bench results, and season-long roster player statistics.
- **Luck Stats**: expected wins, schedule luck, and simulated schedule outcomes.
- **Trades** and **Leaderboard**: completed trade records, trade impact, and season leaders.

Roster averages and standard deviations use a player's completed scoring weeks across all fantasy teams and exclude NFL bye weeks. D/ST rows use NFL team logos when ESPN provides a recognizable team name or abbreviation.

The live probability and trade calculator are estimates. Win probability uses a simulation of remaining ESPN projections. The trade calculator compares projected remaining season points. Trade impact counts players' actual fantasy points in scoring weeks after the trade week.

## League-format portability

Before adapting the dashboard to another league, keep these settings data-driven rather than assuming this league's values:

- Team count, team identifiers, display names, division membership, and team-name abbreviations.
- Playoff qualifiers, division-winner priority, seed tiebreakers, number of byes, consolation rules, reseeding, and matchup length.
- Scoring settings such as PPR, half-PPR, bonuses, fractional scoring, TE premium, and defensive scoring.
- Roster slots and player eligibility, including FLEX, superflex/2QB, tight-end premium, and IDP positions.
- Regular-season length, matchup periods that span multiple NFL weeks, NFL bye weeks, postponed games, and stat corrections.
- Stable player IDs and historical roster ownership so a player's season totals do not reset after a trade or waiver move.
- Team names that truncate cleanly, unique short labels, table width on phones, and chart labels when a league has many teams.

For another platform, keep its API/data collection separate from the dashboard calculations and map its league, team, player, scoring, roster, schedule, and playoff settings into a shared normalized payload.

## Update the existing repository

The GitHub Actions workflow builds `index.html` from `expected_wins.py` and publishes it with the saved `live_week_*.json` snapshots. Keep those snapshot files so the Matchup stats tab retains prior weeks.

In the repository's **Settings > Pages**, set the source to **GitHub Actions**. The workflow publishes on pushes, manual runs, and on a five-minute schedule.

## ESPN secrets for the trade log

The basic dashboard reads the public league. ESPN can restrict complete transaction history, so add these as **repository Actions secrets** if trade records are missing:

- `ESPN_S2`
- `SWID`

In GitHub: **Settings > Secrets and variables > Actions > New repository secret**. Paste each value directly into GitHub's secret form. Do not put cookie values in the repository, in this chat, or in a public file. The workflow passes them to Python as environment variables and does not print them.

## Local run

```powershell
python -m pip install -r requirements.txt
python live_logger.py
python expected_wins.py
```

Open `index.html` in a browser. For private-league trade history, set `ESPN_S2` and `SWID` in the local shell before running.
