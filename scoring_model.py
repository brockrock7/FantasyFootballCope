"""Individual lognormal score draws shared by matchup previews and live odds.

The positive branch is centred on ESPN's projection. Observed nonpositive
score/projection ratios form a discrete branch; no Gaussian negative tail is
invented. Starter outcomes are currently assumed independent.
"""
from __future__ import annotations

import math
import time


def _number(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def merge_scoring_history(*histories):
    """Keep card fields when a box-score week contains only some of them."""
    merged = {}
    for history in histories:
        for key, row in history.items() if isinstance(history, dict) else []:
            try:
                week = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict):
                merged[week] = {**merged.get(week, {}), **row}
    return merged


def player_score_model(player, week, mean=None, sd=None, history_scale=1.0, historical_rows=None):
    """Build a projection-centred positive lognormal / empirical mixture.

    Only past weeks with actual and positive projected points enter the fit.
    Two positive pseudo-observations shrink the nonpositive frequency for short
    histories; the positive shape uses history with at least two positive ratios.
    """
    projected = max(0.0, _number(getattr(player, "projected_points", 0)))
    mean = projected if mean is None else max(0.0, _number(mean))
    sd = max(3.0, 1.7 * math.sqrt(mean)) if sd is None else max(0.0, _number(sd))
    ratios = []
    rows = {row["week"]: {"points": row.get("score"), "projected_points": row.get("projected")}
            for row in historical_rows or []}
    stats = merge_scoring_history(getattr(player, "stats", {}), rows)
    for key, row in stats.items() if isinstance(stats, dict) else []:
        try:
            past_week = int(key)
        except (TypeError, ValueError):
            continue
        if not 0 < past_week < int(week) or not isinstance(row, dict):
            continue
        score = row.get("points")
        projection = row.get("projected_points", row.get("projectedPoints"))
        if score is not None and _number(projection) > 0:
            ratios.append(_number(score) / _number(projection))
    positive = [value for value in ratios if value > 0]
    nonpositive = [value * projected * history_scale for value in ratios if value <= 0]
    q = (len(positive) + 2) / (len(ratios) + 2)
    negative_mean = sum(nonpositive) / len(nonpositive) if nonpositive else 0.0
    positive_mean = (mean - (1 - q) * negative_mean) / q
    if len(positive) >= 2:
        logs = [math.log(value) for value in positive]
        centre = sum(logs) / len(logs)
        log_variance = sum((value - centre) ** 2 for value in logs) / (len(logs) - 1)
    else:
        log_variance = math.log1p((sd / positive_mean) ** 2) if positive_mean > 0 else 0.0
    log_mean = math.log(positive_mean) - log_variance / 2 if positive_mean > 0 else None
    positive_second = positive_mean ** 2 * math.exp(log_variance)
    nonpositive_second = sum(value ** 2 for value in nonpositive) / len(nonpositive) if nonpositive else 0.0
    variance = max(0.0, q * positive_second + (1 - q) * nonpositive_second - mean ** 2)
    return {"mean": mean, "variance": variance, "log_mean": log_mean,
            "log_sd": math.sqrt(log_variance), "positive_probability": q,
            "nonpositive": nonpositive, "history_games": len(ratios)}


def player_progress(player, timestamp=None):
    """Use kickoff elapsed time when ESPN's wrapper reports only 0 or 100."""
    progress = max(0.0, min(100.0, _number(getattr(player, "game_played", 0))))
    if progress == 0:
        kickoff = getattr(player, "game_date", None)
        if kickoff is not None:
            try:
                elapsed = (timestamp if timestamp is not None else time.time()) - kickoff.timestamp()
                progress = max(0.0, min(100.0, 100 * elapsed / (3 * 60 * 60)))
            except (AttributeError, TypeError, ValueError, OSError):
                pass
    return progress


def sample_player_score(model, rng):
    if model["nonpositive"] and rng.random() >= model["positive_probability"]:
        return rng.choice(model["nonpositive"])
    return rng.lognormvariate(model["log_mean"], model["log_sd"]) if model["log_mean"] is not None else 0.0


def sample_team_scores(models, rng, count):
    return [sum(sample_player_score(model, rng) for model in models) for _ in range(count)]


def score_density(samples, bins=100):
    """Equal-width empirical density; no normal or lognormal team assumption."""
    if not samples:
        return []
    low, high = min(samples), max(samples)
    width = max(1.0, high - low) / bins
    counts = [0] * bins
    for value in samples:
        index = min(bins - 1, max(0, int((value - low) / width)))
        counts[index] += 1
    return [{"x": round(low + (index + .5) * width, 3),
             "y": count / (len(samples) * width)} for index, count in enumerate(counts)]
