from __future__ import annotations

import unittest
from unittest.mock import patch

from baseline_model import apply_stake_recommendation
from clv_desk import (
    MIN_OWN_SAMPLE,
    apply_1x2_stake_gate,
    archive_favorite_clv,
    clv_percent,
    own_1x2_clv,
    stake_allowed,
)
from models import PredictionFeatureSnapshot


class ClvDeskTests(unittest.TestCase):
    def test_clv_percent_matches_shorter_close(self) -> None:
        self.assertEqual(clv_percent(2.10, 1.95), 7.69)

    def test_own_row_uses_selected_side_not_home(self) -> None:
        value = own_1x2_clv(
            {
                "top_market": "1X2",
                "top_selection": "TEAM2",
                "placement_odds": 2.20,
                "close_w1": 1.40,
                "close_w2": 2.00,
            }
        )
        self.assertEqual(value, clv_percent(2.20, 2.00))
        self.assertNotEqual(value, clv_percent(2.20, 1.40))

    def test_stake_stays_closed_until_sample_is_large_and_positive(self) -> None:
        self.assertFalse(stake_allowed([]))
        self.assertFalse(stake_allowed([1.0] * (MIN_OWN_SAMPLE - 1)))
        self.assertFalse(stake_allowed([-1.0] * MIN_OWN_SAMPLE))
        self.assertTrue(stake_allowed([0.5] * MIN_OWN_SAMPLE))

    def test_gate_zeros_only_positive_1x2_stake(self) -> None:
        report = {"top_market_code": "1X2", "stake_units": 1.0, "stake_label": "Standard", "data_warnings": []}
        self.assertTrue(apply_1x2_stake_gate(report, []))
        self.assertEqual(report["stake_units"], 0.0)
        self.assertEqual(report["stake_label"], "No bet")

        totals = {"top_market_code": "TOTALS_2_5", "stake_units": 0.75, "data_warnings": []}
        self.assertFalse(apply_1x2_stake_gate(totals, []))
        self.assertEqual(totals["stake_units"], 0.75)

    def test_archive_split_uses_open_favorite_against_its_close(self) -> None:
        plus = archive_favorite_clv(
            {"odds_w1": 2.40, "odds_w2": 1.70},
            {"odds_w1": 2.60, "odds_w2": 1.50},
            "W2",
        )
        minus = archive_favorite_clv(
            {"odds_w1": 1.70, "odds_w2": 2.40},
            {"odds_w1": 1.90, "odds_w2": 2.10},
            "W1",
        )
        self.assertGreater(plus["clv"], 0.0)
        self.assertLess(minus["clv"], 0.0)
        self.assertEqual(plus["side"], "W2")
        draw = archive_favorite_clv(
            {"odds_w1": 1.70, "odds_w2": 2.40},
            {"odds_w1": 1.50, "odds_w2": 2.60},
            "DRAW",
        )
        self.assertFalse(draw["won"])
        self.assertEqual(draw["profit"], -1.0)

    def test_live_stake_path_withholds_1x2_when_ledger_is_empty(self) -> None:
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.82,
            top_edge_pct=6.4,
        )
        report = {
            "top_recommendation": "Arsenal Win",
            "top_market_code": "1X2",
            "top_confidence_pct": 72.0,
            "risk_level": "LOW",
            "data_warnings": [],
        }
        with patch("baseline_model.load_own_1x2_clv", return_value=[]):
            with patch("config.RECOMMENDATION_MODE", "balanced"):
                apply_stake_recommendation(report, snapshot)
        self.assertEqual(report["stake_units"], 0.0)
        self.assertIn("0u", report["stake_rationale"])
