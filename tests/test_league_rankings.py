"""Test record windows and power scores independently of ESPN/API collection."""
import ast
from pathlib import Path
import unittest

import pandas as pd


SOURCE = Path(__file__).resolve().parents[1] / "expected_wins.py"


def payload_body():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "_build_payload").body


class LeagueRankingTests(unittest.TestCase):
    def test_last_three_counts_ties_and_short_or_empty_histories(self):
        body = payload_body()
        start = next(i for i, node in enumerate(body) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "standings"
                             for target in node.targets))
        namespace = {
            "weekly_results": {
                "Alpha": [{"outcome": value} for value in ["W", "W", "L", "T", "W"]],
                "Beta": [{"outcome": value} for value in ["T", "L"]],
                "Gamma": [],
            },
            "team_records": {name: {"team": name, "wins": 0, "losses": 0}
                             for name in ["Alpha", "Beta", "Gamma"]},
            "official_rank": {"Alpha": 1, "Beta": 2, "Gamma": 3},
            "leader": {"wins": 0, "losses": 0},
            "labels": ["Alpha", "Beta", "Gamma"],
        }
        exec(compile(ast.Module(body=body[start:start + 2], type_ignores=[]),
                     str(SOURCE), "exec"), namespace)
        results = {row["team"]: row for row in namespace["standings"]}
        self.assertEqual([results["Alpha"][key] for key in
                          ["last3_wins", "last3_losses", "last3_ties", "last3_pct"]],
                         [1, 1, 1, 0.5])
        self.assertEqual([results["Beta"][key] for key in
                          ["last3_wins", "last3_losses", "last3_ties", "last3_pct"]],
                         [0, 1, 1, 0.25])
        self.assertEqual([results["Gamma"][key] for key in
                          ["last3_wins", "last3_losses", "last3_ties", "last3_pct"]],
                         [0, 0, 0, 0])

    def power_function(self):
        functions = [node for node in payload_body() if isinstance(node, ast.FunctionDef)
                     and node.name in {"normalized", "power_rankings_for"}]
        rows = []
        histories = {
            "Alpha": [(100, 1), (120, 0), (140, 0.5), (160, 1)],
            "Beta": [(180, 0), (180, 0.5)],
            "Gamma": [(80, 1), (100, 1), (120, 1), (100, 0)],
        }
        for team, history in histories.items():
            rows.extend({"team": team, "week": week, "score": score, "actual": actual}
                        for week, (score, actual) in enumerate(history, 1))
        namespace = {
            "pd": pd, "completed": pd.DataFrame(rows), "labels": list(histories),
            "quality_by_week": {
                "Alpha": {1: 0.5, 4: 0.5, 5: 99},
                "Beta": {1: -1}, "Gamma": {1: 3},
            },
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"),
             namespace)
        return namespace["power_rankings_for"]

    def test_exact_weights_use_per_game_scoring_with_unequal_game_counts(self):
        results = self.power_function()(4)
        self.assertEqual([row["team"] for row in results], ["Gamma", "Alpha", "Beta"])
        by_team = {row["team"]: row for row in results}
        # Independently calculated 30/30/10/10/20 scores. Beta has fewer total
        # points than Alpha but a higher PPG and therefore the full PPG components.
        self.assertEqual(by_team["Alpha"]["score"], 52.05)
        self.assertEqual(by_team["Beta"]["score"], 35.0)
        self.assertEqual(by_team["Gamma"]["score"], 62.5)
        self.assertEqual(by_team["Beta"]["overall_points_score"], 1.0)
        self.assertEqual(by_team["Beta"]["recent_points_score"], 1.0)
        self.assertEqual(by_team["Alpha"]["recent_wins"], 1)
        self.assertEqual(by_team["Alpha"]["recent_losses"], 1)
        self.assertEqual(by_team["Alpha"]["recent_ties"], 1)
        self.assertEqual(by_team["Beta"]["recent_games"], 2)
        self.assertEqual(by_team["Alpha"]["elo_raw"], 1.0)  # Excludes W5.

    def test_historical_rankings_ignore_future_games(self):
        results = {row["team"]: row for row in self.power_function()(2)}
        self.assertEqual(results["Alpha"]["overall_points"], 220)
        self.assertEqual(results["Alpha"]["overall_ppg"], 110)
        self.assertEqual(results["Alpha"]["recent_games"], 2)
        self.assertEqual(results["Alpha"]["recent_ties"], 0)
        self.assertEqual(results["Alpha"]["elo_raw"], 0.5)


if __name__ == "__main__":
    unittest.main()
