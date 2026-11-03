import importlib.util
import ast
import math
from pathlib import Path
import random
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from scoring_model import player_progress, player_score_model, sample_player_score, sample_team_scores, score_density, merge_scoring_history, central_score_interval


def load_logger():
    # The model tests also run with the standard library alone, without ESPN
    # credentials or a network call. League construction is never exercised.
    football = ModuleType("espn_api.football")
    football.League = object
    spec = importlib.util.spec_from_file_location("logger_under_test", Path(__file__).parents[1] / "live_logger.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"espn_api": ModuleType("espn_api"), "espn_api.football": football}):
        spec.loader.exec_module(module)
    return module


class ScoreModelTests(unittest.TestCase):
    def test_central_interval_uses_probability_quantiles_without_mutating_draws(self):
        samples = list(reversed(range(101)))
        original = samples.copy()
        lower, upper = central_score_interval(samples)
        self.assertAlmostEqual(lower, 2.5)
        self.assertAlmostEqual(upper, 97.5)
        self.assertEqual(samples, original)
        # A low-height tail can still contain enough probability to enter the
        # central interval; histogram peak height is not a quantile criterion.
        skewed = [0] * 9600 + [10] * 400
        self.assertEqual(central_score_interval(skewed), [0, 10])
        self.assertIsNone(central_score_interval([]))
        self.assertEqual(central_score_interval([12]), [12, 12])
        with self.assertRaises(ValueError):
            central_score_interval(samples, coverage=1)

    def test_configured_lineup_slots_use_familiar_order_and_keep_custom_slots(self):
        source = Path(__file__).parents[1] / "expected_wins.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_matchup_lineup_slot_order")
        scope = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
        settings = SimpleNamespace(position_slot_counts={"D/ST": 1, "K": 1, "WR": 2, "RB/WR/TE": 1, "QB": 1, "RB": 2, "TE": 1, "BE": 6, "IR": 2, "CUSTOM": 1})
        self.assertEqual(scope["_matchup_lineup_slot_order"](settings), ["QB", "RB", "WR", "TE", "FLEX", "K", "D/ST", "CUSTOM"])

    def test_partial_box_rows_preserve_card_projection_and_normalize_week_keys(self):
        history = merge_scoring_history(
            {1: {"points": 10, "projected_points": 12}},
            {"1": {"points": 11}, "2": {"points": 5}},
        )
        self.assertEqual(history[1], {"points": 11, "projected_points": 12})
        player = SimpleNamespace(projected_points=12, stats=history)
        self.assertEqual(player_score_model(player, 3)["history_games"], 1)

    def test_positive_prior_matches_requested_moments_without_negative_tail(self):
        model = player_score_model(SimpleNamespace(projected_points=12), 5, sd=6)
        draws = sample_team_scores([model], random.Random(47), 40000)
        mean = sum(draws) / len(draws)
        sd = math.sqrt(sum((value - mean) ** 2 for value in draws) / len(draws))
        self.assertGreater(min(draws), 0)
        self.assertAlmostEqual(mean, 12, delta=.15)
        self.assertAlmostEqual(sd, 6, delta=.2)

    def test_negative_draws_only_come_from_past_observations(self):
        player = SimpleNamespace(projected_points=10, stats={
            1: {"points": -2, "projected_points": 10},
            2: {"points": 0, "projected_points": 10},
            3: {"points": 10, "projected_points": 10},
            4: {"points": 20, "projected_points": 10},
            5: {"points": -100, "projected_points": 10},
            9: {"points": -1000, "projected_points": 10},
        })
        model = player_score_model(player, 5)
        draws = sample_team_scores([model], random.Random(42), 30000)
        self.assertEqual(model["history_games"], 4)
        self.assertEqual({value for value in draws if value < 0}, {-2})
        self.assertIn(0, draws)
        self.assertAlmostEqual(sum(draws) / len(draws), 10, delta=.2)
        self.assertAlmostEqual(sum(value <= 0 for value in draws) / len(draws), 1 / 3, delta=.015)

    def test_density_is_normalized_even_for_constant_scores(self):
        for values in ([12] * 40, [-2, 0, 1, 2, 9] * 40):
            density = score_density(values)
            width = density[1]["x"] - density[0]["x"]
            self.assertAlmostEqual(sum(point["y"] for point in density) * width, 1, delta=.01)

    def test_progress_uses_kickoff_when_wrapper_only_reports_zero(self):
        kickoff = datetime(2026, 11, 1, 18, tzinfo=timezone.utc)
        player = SimpleNamespace(game_played=0, game_date=kickoff)
        self.assertAlmostEqual(player_progress(player, kickoff.timestamp() + 5400), 50)
        self.assertEqual(player_progress(player, kickoff.timestamp() - 600), 0)


class LiveModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.logger = load_logger()

    def player(self, **overrides):
        values = dict(projected_points=10, points=0, game_played=0, slot_position="WR", stats={})
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_finished_and_bench_players_have_no_future_uncertainty(self):
        players = [self.player(game_played=100), self.player(slot_position="BE"), self.player(slot_position="IR")]
        self.assertEqual(self.logger._remaining_projection(players, week=5), (0, 0))
        self.assertEqual(self.logger._probability(100, 99, players, [], week=5), 100)
        self.assertEqual(self.logger._probability(100, 100, players, [], week=5), 50)
        self.assertEqual(self.logger._probability(99, 100, players, [], week=5), 0)

    def test_starter_cannot_lose_points_from_a_gaussian_tail(self):
        player = self.player(points=20, game_played=50)
        models = self.logger._remaining_models([player], 5)
        self.assertEqual(models[0]["mean"], 10)
        rng = random.Random(51)
        self.assertGreater(min(sample_player_score(models[0], rng) for _ in range(10000)), 0)
        # Home is ahead and only has an unfinished positive-scoring player.
        self.assertEqual(self.logger._probability(101, 100, [player], [], week=5), 100)

    def test_custom_game_window_and_active_status_select_ten_minutes(self):
        saturday = datetime(2026, 11, 7, 15, 4, tzinfo=timezone.utc).timestamp()
        first = self.logger._sampling_bucket(saturday, [[saturday - 3600, saturday + 7200]])
        next_slot = self.logger._sampling_bucket(saturday + 600, [[saturday - 3600, saturday + 7200]])
        self.assertEqual(first[1], 600)
        self.assertEqual(next_slot[0] - first[0], 600)
        self.assertEqual(self.logger._sampling_bucket(saturday, active=True)[1], 600)
        self.assertEqual(self.logger._sampling_bucket(saturday)[1], 21600)


if __name__ == "__main__":
    unittest.main()
