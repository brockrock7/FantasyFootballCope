# Player score distribution study

This folder contains a reproducible study of weekly fantasy-point distributions under this league's ESPN scoring settings.

## Run it

From the repository root, run:

```powershell
python analysis/player_score_models/run_analysis.py
```

The script downloads public nflverse files into the ignored `.cache/` directory, reads the current league scoring items saved in `scoring_settings.json`, reconstructs weekly scores, fits candidate distributions, and checks current scores against ESPN. The Python environment needs `numpy` and `pandas`. Use `--refresh-scoring` to retrieve the current ESPN settings again, or `--years FIRST LAST` to limit the historical seasons.

## Main outputs

- `report.md`: method, validation, held-out results, and recommendation.
- `positive_shape_comparison.svg`: holdout score shapes and fitted positive-score curves by position.
- `model_comparison.csv`: held-out log scores for each candidate positive-score model.
- `fit_parameters.json`: fitted positive-score parameters plus training-set zero/negative/positive masses and empirical negative-score distributions by position.
- `position_score_summary.csv` and `normal_fit_diagnostics.csv`: empirical low-score rates and normal-fit comparisons.
- `scored_player_games.csv`: reconstructed historical player-game scores.
- `espn_score_validation.csv`: current ESPN score cross-check by player/game.

The score reconstruction applies the league's current 2026 settings retroactively to 2015–2025; it does not recreate prior seasons' rules. The fitted curves are conditional on a positive score and normalized by each player's positive-game average. Exact-zero and negative outcomes should be modeled as separate components.
