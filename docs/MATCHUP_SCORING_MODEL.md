# Matchup score distributions

Implemented November 2026 in `scoring_model.py`, `expected_wins.py`, and `live_logger.py`.

## Why simulate the team sum?

A sum of lognormal variables is not exactly normal, and generally is not a single lognormal either. A cumulative distribution function describes the probability below a threshold; adding player CDFs does not give the CDF of the team total. Independent scores must be convolved or sampled and added.

The central limit theorem explains why sums of many independent finite-variance scores can become approximately normal. A fantasy lineup has relatively few starters, unequal variances, and potentially skewed outcomes, so that theorem alone does not establish a good normal approximation for a given matchup. The dashboard therefore simulates each starter and adds the draws, retaining the shape of the resulting team distribution. This is a modelling choice based on the mathematical properties, not evidence that lognormal scores have been empirically proven best for this league.

Primary sources:

- [NIST lognormal distribution](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3669.htm): positive support and lognormal moments.
- [NIST normal distribution and central limit theorem](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3661.htm): the normal approximation is an asymptotic result.
- [Berggren, Bounds and Approximations for the Distribution of a Sum of Lognormal Random Variables (2024)](https://arxiv.org/abs/2405.09283): sums generally require approximation or numerical treatment.

## Individual model

For completed weeks before the selected matchup, use actual-to-projected ratios with positive ESPN projections. Fit the sample variance of the logarithm of positive ratios when at least two exist. Otherwise use the prior standard deviation `max(3, 1.7 sqrt(mean))` to set the positive lognormal shape.

Let `n` be the number of usable historical ratios and `n+` the positive count. The positive component receives probability `q = (n+ + 2) / (n + 2)`. Two positive pseudo-observations reduce the impact of one early zero or negative game. The remaining probability samples observed nonpositive ratios, scaled by the current projection. This empirical branch is the only source of simulated negative points; a broad normal curve no longer invents a negative tail. When history is absent the model is entirely positive lognormal.

The requested mean `m` is kept equal to the ESPN projection for the preview. If the empirical branch has mean `a`, the positive branch has mean `(m - (1-q)a) / q`. For log variance `v`, its log mean is `log(positive_mean) - v/2`. This preserves the projected expectation while allowing right skew and observed nonpositive outcomes. The team preview uses 10,000 draws and an equal-width histogram density. Dashed lines show ESPN's projected totals. Missing projections remain missing and make the total partial.

## Live remaining scores

Completed starters contribute no future uncertainty. An unfinished starter normally has remaining mean `max(0, projection - points_so_far)`. If the starter has already beaten that projection while the game is active, the fallback remaining mean is `max(projection, points_so_far, 0) * unplayed_fraction`. This retains positive upside after an early big play. The nonpositive historical branch is scaled by the unplayed fraction; existing scored points are fixed.

The ESPN wrapper can report game completion as only 0 or 100. For unfinished games the model estimates intermediate progress from elapsed time since kickoff over a three-hour game window. This is a timing approximation, not possession-level or game-clock information. In the absence of a kickoff time it uses the wrapper's completion value.

Each of 5,000 live trials samples the remaining score for every starter separately, adds the team's points already scored, and compares final totals. A tie earns half a win. Starters are treated as independent: teammate stacks, opposing players, weather and game scripts are not explicitly correlated. The resulting odds are estimates and require retrospective calibration before making stronger accuracy claims.

Player-card history is loaded in batches for the logger. The preview reuses full scoring history already collected for the roster tab. Current/future scoring periods never enter a historical fit. If history cannot be loaded, the positive prior still works.

## Ten-minute sampling

The five-minute GitHub Actions schedule runs the collector at minutes 3, 8, 13, and so on, offset from the busiest top-of-hour slots. It records at most one observation per ten-minute bucket during scheduled NFL game windows or an active starter's game, including games outside the usual Thursday/Sunday/Monday blocks. Those standard evening/weekend blocks remain ten-minute windows; off-hours use six-hour buckets. [GitHub documents that scheduled jobs can be delayed or dropped under load](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), so ten minutes is the target observation interval rather than a guarantee of execution exactly on the minute. Existing archived observations are retained as recorded; historical probabilities are not recalculated.
