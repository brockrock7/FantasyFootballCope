# ESPN Fantasy Expected Wins Dashboard

This package adapts the supplied `expected_wins_2026.py` into a public GitHub Pages dashboard. GitHub Actions polls the public ESPN league on a five-minute schedule, saves a live matchup probability snapshot, rebuilds the dashboard, and deploys it. Viewers' browsers also check for a newer score history once a minute.

The live win probability is an estimate. It combines each matchup's current team score with ESPN's projected points for starters whose NFL games are not marked complete, then runs a simple simulation. ESPN score publication and GitHub scheduled workflow timing affect how quickly updates appear. GitHub documents a five-minute minimum interval for scheduled workflows, and says scheduled runs can be delayed.

## Publish the link

1. Create a **public** GitHub repository and upload all files in this folder, including the `.github/workflows/publish.yml` workflow.
2. In the repository, open **Settings > Pages** and set the build and deployment source to **GitHub Actions**.
3. Open **Actions > Update and publish fantasy dashboard** and choose **Run workflow** once. The first run builds the initial site; scheduled runs then update it every five minutes.
4. GitHub shows the public URL under **Settings > Pages**. Share that URL with the league.

The repository must be public for the source and snapshot JSON to be public. Do not add ESPN cookies or account credentials; the supplied script identifies the league as public and the workflow uses no private credentials.

## Local preview

```powershell
python -m pip install -r requirements.txt
python live_logger.py
python expected_wins.py
```

Open `xwins_dashboard_2026.html` in a browser. The script also writes weekly and season CSV summaries.
