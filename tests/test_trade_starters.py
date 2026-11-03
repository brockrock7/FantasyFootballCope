"""Exercise trade ownership accounting without ESPN credentials or network calls."""
import ast
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest


def trade_functions():
    source = Path(__file__).resolve().parents[1] / "expected_wins.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_trade_data", "_safe_float", "_trade_rating"}]
    namespace = {"defaultdict": defaultdict, "datetime": datetime, "timezone": timezone}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class TradeStarterAccountingTests(unittest.TestCase):
    def setUp(self):
        self.namespace = trade_functions()
        transaction = SimpleNamespace(
            type="TRADE_ACCEPT", status="EXECUTED", id="one", scoring_period=1, date=0,
            items=[SimpleNamespace(type="TRADE", playerId=1, from_team_id=1, to_team_id=2),
                   SimpleNamespace(type="TRADE", playerId=-2, from_team_id=2, to_team_id=1)],
        )
        self.namespace["_raw_trade_transactions"] = lambda league, week: (
            [transaction] if week == 1 else [], 0, [], week == 1
        )
        self.namespace["_activity_trade_rows"] = lambda league, dates: ([], 0)
        self.league = SimpleNamespace(
            current_week=5, player_map={},
            player_info=lambda **kwargs: [
                SimpleNamespace(playerId=1, name="Runner", stats={
                    2: {"points": 10}, 3: {"points": 20}, 4: {"points": 30}}),
                SimpleNamespace(playerId=-2, name="Defense", stats={
                    2: {"points": -4}, 3: {"points": 6}, 4: {"points": 3}}),
            ],
        )

    def test_only_actual_recipient_starts_count_and_negative_points_remain(self):
        history = {
            ("2", 1): [{"week": 2, "points": 10, "started": True},
                        {"week": 3, "points": 20, "started": False}],
            # A later owner started Runner in W4; the trade recipient did not.
            ("3", 1): [{"week": 4, "points": 30, "started": True}],
            ("1", -2): [{"week": 2, "points": -4, "started": True},
                         {"week": 3, "points": 6, "started": True},
                         {"week": 4, "points": 3, "started": False}],
        }
        trades, warning, _ = self.namespace["_trade_data"](
            self.league, {"1": "Alpha", "2": "Beta", "3": "Gamma"}, 4, history
        )
        self.assertEqual(warning, "")
        alpha = next(side for side in trades[0]["sides"] if side["team"] == "Alpha")
        beta = next(side for side in trades[0]["sides"] if side["team"] == "Beta")
        self.assertEqual(alpha["net_points"], -55)
        self.assertEqual([week["started_net"] for week in alpha["weekly_points"]], [-14, 6, 0])
        self.assertEqual([week["started_net"] for week in beta["weekly_points"]], [14, -6, 0])
        self.assertEqual(alpha["incoming"][0]["weekly_started_points"]["2"], -4)
        self.assertFalse(alpha["outgoing"][0]["weekly_started"]["4"])
        self.assertEqual(alpha["outgoing"][0]["recipient_team"], "Beta")

    def test_missing_ownership_history_never_invents_starts(self):
        trades, _, _ = self.namespace["_trade_data"](
            self.league, {"1": "Alpha", "2": "Beta"}, 4
        )
        for side in trades[0]["sides"]:
            self.assertTrue(all(row["started_net"] == 0 for row in side["weekly_points"]))
            self.assertNotEqual(side["net_points"], 0)


if __name__ == "__main__":
    unittest.main()
