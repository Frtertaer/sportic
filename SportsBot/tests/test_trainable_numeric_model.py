from __future__ import annotations

import unittest

import numpy as np

from trainable_numeric_model import (
    TrainableNumericModel,
    fit_trainable_numeric_model,
    predict_trainable_numeric_model,
)


def _synthetic_row(idx: int, *, actual_result: str, xg_diff: float, form_diff: float, market_w1: float, market_draw: float, market_w2: float, total_goals: int, both_scored: bool) -> dict:
    if actual_result == "DRAW":
        shared = max(0, total_goals // 2)
        home_goals = shared
        away_goals = shared
    elif actual_result == "W1":
        if both_scored:
            away_goals = max(1, total_goals // 3)
            home_goals = max(away_goals + 1, total_goals - away_goals)
        else:
            home_goals = total_goals
            away_goals = 0
    else:
        if both_scored:
            home_goals = max(1, total_goals // 3)
            away_goals = max(home_goals + 1, total_goals - home_goals)
        else:
            home_goals = 0
            away_goals = total_goals

    market_over = 58.0 if total_goals >= 3 else 44.0
    market_btts = 57.0 if both_scored else 41.0
    return {
        "team1": f"Home {idx}",
        "team2": f"Away {idx}",
        "competition_name": "Synthetic League",
        "data_completeness": 0.85,
        "successful_source_count": 5,
        "total_source_count": 6,
        "is_team1_home": True,
        "market_prob_w1": market_w1,
        "market_prob_draw": market_draw,
        "market_prob_w2": market_w2,
        "market_prob_over_2_5": market_over,
        "market_prob_under_2_5": 100.0 - market_over,
        "market_prob_btts_yes": market_btts,
        "market_prob_btts_no": 100.0 - market_btts,
        "form_points_last5_diff": form_diff,
        "form_goal_diff_last5_diff": xg_diff,
        "form_scored_avg_diff": xg_diff / 4.0,
        "form_conceded_avg_diff": -xg_diff / 5.0,
        "h2h_team1_win_rate": 50.0 + (xg_diff * 3.0),
        "h2h_draw_rate": 27.0 if actual_result != "DRAW" else 35.0,
        "h2h_avg_goals": 2.4 if total_goals <= 2 else 3.0,
        "xg_diff": xg_diff,
        "xga_edge": xg_diff / 2.0,
        "rating_diff": xg_diff / 4.0,
        "possession_diff": xg_diff * 2.5,
        "standings_point_gap": form_diff * 1.5,
        "standings_position_gap": form_diff / 2.0,
        "rest_gap_hours": 24.0 + form_diff,
        "team1_short_rest": False,
        "team2_short_rest": False,
        "market_value_ratio": 1.2 if actual_result == "W1" else (0.9 if actual_result == "DRAW" else 0.8),
        "key_absence_diff": -1 if actual_result == "W1" else (0 if actual_result == "DRAW" else 1),
        "shots_avg_diff": xg_diff * 0.8,
        "shots_on_target_avg_diff": xg_diff * 0.35,
        "corners_avg_diff": xg_diff * 0.25,
        "shots_balance_diff": xg_diff * 0.5,
        "shots_on_target_balance_diff": xg_diff * 0.2,
        "corners_balance_diff": xg_diff * 0.15,
        "actual_result": actual_result,
        "actual_goals_home": home_goals,
        "actual_goals_away": away_goals,
    }


class TrainableNumericModelTests(unittest.TestCase):
    def test_fit_and_predict_return_probability_set(self):
        rows = []
        for idx in range(18):
            rows.append(_synthetic_row(idx, actual_result="W1", xg_diff=6.0, form_diff=8.0, market_w1=60.0, market_draw=23.0, market_w2=17.0, total_goals=3, both_scored=True))
        for idx in range(18, 36):
            rows.append(_synthetic_row(idx, actual_result="DRAW", xg_diff=0.0, form_diff=0.0, market_w1=36.0, market_draw=34.0, market_w2=30.0, total_goals=2, both_scored=False))
        for idx in range(36, 54):
            rows.append(_synthetic_row(idx, actual_result="W2", xg_diff=-6.0, form_diff=-8.0, market_w1=18.0, market_draw=24.0, market_w2=58.0, total_goals=3, both_scored=True))

        model = fit_trainable_numeric_model(rows)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertGreaterEqual(model.trained_rows, 54)
        self.assertGreaterEqual(model.btts_trained_rows, 54)

        prediction = predict_trainable_numeric_model(
            model,
            _synthetic_row(999, actual_result="W1", xg_diff=7.0, form_diff=10.0, market_w1=62.0, market_draw=22.0, market_w2=16.0, total_goals=4, both_scored=True),
        )

        self.assertIsNotNone(prediction)
        assert prediction is not None
        self.assertAlmostEqual(
            prediction["prob_w1"] + prediction["prob_draw"] + prediction["prob_w2"],
            100.0,
            places=1,
        )
        self.assertGreater(prediction["prob_w1"], prediction["prob_w2"])
        self.assertGreater(prediction["prob_over_2_5"], 50.0)
        self.assertGreater(prediction["prob_btts_yes"], 50.0)

    def test_fit_returns_none_when_not_enough_rows(self):
        rows = [
            _synthetic_row(idx, actual_result="W1", xg_diff=5.0, form_diff=6.0, market_w1=58.0, market_draw=24.0, market_w2=18.0, total_goals=3, both_scored=True)
            for idx in range(10)
        ]
        self.assertIsNone(fit_trainable_numeric_model(rows))

    def test_btts_training_uses_only_rows_with_explicit_btts_market(self):
        rows = []
        for idx in range(50):
            row = _synthetic_row(
                idx,
                actual_result="W1" if idx % 2 == 0 else "W2",
                xg_diff=4.0 if idx % 2 == 0 else -4.0,
                form_diff=5.0 if idx % 2 == 0 else -5.0,
                market_w1=54.0 if idx % 2 == 0 else 22.0,
                market_draw=24.0,
                market_w2=22.0 if idx % 2 == 0 else 54.0,
                total_goals=3,
                both_scored=bool(idx % 3),
            )
            if idx >= 45:
                row["market_prob_btts_yes"] = None
                row["market_prob_btts_no"] = None
            rows.append(row)

        model = fit_trainable_numeric_model(rows)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model.trained_rows, 50)
        self.assertEqual(model.btts_market_rows, 45)
        self.assertEqual(model.btts_trained_rows, 50)
        self.assertGreaterEqual(model.btts_blend_alpha, 0.03)

    def test_btts_proxy_supports_prediction_without_explicit_btts_market(self):
        rows = []
        for idx in range(60):
            rows.append(
                _synthetic_row(
                    idx,
                    actual_result="W1" if idx % 3 == 0 else ("DRAW" if idx % 3 == 1 else "W2"),
                    xg_diff=5.0 if idx % 3 == 0 else (-5.0 if idx % 3 == 2 else 0.0),
                    form_diff=6.0 if idx % 3 == 0 else (-6.0 if idx % 3 == 2 else 0.0),
                    market_w1=56.0 if idx % 3 == 0 else (22.0 if idx % 3 == 2 else 34.0),
                    market_draw=24.0 if idx % 3 != 1 else 34.0,
                    market_w2=20.0 if idx % 3 == 0 else (56.0 if idx % 3 == 2 else 32.0),
                    total_goals=3 if idx % 2 == 0 else 2,
                    both_scored=bool(idx % 4),
                )
            )
        model = fit_trainable_numeric_model(rows)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertGreaterEqual(model.btts_market_rows, 60)
        probe = _synthetic_row(
            999,
            actual_result="DRAW",
            xg_diff=1.0,
            form_diff=2.0,
            market_w1=39.0,
            market_draw=30.0,
            market_w2=31.0,
            total_goals=2,
            both_scored=True,
        )
        probe["market_prob_btts_yes"] = None
        probe["market_prob_btts_no"] = None
        prediction = predict_trainable_numeric_model(model, probe)
        self.assertIsNotNone(prediction)
        assert prediction is not None
        self.assertGreater(prediction["prob_btts_yes"], 1.0)
        self.assertLess(prediction["prob_btts_yes"], 99.0)

    def test_prediction_prefers_recalibrated_btts_base_when_available(self):
        model = TrainableNumericModel(
            feature_names=(),
            means=np.zeros(32, dtype=np.float64),
            scales=np.ones(32, dtype=np.float64),
            one_x_two_weights=None,
            one_x_two_base_rates=np.array([0.33, 0.34, 0.33], dtype=np.float64),
            over_weights=None,
            over_base_rate=0.5,
            btts_weights=None,
            btts_base_rate=0.5,
            btts_feature_indices=(),
            btts_proxy_feature_indices=(),
            btts_prior_proxy_weights=None,
            trained_rows=100,
            btts_trained_rows=100,
            btts_market_rows=100,
            blend_alpha=0.2,
            btts_blend_alpha=0.0,
        )

        row = _synthetic_row(
            999,
            actual_result="DRAW",
            xg_diff=0.5,
            form_diff=1.0,
            market_w1=36.0,
            market_draw=31.0,
            market_w2=33.0,
            total_goals=2,
            both_scored=True,
        )
        row["market_prob_btts_yes"] = None
        row["market_prob_btts_no"] = None
        row["model_prob_btts_yes"] = 63.0
        row["model_prob_btts_no"] = 37.0

        prediction = predict_trainable_numeric_model(model, row)
        self.assertIsNotNone(prediction)
        assert prediction is not None
        self.assertEqual(prediction["prob_btts_yes"], 63.0)
        self.assertEqual(prediction["prob_btts_no"], 37.0)

    def test_prediction_prefers_model_btts_base_over_market_when_row_has_both(self):
        model = TrainableNumericModel(
            feature_names=(),
            means=np.zeros(32, dtype=np.float64),
            scales=np.ones(32, dtype=np.float64),
            one_x_two_weights=None,
            one_x_two_base_rates=np.array([0.33, 0.34, 0.33], dtype=np.float64),
            over_weights=None,
            over_base_rate=0.5,
            btts_weights=None,
            btts_base_rate=0.5,
            btts_feature_indices=(),
            btts_proxy_feature_indices=(),
            btts_prior_proxy_weights=None,
            trained_rows=100,
            btts_trained_rows=100,
            btts_market_rows=100,
            blend_alpha=0.2,
            btts_blend_alpha=0.0,
        )

        row = _synthetic_row(
            1000,
            actual_result="DRAW",
            xg_diff=0.5,
            form_diff=1.0,
            market_w1=36.0,
            market_draw=31.0,
            market_w2=33.0,
            total_goals=2,
            both_scored=True,
        )
        row["market_prob_btts_yes"] = 57.0
        row["market_prob_btts_no"] = 43.0
        row["model_prob_btts_yes"] = 63.0
        row["model_prob_btts_no"] = 37.0

        prediction = predict_trainable_numeric_model(model, row)
        self.assertIsNotNone(prediction)
        assert prediction is not None
        self.assertEqual(prediction["prob_btts_yes"], 63.0)
        self.assertEqual(prediction["prob_btts_no"], 37.0)
