# Fantasy dashboard update

This package updates the ESPN Fantasy dashboard with four tabs:

- **Team Stats**: season position radars, per-team weekly position bars, and per-team weekly drafted/traded/waivers point bars. Unrecognized acquisition types are shown separately as **Unknown**.
- **Luck**: cumulative expected vs. actual wins, weekly expected-win heatmap, and league schedule-luck ranking.
- **Live Win %**: matchup win-probability histories with a week picker. Snapshots are appended by GitHub Actions every five minutes and the page checks for fresh data once per minute.
- **Trades**: completed trade ledger, realized points and points per week for each side, team ratings, and an ESPN-projection trade calculator.

The live probability and trade calculator are estimates. Win probability uses a simple simulation of remaining ESPN projections. The trade calculator compares projected remaining season points. Trade impact counts players' actual fantasy points in scoring weeks after the trade week.

## Update the existing repository

Copy these files into the root of `FantasyFootballCope`, replacing the existing files with the same names. Put `publish.yml` under `.github/workflows/` (not the repository root). Keep old `live_week_*.json` files so the Live Win % tab retains prior-week snapshots.

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

Open `index.html` in a browser. For trade history, set `ESPN_S2` and `SWID` in the local shell before running.
