# Player weekly score distribution study

Data: NFL regular-season player-games from 2015–2025, scored using the league's current ESPN settings from 2026.
The training period is 2015–2023; 2024–2025 are held out for model comparison.

## Data and scoring

Weekly player stats, team stats, play-by-play, and snap counts are from the public [nflverse data releases](https://github.com/nflverse/nflverse-data). ESPN's public league settings endpoint supplied the current scoring items. Scores are recalculated for this league instead of taking nflverse's default PPR totals.

Only regular-season games with at least one offensive snap are included for QB/RB/WR/TE; kickers need a special-teams snap. This keeps active zero-point games while excluding bye weeks and DNPs. D/ST rows come from each team's weekly defensive stats.

Data coverage: 75,553 offensive-player/kicker active games, 5,790 D/ST games, 81,343 total position-games.

Current ESPN cross-check: 998 rostered player-games matched; mean absolute error 0.015 points; within 0.05 points 98.7%.

The cross-check uses the league's live weekly applied scores. Scoring rules are configured through ESPN's [scoring settings](https://support.espn.com/hc/en-us/articles/360003914032-Scoring-Formats).

| Position | Matched games | Within 0.05 points | Mean absolute error |
|---|---:|---:|---:|
| QB | 118 | 100.0% | 0.000 |
| RB | 251 | 100.0% | 0.000 |
| WR | 340 | 99.4% | 0.012 |
| TE | 125 | 100.0% | 0.000 |
| K | 77 | 93.5% | 0.058 |
| DEF | 87 | 93.1% | 0.075 |

The current league is custom PPR: 1 point per reception, 0.1 per rushing/receiving yard, 0.04 per passing yard, 4-point passing TDs, 6-point rushing/receiving TDs, −2 interceptions, −2 lost fumbles, and −0.5 per QB sack, plus the league's yardage, long-TD, kicking, and defense bonuses. The settings are saved in `scoring_settings.json`.

## What the data says about the normal fit

The normal diagnostic fits each player-season's own mean and SD, then compares its implied low-score probabilities with that season's actual games. This is a shape diagnostic, not a future-week forecast.

| Position | Holdout games | Zero | Negative | Under 1 point | Normal predicts below 0 | Normal predicts under 1 |
|---|---:|---:|---:|---:|---:|---:|
| QB | 1,369 | 3.6% | 7.4% | 16.4% | 4.4% | 5.8% |
| RB | 3,207 | 14.8% | 1.4% | 24.4% | 11.4% | 18.0% |
| WR | 5,292 | 26.8% | 0.7% | 29.1% | 13.0% | 19.4% |
| TE | 3,320 | 40.3% | 0.2% | 41.1% | 15.7% | 26.1% |
| K | 1,086 | 2.3% | 0.9% | 3.4% | 4.0% | 6.1% |
| DEF | 1,088 | 0.2% | 12.8% | 16.7% | 13.6% | 17.4% |

The table shows whether a normal curve assigns too much probability below zero or misses the observed low-score cluster. Exact zero and negative scores are kept as separate outcomes in the proposed display model; they are not smoothed into the positive-score curve.

## Candidate positive-score distributions

For each player-season with at least eight active games and three positive scores, each positive weekly score is divided by that player's positive-game mean. This removes most between-player scoring-level differences and lets the data estimate the within-player shape. Candidate positive components are a normal truncated at zero, Gamma, lognormal, and a two-lognormal mixture. Training uses 2015–2023; reported test log scores use 2024–2025.

| Position | Model with best held-out log score | Held-out mean log score | Observed fraction below 25% of positive mean | Model probability below 25% |
|---|---|---:|---:|---:|
| QB | two_lognormal | -0.741 | 7.7% | 6.9% |
| RB | two_lognormal | -0.918 | 11.0% | 11.8% |
| WR | gamma | -0.842 | 7.2% | 7.0% |
| TE | two_lognormal | -0.815 | 4.5% | 5.8% |
| K | two_lognormal | -0.723 | 5.2% | 5.9% |
| DEF | two_lognormal | -0.890 | 8.9% | 10.3% |

![Position-specific positive-score curves](positive_shape_comparison.svg)

## Recommended player-level curve

Use a three-part model: a position- and player-specific negative-score component, a point mass at exactly zero, and a position-specific positive-score distribution. The positive part should use the held-out winner by position: Gamma for WR; Gamma or the two-lognormal mixture for RB (their scores are effectively tied); and the two-lognormal mixture for QB, TE, K, and DEF. Scale the positive shape to the player's typical positive-game score. Estimate each player's zero and negative rates from their own history, shrinking toward the same-position training baselines when the sample is short. Keep the actual negative-score values as a separate left-tail distribution rather than collapsing them into one probability. The training baseline masses and empirical negative-score distributions are in `fit_parameters.json`.

This is more faithful to the sketch than a normal curve, but pooled raw scores should not be used directly for an individual player: they mix backups and stars. The scale adjustment and explicit zero/negative masses address that distinction. A full opportunity-count × event-outcome simulation would be a later forecasting model, not required for this historical hover visualization.

## Limits

This analysis applies the current 2026 league scoring settings retroactively to 2015–2025 stats; it does not reproduce each historical season's original rules. nflverse weekly player stats omit some ESPN-specific stat corrections, and rare D/ST event categories (especially one-point safeties and unusual return plays) may not match ESPN perfectly. The curve selection is based on held-out distributional log score, but it is not yet integrated into the dashboard.

Raw source files are cached under this folder's ignored `.cache/` directory. nflverse data releases are broadly licensed CC-BY 4.0; see the source repository for attribution details.
