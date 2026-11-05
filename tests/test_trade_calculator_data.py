"""Projection provenance and schedule rules are independent of ESPN/network."""
from types import SimpleNamespace
import unittest

from trade_calculator import (build_trade_calculator, bye_week, lineup_slots,
                              player_forecasts, raw_weekly_projections)


def player(**changes):
    values = dict(playerId=123, name="Runner", position="RB", lineupSlot="RB",
                  eligibleSlots=[2, 3, 23, 7, 20, 21], injuryStatus="ACTIVE",
                  projected_avg_points=12.5, stats={}, schedule={week: {"team": "SEA"}
                  for week in range(1, 19) if week != 11})
    values.update(changes)
    return SimpleNamespace(**values)


def roster():
    return {"id": 123, "name": "Runner", "position": "RB", "slot": "RB",
            "ppg": 10, "score_games": 4}


class TradeCalculatorDataTests(unittest.TestCase):
    def test_exact_week_season_and_projection_source_required(self):
        base = {"seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 1,
                "scoringPeriodId": 10, "appliedTotal": 13.2}
        rows = [dict(base, seasonId=2025, appliedTotal=90),
                dict(base, statSourceId=0, appliedTotal=80),
                dict(base, statSplitTypeId=0, scoringPeriodId=0, appliedTotal=300),
                dict(base, statSplitTypeId=2, appliedTotal=40),
                dict(base, scoringPeriodId=9, appliedTotal=50),
                base]
        payload = {"teams": [{"roster": {"entries": [{"playerPoolEntry": {
            "player": {"id": 123, "stats": rows}}}]}}]}
        self.assertEqual(raw_weekly_projections(payload, 2026, 10), {(123, 10): 13.2})
        self.assertEqual(raw_weekly_projections(payload, 2026, 11), {})

    def test_byes_and_negative_or_zero_projections_are_preserved(self):
        forecasts = player_forecasts(player(), roster(), [9, 10, 11, 12], 9,
                                     {(123, 9): 0, (123, 10): -1.5, (123, 11): 22})
        weekly = forecasts["projections"]
        self.assertEqual(weekly["9"], {"points": 0.0, "source": "espn_weekly", "available": True})
        self.assertEqual(weekly["10"]["points"], -1.5)
        self.assertEqual(weekly["11"]["points"], 0)
        self.assertFalse(weekly["11"]["available"])
        self.assertEqual(weekly["12"]["source"], "espn_baseline")
        self.assertEqual(forecasts["eligible_slots"], ["RB", "RB/WR", "FLEX", "OP"])

    def test_incomplete_schedule_does_not_manufacture_bye(self):
        self.assertIsNone(bye_week(player(schedule={9: {"team": "DAL"}})))
        self.assertEqual(bye_week(player()), 11)
        self.assertEqual(bye_week(player(schedule={}, bye_week=7)), 7)

    def test_current_injury_does_not_erase_conditional_future_projection(self):
        forecasts = player_forecasts(player(injuryStatus="OUT"), roster(), [9, 10], 9,
                                     {(123, 9): 20, (123, 10): 18})["projections"]
        self.assertFalse(forecasts["9"]["available"])
        self.assertEqual(forecasts["9"]["source"], "unavailable")
        self.assertEqual(forecasts["10"]["points"], 18)
        self.assertTrue(forecasts["10"]["available"])
        self.assertIn("Conditional", forecasts["10"]["reason"])

    def test_missing_is_unknown_not_an_invented_projection(self):
        forecasts = player_forecasts(player(projected_avg_points=0), {"score_games": 0},
                                     [9], 9, {})["projections"]["9"]
        self.assertIsNone(forecasts["points"])
        self.assertFalse(forecasts["available"])
        self.assertEqual(forecasts["source"], "missing")

    def test_baseline_provenance_and_no_season_total_minus_actual(self):
        p = player(projected_avg_points=0, projected_total_points=5, total_points=50,
                   stats={"8": {"projected_points": 12}, 7: {"projected_points": 18},
                          "bad": {"projected_points": 999}, 0: {"projected_points": 200}})
        weekly = player_forecasts(p, roster(), [10], 9, {})["projections"]["10"]
        self.assertEqual((weekly["points"], weekly["source"]), (15, "recent_projection"))
        p.stats = {}
        weekly = player_forecasts(p, roster(), [10], 9, {})["projections"]["10"]
        self.assertEqual((weekly["points"], weekly["source"]), (10, "historical_ppg"))

    def test_actual_lineup_count_and_espn_slot_ids(self):
        self.assertEqual(lineup_slots({"0": 1, "2": 2, "4": 2, "6": 1, "23": 1,
                                      "17": 1, "16": 1, "20": 7, "21": 1}),
                         ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "D/ST"])

    def test_league_horizon_and_fallback_are_visible_after_failed_request(self):
        calls = []
        def fetch(params):
            calls.append(params["scoringPeriodId"])
            if params["scoringPeriodId"] == 17:
                raise RuntimeError("test failure")
            return {"teams": []}
        league = SimpleNamespace(current_week=16, year=2026,
            settings=SimpleNamespace(position_slot_counts={"QB": 1}),
            teams=[SimpleNamespace(team_id=1, team_name="Alpha", roster=[player()])],
            espn_request=SimpleNamespace(league_get=fetch))
        result = build_trade_calculator(league, {"1": [roster()]},
                                       {"matchup_periods": {"14": [14, 15], "15": [16, 17]},
                                        "lineup_slot_counts": {"2": 1}})
        self.assertEqual(result["weeks"], [16, 17])
        self.assertEqual(result["lineup_slots"], ["RB"])
        self.assertEqual(calls, [16, 17])
        self.assertEqual(result["failed_weeks"], [17])
        self.assertEqual(result["projection_coverage"], {"espn_baseline": 2})
        self.assertTrue(any("failed" in note for note in result["notes"]))


if __name__ == "__main__":
    unittest.main()
