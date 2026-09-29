from __future__ import annotations

import unittest

from scripts.backtest_baseline import (
    _calibration_buckets_1x2_top_pick,
    _calibration_buckets_binary,
    _candidate_report_from_row,
    _extract_prediction_clv_entry,
    _extract_value_candidate_clv_entries,
    _fit_best_alpha_1x2,
    _fit_best_alpha_binary,
    _grade_market_selection,
    _hybrid_alpha_cap,
    _hybrid_getter,
    _market_family_trust_band,
    _numeric_getter,
    _policy_flag_category,
    _prepare_market_family_trust_rows,
    _split_competition_policy_flag,
    _segment_prediction_clv,
    _segment_prediction_clv_by_competition_policy_flag,
    _segment_prediction_clv_by_policy_flag,
    _segment_sample_gate,
    _summarize_action_core_preference_impact,
    _summarize_family_gating_impact,
    _summarize_market_coverage,
    _summarize_live_market_drift,
    _summarize_prediction_clv,
    _summarize_top_pick_core_preference_impact,
    _time_split_rows,
)


class BacktestScriptTests(unittest.TestCase):
    def test_calibration_buckets_binary_groups_probabilities(self):
        rows = [
            {"prob": 52.0, "actual": 1.0},
            {"prob": 54.0, "actual": 0.0},
            {"prob": 67.0, "actual": 1.0},
            {"prob": 68.0, "actual": 1.0},
            {"prob": 33.0, "actual": 1.0},
        ]

        buckets = _calibration_buckets_binary(
            rows,
            lambda row: row.get("prob"),
            lambda row: row.get("actual"),
        )

        self.assertEqual([bucket["bucket"] for bucket in buckets], ["50-55", "65-70"])
        self.assertEqual(buckets[0]["count"], 2)
        self.assertAlmostEqual(buckets[0]["avg_prob"], 53.0)
        self.assertAlmostEqual(buckets[0]["hit_rate"], 50.0)
        self.assertAlmostEqual(buckets[1]["hit_rate"], 100.0)

    def test_calibration_buckets_1x2_top_pick_tracks_top_outcome_hit_rate(self):
        rows = [
            {"prob_w1": 58.0, "prob_draw": 24.0, "prob_w2": 18.0, "actual_result": "W1"},
            {"prob_w1": 56.0, "prob_draw": 27.0, "prob_w2": 17.0, "actual_result": "DRAW"},
            {"prob_w1": 46.0, "prob_draw": 28.0, "prob_w2": 26.0, "actual_result": "W1"},
        ]

        buckets = _calibration_buckets_1x2_top_pick(
            rows,
            lambda row: row.get("prob_w1"),
            lambda row: row.get("prob_draw"),
            lambda row: row.get("prob_w2"),
        )

        self.assertEqual([bucket["bucket"] for bucket in buckets], ["45-50", "55-60"])
        self.assertEqual(buckets[0]["count"], 1)
        self.assertAlmostEqual(buckets[0]["hit_rate"], 100.0)
        self.assertEqual(buckets[1]["count"], 2)
        self.assertAlmostEqual(buckets[1]["hit_rate"], 50.0)

    def test_numeric_getter_can_use_recalibration_profiles(self):
        row = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "competition_name": "Premier League",
            "data_completeness": 0.8,
            "successful_source_count": 4,
            "total_source_count": 5,
            "market_prob_w1": 52.0,
            "market_prob_draw": 25.0,
            "market_prob_w2": 23.0,
            "market_prob_over_2_5": 53.0,
            "market_prob_under_2_5": 47.0,
            "market_prob_btts_yes": 51.0,
            "market_prob_btts_no": 49.0,
            "form_points_last5_diff": 5.0,
            "xg_diff": 8.0,
            "xga_edge": 4.0,
            "standings_point_gap": 10.0,
            "rest_gap_hours": 36.0,
            "is_team1_home": True,
        }
        raw_top = _numeric_getter(row, "prob_w1")
        bucket_low = int((float(raw_top or 0.0) // 5) * 5)
        bucket_high = min(100, bucket_low + 5)
        row["_recalibration_profiles"] = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "market": "1X2_TOP_PICK",
                "bucket_label": f"{bucket_low}-{bucket_high}",
                "delta_pct": -6.0,
            }
        ]

        recalibrated_top = _numeric_getter(row, "prob_w1", recalibrated=True)

        self.assertIsNotNone(raw_top)
        self.assertIsNotNone(recalibrated_top)
        self.assertLess(float(recalibrated_top), float(raw_top))

    def test_hybrid_getter_blends_and_normalizes_1x2_probabilities(self):
        row = {
            "_numeric_baseline_recalibrated": {
                "prob_w1": 60.0,
                "prob_draw": 25.0,
                "prob_w2": 15.0,
            },
            "_trainable_numeric_prediction": {
                "prob_w1": 30.0,
                "prob_draw": 30.0,
                "prob_w2": 40.0,
            },
        }

        p1 = _hybrid_getter(row, "prob_w1", object(), 0.25)
        pd = _hybrid_getter(row, "prob_draw", object(), 0.25)
        p2 = _hybrid_getter(row, "prob_w2", object(), 0.25)

        self.assertAlmostEqual(float(p1 or 0.0), 52.5, places=3)
        self.assertAlmostEqual(float(pd or 0.0), 26.25, places=3)
        self.assertAlmostEqual(float(p2 or 0.0), 21.25, places=3)
        self.assertAlmostEqual(float(p1 or 0.0) + float(pd or 0.0) + float(p2 or 0.0), 100.0, places=3)

    def test_fit_best_alpha_prefers_recalibration_when_trainable_is_worse(self):
        rows = [
            {
                "actual_result": "W1",
                "_numeric_baseline_recalibrated": {"prob_w1": 70.0, "prob_draw": 20.0, "prob_w2": 10.0},
                "_trainable_numeric_prediction": {"prob_w1": 25.0, "prob_draw": 35.0, "prob_w2": 40.0},
            },
            {
                "actual_result": "W1",
                "_numeric_baseline_recalibrated": {"prob_w1": 68.0, "prob_draw": 21.0, "prob_w2": 11.0},
                "_trainable_numeric_prediction": {"prob_w1": 30.0, "prob_draw": 35.0, "prob_w2": 35.0},
            },
            {
                "actual_result": "DRAW",
                "_numeric_baseline_recalibrated": {"prob_w1": 24.0, "prob_draw": 54.0, "prob_w2": 22.0},
                "_trainable_numeric_prediction": {"prob_w1": 45.0, "prob_draw": 20.0, "prob_w2": 35.0},
            },
        ]

        alpha = _fit_best_alpha_1x2(rows, object())

        self.assertEqual(alpha, 0.0)

    def test_fit_best_alpha_binary_uses_trainable_when_it_improves_validate(self):
        base_rows = [
            {
                "actual_goals_home": 2,
                "actual_goals_away": 1,
                "_numeric_baseline_recalibrated": {"prob_over_2_5": 44.0},
                "_trainable_numeric_prediction": {"prob_over_2_5": 66.0},
            },
            {
                "actual_goals_home": 3,
                "actual_goals_away": 1,
                "_numeric_baseline_recalibrated": {"prob_over_2_5": 46.0},
                "_trainable_numeric_prediction": {"prob_over_2_5": 69.0},
            },
            {
                "actual_goals_home": 1,
                "actual_goals_away": 1,
                "_numeric_baseline_recalibrated": {"prob_over_2_5": 57.0},
                "_trainable_numeric_prediction": {"prob_over_2_5": 35.0},
            },
        ]
        rows = base_rows * 20

        alpha = _fit_best_alpha_binary(
            rows,
            "prob_over_2_5",
            lambda row: 1.0 if (row["actual_goals_home"] + row["actual_goals_away"]) > 2 else 0.0,
            object(),
            train_rows=180,
        )

        self.assertGreater(alpha, 0.0)

    def test_hybrid_alpha_cap_shrinks_btts_more_than_1x2_on_small_samples(self):
        one_x_two_cap = _hybrid_alpha_cap("1X2", train_rows=104, validate_rows=37)
        totals_cap = _hybrid_alpha_cap("prob_over_2_5", train_rows=104, validate_rows=37)
        btts_cap = _hybrid_alpha_cap("prob_btts_yes", train_rows=104, validate_rows=37)

        self.assertGreater(one_x_two_cap, totals_cap)
        self.assertGreater(totals_cap, btts_cap)
        self.assertLessEqual(btts_cap, 0.1)

    def test_extract_prediction_clv_entry_maps_team_win_and_beats_close(self):
        row = {
            "prediction_id": 1,
            "team1": "Arsenal",
            "team2": "Chelsea",
            "actual_result": "W1",
            "actual_goals_home": 2,
            "actual_goals_away": 1,
            "top_recommendation": "Arsenal Win",
            "top_market_code": "1X2",
            "top_selection_code": "TEAM1",
            "entry_odds": 1.82,
            "open_odds_w1": 1.91,
            "close_odds_w1": 1.74,
        }

        entry = _extract_prediction_clv_entry(row)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["family"], "1x2")
        self.assertEqual(entry["market_field"], "odds_w1")
        self.assertTrue(entry["beat_close"])
        self.assertEqual(entry["settlement"], "WIN")
        self.assertAlmostEqual(entry["profit_units"], 0.82, places=4)
        self.assertAlmostEqual(entry["entry_to_close_delta"], -0.08, places=4)
        self.assertAlmostEqual(entry["open_to_close_delta"], -0.17, places=4)

    def test_prediction_clv_summary_and_live_market_drift_aggregate_rows(self):
        entries = [
            {
                "family": "1x2",
                "beat_close": True,
                "flat_close": False,
                "entry_to_close_delta": -0.08,
                "open_to_close_delta": -0.17,
                "implied_prob_delta_pct": 2.53,
                "settlement": "WIN",
                "profit_units": 0.82,
            },
            {
                "family": "totals",
                "beat_close": False,
                "flat_close": False,
                "entry_to_close_delta": 0.12,
                "open_to_close_delta": 0.08,
                "implied_prob_delta_pct": -3.41,
                "settlement": "LOSS",
                "profit_units": -1.0,
            },
        ]
        rows = [
            {
                "open_odds_w1": 1.91,
                "close_odds_w1": 1.74,
                "open_odds_draw": 3.55,
                "close_odds_draw": 3.60,
                "open_odds_w2": 4.30,
                "close_odds_w2": 4.55,
                "open_odds_over_2_5": 2.01,
                "close_odds_over_2_5": 1.95,
                "open_odds_under_2_5": 1.82,
                "close_odds_under_2_5": 1.88,
                "open_odds_btts_yes": 1.93,
                "close_odds_btts_yes": 1.90,
                "open_odds_btts_no": 1.87,
                "close_odds_btts_no": 1.91,
            }
        ]

        summary = _summarize_prediction_clv(entries)
        drift = _summarize_live_market_drift(rows)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["beat_close"], 1)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["pushes"], 0)
        self.assertAlmostEqual(summary["hit_rate"], 50.0)
        self.assertAlmostEqual(summary["roi_pct"], -9.0)
        self.assertAlmostEqual(summary["avg_entry_to_close_delta"], 0.02, places=4)
        self.assertEqual(summary["family_counts"]["1x2"], 1)
        self.assertEqual(summary["family_counts"]["totals"], 1)
        self.assertAlmostEqual(drift["1X2"]["avg_abs_delta"], 0.1567, places=4)
        self.assertAlmostEqual(drift["O/U 2.5"]["avg_abs_delta"], 0.06, places=4)
        self.assertAlmostEqual(drift["BTTS"]["avg_abs_delta"], 0.035, places=4)

    def test_segment_prediction_clv_groups_by_family_and_competition(self):
        entries = [
            {
                "family": "1x2",
                "competition_name": "Premier League",
                "beat_close": True,
                "flat_close": False,
                "entry_to_close_delta": -0.08,
                "open_to_close_delta": -0.17,
                "implied_prob_delta_pct": 2.53,
                "settlement": "WIN",
                "profit_units": 0.82,
            },
            {
                "family": "1x2",
                "competition_name": "Premier League",
                "beat_close": False,
                "flat_close": False,
                "entry_to_close_delta": 0.12,
                "open_to_close_delta": 0.08,
                "implied_prob_delta_pct": -3.41,
                "settlement": "LOSS",
                "profit_units": -1.0,
            },
            {
                "family": "totals",
                "competition_name": "La Liga",
                "beat_close": True,
                "flat_close": False,
                "entry_to_close_delta": -0.05,
                "open_to_close_delta": -0.10,
                "implied_prob_delta_pct": 1.42,
                "settlement": "PUSH",
                "profit_units": 0.0,
            },
        ]

        family_segments = _segment_prediction_clv(entries, key_name="family")
        competition_segments = _segment_prediction_clv(entries, key_name="competition_name")

        self.assertEqual(family_segments[0]["segment"], "1x2")
        self.assertEqual(family_segments[0]["count"], 2)
        self.assertAlmostEqual(family_segments[0]["beat_close_rate"], 50.0)
        self.assertAlmostEqual(family_segments[0]["hit_rate"], 50.0)
        self.assertAlmostEqual(family_segments[0]["roi_pct"], -9.0)
        self.assertEqual(family_segments[0]["sample_gate"], "low-sample")
        self.assertEqual(competition_segments[0]["segment"], "Premier League")
        self.assertEqual(competition_segments[0]["count"], 2)

    def test_segment_prediction_clv_by_policy_flag_groups_entries(self):
        entries = [
            {
                "family": "btts",
                "competition_name": "Premier League",
                "policy_flags": ["path_agent_chain", "top_market_core"],
                "beat_close": True,
                "flat_close": False,
                "entry_to_close_delta": -0.08,
                "open_to_close_delta": -0.12,
                "implied_prob_delta_pct": 2.1,
                "settlement": "WIN",
                "profit_units": 0.9,
            },
            {
                "family": "btts",
                "competition_name": "Premier League",
                "policy_flags": ["path_agent_chain"],
                "beat_close": False,
                "flat_close": False,
                "entry_to_close_delta": 0.05,
                "open_to_close_delta": 0.02,
                "implied_prob_delta_pct": -1.3,
                "settlement": "LOSS",
                "profit_units": -1.0,
            },
        ]

        segments = _segment_prediction_clv_by_policy_flag(entries)

        self.assertEqual(segments[0]["segment"], "path_agent_chain")
        self.assertEqual(segments[0]["count"], 2)
        self.assertEqual(segments[1]["segment"], "top_market_core")
        self.assertEqual(segments[1]["count"], 1)

    def test_segment_prediction_clv_by_competition_policy_flag_groups_entries(self):
        entries = [
            {
                "family": "btts",
                "competition_name": "Premier League",
                "policy_flags": ["path_agent_chain", "top_market_core"],
                "beat_close": True,
                "flat_close": False,
                "entry_to_close_delta": -0.08,
                "open_to_close_delta": -0.12,
                "implied_prob_delta_pct": 2.1,
                "settlement": "WIN",
                "profit_units": 0.9,
            },
            {
                "family": "btts",
                "competition_name": "Premier League",
                "policy_flags": ["path_agent_chain"],
                "beat_close": False,
                "flat_close": False,
                "entry_to_close_delta": 0.05,
                "open_to_close_delta": 0.02,
                "implied_prob_delta_pct": -1.3,
                "settlement": "LOSS",
                "profit_units": -1.0,
            },
            {
                "family": "1x2",
                "competition_name": "La Liga",
                "policy_flags": ["path_single_call"],
                "beat_close": True,
                "flat_close": False,
                "entry_to_close_delta": -0.04,
                "open_to_close_delta": -0.07,
                "implied_prob_delta_pct": 1.4,
                "settlement": "WIN",
                "profit_units": 0.82,
            },
        ]

        segments = _segment_prediction_clv_by_competition_policy_flag(entries)

        self.assertEqual(segments[0]["segment"], "Premier League / path_agent_chain")
        self.assertEqual(segments[0]["count"], 2)
        self.assertEqual(segments[1]["segment"], "La Liga / path_single_call")
        self.assertEqual(segments[1]["count"], 1)
        self.assertEqual(segments[2]["segment"], "Premier League / top_market_core")
        self.assertEqual(segments[2]["count"], 1)

    def test_policy_flag_category_groups_flags_for_diagnostics(self):
        self.assertEqual(_policy_flag_category("path_agent_chain"), ("Execution path", 0))
        self.assertEqual(_policy_flag_category("top_market_core"), ("Market type", 1))
        self.assertEqual(_policy_flag_category("top_family_positive_trust"), ("Historical trust", 2))
        self.assertEqual(_policy_flag_category("action_mode_pick"), ("Selection policy", 3))

    def test_split_competition_policy_flag_keeps_competition_and_flag(self):
        self.assertEqual(
            _split_competition_policy_flag("Premier League / path_agent_chain"),
            ("Premier League", "path_agent_chain"),
        )
        self.assertEqual(
            _split_competition_policy_flag("top_market_core"),
            ("Unknown", "top_market_core"),
        )

    def test_extract_value_candidate_clv_entries_uses_structured_candidate_snapshot(self):
        row = {
            "prediction_id": 7,
            "actual_result": "W1",
            "actual_goals_home": 2,
            "actual_goals_away": 1,
            "value_candidate_snapshot": [
                {
                    "label": "Over 2.5",
                    "family": "totals",
                    "market_code": "TOTALS_2_5",
                    "selection_code": "OVER",
                    "probability": 58.0,
                    "market_probability": 52.0,
                    "edge": 6.0,
                    "odds": 2.02,
                },
                {
                    "label": "Arsenal Draw No Bet",
                    "family": "dnb",
                    "market_code": "DNB",
                    "selection_code": "TEAM1",
                    "probability": 69.0,
                    "market_probability": 62.0,
                    "edge": 7.0,
                    "odds": 1.61,
                },
            ],
            "open_odds_over_2_5": 2.08,
            "close_odds_over_2_5": 1.95,
            "open_odds_dnb_team1": 1.67,
            "close_odds_dnb_team1": 1.58,
        }

        entries = _extract_value_candidate_clv_entries(row)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["market_field"], "odds_over_2_5")
        self.assertTrue(entries[0]["beat_close"])
        self.assertEqual(entries[0]["settlement"], "WIN")
        self.assertEqual(entries[1]["market_field"], "odds_dnb_team1")
        self.assertTrue(entries[1]["beat_close"])
        self.assertEqual(entries[1]["settlement"], "WIN")

    def test_grade_market_selection_supports_main_supported_markets(self):
        win_row = {
            "actual_result": "W1",
            "actual_goals_home": 2,
            "actual_goals_away": 1,
        }
        draw_row = {
            "actual_result": "DRAW",
            "actual_goals_home": 1,
            "actual_goals_away": 1,
        }

        self.assertEqual(_grade_market_selection(win_row, "1X2", "TEAM1"), "WIN")
        self.assertEqual(_grade_market_selection(win_row, "1X2", "TEAM2"), "LOSS")
        self.assertEqual(_grade_market_selection(win_row, "TOTALS_2_5", "OVER"), "WIN")
        self.assertEqual(_grade_market_selection(win_row, "TOTALS_3_5", "UNDER"), "WIN")
        self.assertEqual(_grade_market_selection(win_row, "BTTS", "YES"), "WIN")
        self.assertEqual(_grade_market_selection(win_row, "DNB", "TEAM1"), "WIN")
        self.assertEqual(_grade_market_selection(draw_row, "DNB", "TEAM1"), "PUSH")
        self.assertEqual(_grade_market_selection(win_row, "DOUBLE_CHANCE", "1X"), "WIN")
        self.assertEqual(_grade_market_selection(draw_row, "DOUBLE_CHANCE", "12"), "LOSS")

    def test_segment_sample_gate_marks_stable_and_sorts_ahead_of_small_segments(self):
        stable_entries = [
            {
                "family": "1x2",
                "competition_name": "Premier League",
                "beat_close": idx % 2 == 0,
                "flat_close": False,
                "entry_to_close_delta": -0.05 if idx % 2 == 0 else 0.03,
                "open_to_close_delta": -0.08 if idx % 2 == 0 else 0.02,
                "implied_prob_delta_pct": 1.1 if idx % 2 == 0 else -0.6,
                "settlement": "WIN" if idx % 2 == 0 else "LOSS",
                "profit_units": 0.9 if idx % 2 == 0 else -1.0,
            }
            for idx in range(20)
        ]
        low_entries = [
            {
                "family": "totals",
                "competition_name": "La Liga",
                "beat_close": True,
                "flat_close": False,
                "entry_to_close_delta": -0.04,
                "open_to_close_delta": -0.05,
                "implied_prob_delta_pct": 0.8,
                "settlement": "WIN",
                "profit_units": 0.82,
            }
            for _ in range(3)
        ]

        stable_summary = _summarize_prediction_clv(stable_entries)
        assert stable_summary is not None
        self.assertEqual(_segment_sample_gate(stable_summary), ("stable", 3))

        segments = _segment_prediction_clv(stable_entries + low_entries, key_name="family")

        self.assertEqual(segments[0]["segment"], "1x2")
        self.assertEqual(segments[0]["sample_gate"], "stable")
        self.assertEqual(segments[1]["segment"], "totals")
        self.assertEqual(segments[1]["sample_gate"], "low-sample")

    def test_time_split_rows_orders_by_date_and_partitions_chronologically(self):
        rows = [
            {"predicted_at": "2026-03-09T20:00:00+00:00", "id": 9},
            {"predicted_at": "2026-03-01T20:00:00+00:00", "id": 1},
            {"predicted_at": "2026-03-08T20:00:00+00:00", "id": 8},
            {"predicted_at": "2026-03-02T20:00:00+00:00", "id": 2},
            {"predicted_at": "2026-03-03T20:00:00+00:00", "id": 3},
            {"predicted_at": "2026-03-04T20:00:00+00:00", "id": 4},
            {"predicted_at": "2026-03-05T20:00:00+00:00", "id": 5},
            {"predicted_at": "2026-03-06T20:00:00+00:00", "id": 6},
            {"predicted_at": "2026-03-07T20:00:00+00:00", "id": 7},
        ]

        splits = _time_split_rows(rows)

        self.assertEqual([split["label"] for split in splits], ["Train", "Validate", "Test"])
        self.assertEqual([row["id"] for row in splits[0]["rows"]], [1, 2, 3, 4, 5])
        self.assertEqual([row["id"] for row in splits[1]["rows"]], [6])
        self.assertEqual([row["id"] for row in splits[2]["rows"]], [7, 8, 9])
        self.assertEqual(splits[0]["date_from"], "2026-03-01")
        self.assertEqual(splits[2]["date_to"], "2026-03-09")

    def test_time_split_rows_requires_enough_rows(self):
        rows = [
            {"predicted_at": f"2026-03-0{idx}T20:00:00+00:00", "id": idx}
            for idx in range(1, 8)
        ]

        self.assertEqual(_time_split_rows(rows), [])

    def test_summarize_market_coverage_counts_available_market_families(self):
        rows = [
            {
                "odds_w1": 1.9,
                "odds_draw": 3.4,
                "odds_w2": 4.2,
                "odds_over_2_5": 2.0,
                "odds_under_2_5": 1.8,
                "odds_btts_yes": 1.9,
                "odds_btts_no": 1.9,
                "odds_dnb_team1": 1.5,
                "odds_dnb_team2": 2.6,
                "odds_dc_1x": 1.2,
                "odds_dc_12": 1.3,
                "odds_dc_x2": 1.8,
                "ah_team1_odds": 1.95,
                "ah_team2_odds": 1.9,
            },
            {
                "odds_w1": 2.1,
                "odds_draw": 3.2,
                "odds_w2": 3.7,
                "odds_over_2_5": 2.1,
                "odds_under_2_5": 1.75,
                "odds_btts_yes": None,
                "odds_btts_no": None,
                "odds_dnb_team1": 1.7,
                "odds_dnb_team2": 2.2,
                "odds_dc_1x": 1.3,
                "odds_dc_12": 1.4,
                "odds_dc_x2": 1.7,
                "ah_team1_odds": None,
                "ah_team2_odds": None,
            },
        ]

        summary = _summarize_market_coverage(rows)

        self.assertEqual(summary["1X2"]["count"], 2)
        self.assertEqual(summary["O/U 2.5"]["count"], 2)
        self.assertEqual(summary["BTTS"]["count"], 1)
        self.assertEqual(summary["DNB"]["count"], 2)
        self.assertEqual(summary["Double Chance"]["count"], 2)
        self.assertEqual(summary["AH"]["count"], 1)

    def test_market_family_trust_band_and_sorting_prefer_stable_high_signal_profiles(self):
        signal, sample_band, sample_rank = _market_family_trust_band(
            {"sample_size": 420, "trust_score": 2.1}
        )
        self.assertEqual((signal, sample_band, sample_rank), ("strong", "stable", 3))

        profiles = [
            {
                "competition_key": None,
                "family": "totals",
                "sample_size": 90,
                "model_brier": 0.236,
                "market_brier": 0.237,
                "brier_delta": 0.001,
                "trust_score": 0.8,
            },
            {
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 620,
                "model_brier": 0.236,
                "market_brier": 0.239,
                "brier_delta": 0.003,
                "trust_score": 2.4,
            },
            {
                "competition_key": "la liga",
                "family": "1x2",
                "sample_size": 310,
                "model_brier": 0.191,
                "market_brier": 0.190,
                "brier_delta": -0.001,
                "trust_score": -0.9,
            },
        ]

        prepared = _prepare_market_family_trust_rows(profiles, include_competition=True)

        self.assertEqual(prepared[0]["segment"], "premier league / btts")
        self.assertEqual(prepared[0]["signal"], "strong")
        self.assertEqual(prepared[0]["sample_band"], "stable")
        self.assertEqual(prepared[1]["segment"], "la liga / 1x2")

    def test_summarize_family_gating_impact_tracks_btts_promotions_and_demotions(self):
        rows = [
            {
                "team1": "Arsenal",
                "team2": "Chelsea",
                "competition_name": "Premier League",
                "data_completeness": 0.38,
                "successful_source_count": 6,
                "total_source_count": 8,
                "market_prob_w1": 50.0,
                "market_prob_draw": 26.0,
                "market_prob_w2": 24.0,
                "market_prob_btts_yes": 50.0,
                "market_prob_btts_no": 50.0,
                "_numeric_baseline_recalibrated": {
                    "prob_w1": 52.0,
                    "prob_draw": 26.0,
                    "prob_w2": 22.0,
                    "prob_over_2_5": 53.0,
                    "prob_under_2_5": 47.0,
                    "prob_btts_yes": 54.0,
                    "prob_btts_no": 46.0,
                    "expected_goals": 2.7,
                },
                "_family_quality_profiles": [
                    {"family": "btts", "sample_size": 620, "trust_score": 2.4},
                ],
            },
            {
                "team1": "Roma",
                "team2": "Lazio",
                "competition_name": "Serie A",
                "data_completeness": 0.72,
                "successful_source_count": 7,
                "total_source_count": 8,
                "market_prob_w1": 40.0,
                "market_prob_draw": 30.0,
                "market_prob_w2": 30.0,
                "market_prob_btts_yes": 50.0,
                "market_prob_btts_no": 50.0,
                "_numeric_baseline_recalibrated": {
                    "prob_w1": 41.0,
                    "prob_draw": 29.0,
                    "prob_w2": 30.0,
                    "prob_over_2_5": 51.0,
                    "prob_under_2_5": 49.0,
                    "prob_btts_yes": 53.1,
                    "prob_btts_no": 46.9,
                    "expected_goals": 2.55,
                },
                "_family_quality_profiles": [
                    {"family": "btts", "sample_size": 540, "trust_score": -2.1},
                ],
            },
        ]

        self.assertIsNotNone(_candidate_report_from_row(rows[0]))

        summary = _summarize_family_gating_impact(rows, family="btts")

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["considered"], 2)
        self.assertEqual(summary["promoted_top"], 1)
        self.assertEqual(summary["demoted_top"], 1)
        self.assertEqual(summary["positive_trust_rows"], 1)
        self.assertEqual(summary["negative_trust_rows"], 1)

    def test_action_core_preference_impact_tracks_derived_to_core_switch(self):
        rows = [
            {
                "team1": "Arsenal",
                "team2": "Chelsea",
                "competition_name": "Premier League",
                "data_completeness": 0.72,
                "successful_source_count": 4,
                "total_source_count": 5,
                "market_prob_w1": 45.0,
                "market_prob_draw": 28.0,
                "market_prob_w2": 27.0,
                "market_prob_btts_yes": 55.0,
                "market_prob_btts_no": 45.0,
                "_numeric_baseline_recalibrated": {
                    "prob_w1": 48.0,
                    "prob_draw": 27.0,
                    "prob_w2": 25.0,
                    "prob_over_2_5": 52.0,
                    "prob_under_2_5": 48.0,
                    "prob_btts_yes": 58.0,
                    "prob_btts_no": 42.0,
                    "expected_goals": 2.65,
                },
                "_family_quality_profiles": [
                    {"family": "btts", "sample_size": 780, "trust_score": 2.7},
                    {"family": "1x2", "sample_size": 780, "trust_score": -0.8},
                ],
            }
        ]

        summary = _summarize_action_core_preference_impact(rows)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["considered"], 1)
        self.assertEqual(summary["switched"], 1)
        self.assertEqual(summary["switched_to_core"], 1)
        self.assertEqual(summary["derived_to_core"], 1)
        self.assertEqual(summary["switched_to_btts"], 1)
        self.assertEqual(summary["top_competitions"], [("Premier League", 1)])
        self.assertEqual(summary["top_target_families"], [("btts", 1)])

    def test_top_pick_core_preference_impact_tracks_derived_to_core_switch(self):
        rows = [
            {
                "team1": "Arsenal",
                "team2": "Chelsea",
                "competition_name": "Premier League",
                "data_completeness": 0.72,
                "successful_source_count": 4,
                "total_source_count": 5,
                "market_prob_w1": 45.0,
                "market_prob_draw": 28.0,
                "market_prob_w2": 27.0,
                "market_prob_btts_yes": 55.0,
                "market_prob_btts_no": 45.0,
                "_numeric_baseline_recalibrated": {
                    "prob_w1": 48.0,
                    "prob_draw": 27.0,
                    "prob_w2": 25.0,
                    "prob_over_2_5": 52.0,
                    "prob_under_2_5": 48.0,
                    "prob_btts_yes": 58.0,
                    "prob_btts_no": 42.0,
                    "expected_goals": 2.65,
                },
                "_family_quality_profiles": [
                    {"family": "btts", "sample_size": 780, "trust_score": 2.7},
                    {"family": "1x2", "sample_size": 780, "trust_score": -0.8},
                ],
            }
        ]

        summary = _summarize_top_pick_core_preference_impact(rows)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["considered"], 1)
        self.assertEqual(summary["switched"], 1)
        self.assertEqual(summary["derived_to_core"], 1)
        self.assertEqual(summary["switched_to_btts"], 1)
        self.assertEqual(summary["top_competitions"], [("Premier League", 1)])
        self.assertEqual(summary["top_target_families"], [("btts", 1)])
