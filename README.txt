Fantasy dashboard update

Replace expected_wins.py and requirements.txt in the root of the FantasyFootballCope repository with these files. Keep the existing workflow, live_week_*.json history, and repository Actions secrets. Commit both files to main. The workflow should rebuild and publish the updated dashboard.

Changes: team, week, and matchup chart selectors are acronym buttons; full names appear on charts; League Manager is ordered first; the Trades tab includes weekly net traded-player points and five rating levels for each trade and each team. espn-api 1.0.1 is required for trade item recovery.
