from __future__ import annotations

import unittest

from baseline_model import (
    apply_pick_policy,
    apply_stake_recommendation,
    apply_top_pick_identity,
    build_market_candidates,
    build_numeric_baseline,
    build_value_candidate_snapshot,
    choose_action_pick,
    derive_expanded_market_probabilities,
    estimate_market_edge_for_top_pick,
    extract_feature_snapshot,
    infer_blended_probabilities,
)
from unittest.mock import patch
from models import (
    AdvancedStatsData,
    FlashscoreData,
    H2HRecord,
    MatchContextData,
    MatchData,
    MatchResult,
    OddsData,
    PredictionFeatureSnapshot,
    RecentMatch,
    ScheduleLoadData,
    SofascoreData,
    SourceStatus,
    TeamAdvancedMetrics,
    TeamScheduleLoadSummary,
    TransfermarktData,
)


class BaselineModelTests(unittest.TestCase):
    def test_extract_feature_snapshot_captures_core_numeric_signals(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            flashscore=FlashscoreData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_form=[
                    RecentMatch(date="2026-03-01", opponent="A", result=MatchResult.WIN, score="2-0", home_away="H"),
                    RecentMatch(date="2026-02-25", opponent="B", result=MatchResult.DRAW, score="1-1", home_away="A"),
                ],
                team2_form=[
                    RecentMatch(date="2026-03-01", opponent="C", result=MatchResult.LOSS, score="0-2", home_away="H"),
                    RecentMatch(date="2026-02-24", opponent="D", result=MatchResult.DRAW, score="1-1", home_away="A"),
                ],
                h2h_matches=[
                    H2HRecord(date="2025-12-01", home_team="Arsenal", away_team="Chelsea", score="2-1"),
                    H2HRecord(date="2025-08-01", home_team="Chelsea", away_team="Arsenal", score="1-1"),
                ],
                team1_goals_scored_avg=1.8,
                team1_goals_conceded_avg=0.9,
                team2_goals_scored_avg=1.1,
                team2_goals_conceded_avg=1.4,
                scrape_success=True,
            ),
            odds=OddsData(
                implied_prob_w1=0.51,
                implied_prob_draw=0.26,
                implied_prob_w2=0.23,
                over_2_5_odds=1.92,
                under_2_5_odds=1.88,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            advanced_stats=AdvancedStatsData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_metrics=TeamAdvancedMetrics(
                    xg=58.0,
                    xga=30.0,
                    rating=7.3,
                    possession_pct=57.0,
                    shots_on_target_per_match=5.4,
                    corners=6.3,
                ),
                team2_metrics=TeamAdvancedMetrics(
                    xg=46.0,
                    xga=38.0,
                    rating=6.8,
                    possession_pct=49.0,
                    shots_on_target_per_match=3.8,
                    corners=4.7,
                ),
                scrape_success=True,
            ),
            match_context=MatchContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                competition_name="Premier League",
                is_neutral_venue=False,
                is_team1_home=True,
                scrape_success=True,
            ),
            sofascore=SofascoreData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_key_absences=[],
                team2_key_absences=[],
                scrape_success=True,
            ),
            transfermarkt=TransfermarktData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_market_value_eur=950.0,
                team2_market_value_eur=800.0,
                scrape_success=True,
            ),
            schedule_load=ScheduleLoadData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1=TeamScheduleLoadSummary(rest_hours=120.0, short_rest=False),
                team2=TeamScheduleLoadSummary(rest_hours=72.0, short_rest=True),
                rest_gap_hours=48.0,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
                SourceStatus(key="advanced", label="Advanced", field_name="advanced_stats", scrape_success=True),
                SourceStatus(key="context", label="Context", field_name="match_context", scrape_success=True),
            ],
        )

        snapshot = extract_feature_snapshot(data)

        self.assertEqual(snapshot.competition_name, "Premier League")
        self.assertAlmostEqual(snapshot.market_prob_w1 or 0.0, 51.0)
        self.assertGreater(snapshot.form_points_last5_diff or 0.0, 0.0)
        self.assertGreater(snapshot.xg_diff or 0.0, 0.0)
        self.assertGreater(snapshot.shots_on_target_avg_diff or 0.0, 0.0)
        self.assertGreater(snapshot.corners_avg_diff or 0.0, 0.0)
        self.assertTrue(snapshot.is_team1_home)
        self.assertEqual(snapshot.rest_gap_hours, 48.0)

    def test_build_numeric_baseline_returns_normalized_probabilities(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.8,
            successful_source_count=4,
            total_source_count=5,
            market_prob_w1=52.0,
            market_prob_draw=25.0,
            market_prob_w2=23.0,
            market_prob_over_2_5=53.0,
            market_prob_under_2_5=47.0,
            market_prob_btts_yes=51.0,
            market_prob_btts_no=49.0,
            form_points_last5_diff=5.0,
            xg_diff=8.0,
            xga_edge=4.0,
            standings_point_gap=10.0,
            rest_gap_hours=36.0,
            is_team1_home=True,
        )

        result = build_numeric_baseline(MatchData(team1="Arsenal", team2="Chelsea"), snapshot)

        self.assertAlmostEqual(result["prob_w1"] + result["prob_draw"] + result["prob_w2"], 100.0, places=1)
        self.assertAlmostEqual(result["prob_over_2_5"] + result["prob_under_2_5"], 100.0, places=1)
        self.assertAlmostEqual(result["prob_btts_yes"] + result["prob_btts_no"], 100.0, places=1)
        self.assertGreater(result["prob_w1"], result["prob_w2"])

    def test_build_numeric_baseline_applies_recalibration_profile(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            competition_name="Premier League",
            data_completeness=0.8,
            successful_source_count=4,
            total_source_count=5,
            market_prob_w1=52.0,
            market_prob_draw=25.0,
            market_prob_w2=23.0,
            market_prob_over_2_5=53.0,
            market_prob_under_2_5=47.0,
            market_prob_btts_yes=51.0,
            market_prob_btts_no=49.0,
            form_points_last5_diff=5.0,
            xg_diff=8.0,
            xga_edge=4.0,
            standings_point_gap=10.0,
            rest_gap_hours=36.0,
            is_team1_home=True,
        )

        raw = build_numeric_baseline(MatchData(team1="Arsenal", team2="Chelsea"), snapshot)
        top_key = max(("prob_w1", "prob_draw", "prob_w2"), key=lambda key: raw[key])
        bucket_low = int((raw[top_key] // 5) * 5)
        bucket_high = min(100, bucket_low + 5)
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "market": "1X2_TOP_PICK",
                "bucket_label": f"{bucket_low}-{bucket_high}",
                "delta_pct": -6.0,
            }
        ]

        adjusted = build_numeric_baseline(
            MatchData(team1="Arsenal", team2="Chelsea"),
            snapshot,
            recalibration_profiles=profiles,
        )

        self.assertLess(adjusted[top_key], raw[top_key])
        self.assertAlmostEqual(
            adjusted["prob_w1"] + adjusted["prob_draw"] + adjusted["prob_w2"],
            100.0,
            places=1,
        )

    def test_build_numeric_baseline_prefers_segmented_btts_recalibration(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            competition_name="Premier League",
            data_completeness=0.8,
            successful_source_count=4,
            total_source_count=5,
            market_prob_w1=52.0,
            market_prob_draw=25.0,
            market_prob_w2=23.0,
            market_prob_over_2_5=45.0,
            market_prob_under_2_5=55.0,
            market_prob_btts_yes=51.0,
            market_prob_btts_no=49.0,
            form_points_last5_diff=2.0,
            xg_diff=1.0,
            xga_edge=0.5,
            is_team1_home=True,
        )

        raw = build_numeric_baseline(MatchData(team1="Arsenal", team2="Chelsea"), snapshot)
        bucket_low = int((raw["prob_btts_yes"] // 5) * 5)
        bucket_high = min(100, bucket_low + 5)
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "market": "BTTS_YES",
                "bucket_label": f"{bucket_low}-{bucket_high}",
                "delta_pct": -1.0,
            },
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "market": "BTTS_YES_LOW_TOTALS",
                "bucket_label": f"{bucket_low}-{bucket_high}",
                "delta_pct": -6.0,
            },
        ]

        adjusted = build_numeric_baseline(
            MatchData(team1="Arsenal", team2="Chelsea"),
            snapshot,
            recalibration_profiles=profiles,
        )

        self.assertLess(adjusted["prob_btts_yes"], raw["prob_btts_yes"] - 3.0)
        self.assertAlmostEqual(
            adjusted["prob_btts_yes"] + adjusted["prob_btts_no"],
            100.0,
            places=1,
        )

    def test_infer_blended_probabilities_uses_stored_alpha(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.75,
            llm_prob_w1=60.0,
            llm_prob_draw=22.0,
            llm_prob_w2=18.0,
            llm_prob_over_2_5=58.0,
            llm_prob_under_2_5=42.0,
            llm_prob_btts_yes=54.0,
            llm_prob_btts_no=46.0,
            model_prob_w1=48.0,
            model_prob_draw=28.0,
            model_prob_w2=24.0,
            model_prob_over_2_5=52.0,
            model_prob_under_2_5=48.0,
            model_prob_btts_yes=50.0,
            model_prob_btts_no=50.0,
            blend_alpha=0.25,
        )

        blended = infer_blended_probabilities(snapshot)

        self.assertIsNotNone(blended)
        self.assertAlmostEqual(blended["prob_w1"], 57.0, places=1)
        self.assertAlmostEqual(blended["prob_draw"], 23.5, places=1)

    def test_estimate_market_edge_for_top_pick_uses_matching_market(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.7,
            market_prob_over_2_5=49.0,
            market_prob_w1=52.0,
            market_prob_draw=25.0,
            market_prob_w2=23.0,
        )

        edge = estimate_market_edge_for_top_pick(
            "Over 2.5",
            {"prob_over_2_5": 55.0},
            snapshot,
        )

        self.assertEqual(edge, 6.0)

    def test_derive_expanded_market_probabilities_includes_alt_totals_and_composites(self):
        derived = derive_expanded_market_probabilities(
            {
                "prob_w1": 48.0,
                "prob_draw": 27.0,
                "prob_w2": 25.0,
                "expected_goals": 2.4,
            }
        )

        self.assertIn("prob_over_1_5", derived)
        self.assertIn("prob_under_3_5", derived)
        self.assertAlmostEqual(derived["prob_double_chance_1x"], 75.0, places=1)
        self.assertAlmostEqual(derived["prob_double_chance_x2"], 52.0, places=1)
        self.assertAlmostEqual(derived["prob_dnb_team1"] + derived["prob_dnb_team2"], 100.0, places=1)

    def test_build_market_candidates_includes_expanded_markets(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=50.0,
            market_prob_draw=27.0,
            market_prob_w2=23.0,
            market_prob_over_2_5=47.0,
            market_prob_under_2_5=53.0,
            market_prob_btts_yes=49.0,
            market_prob_btts_no=51.0,
        )
        report_dict = {
            "prob_w1": 51.0,
            "prob_draw": 26.0,
            "prob_w2": 23.0,
            "prob_over_2_5": 46.0,
            "prob_under_2_5": 54.0,
            "prob_btts_yes": 48.0,
            "prob_btts_no": 52.0,
            "expected_goals": 2.35,
        }
        odds = OddsData(
            team1_win_odds=2.02,
            draw_odds=3.25,
            team2_win_odds=3.85,
            over_1_5_odds=1.30,
            under_1_5_odds=3.55,
            over_2_5_odds=2.02,
            under_2_5_odds=1.82,
            over_3_5_odds=3.05,
            under_3_5_odds=1.40,
            btts_yes_odds=1.92,
            btts_no_odds=1.88,
        )

        candidates = build_market_candidates(report_dict, snapshot, odds)
        labels = {candidate["label"] for candidate in candidates}
        identity_by_label = {
            candidate["label"]: (candidate.get("market_code"), candidate.get("selection_code"))
            for candidate in candidates
        }

        self.assertIn("Over 1.5", labels)
        self.assertIn("Under 3.5", labels)
        self.assertIn("Arsenal Draw No Bet", labels)
        self.assertIn("Arsenal or Draw (1X)", labels)
        self.assertEqual(identity_by_label["Arsenal"], ("1X2", "TEAM1"))
        self.assertEqual(identity_by_label["Under 3.5"], ("TOTALS_3_5", "UNDER"))
        self.assertEqual(identity_by_label["Arsenal Draw No Bet"], ("DNB", "TEAM1"))
        self.assertEqual(identity_by_label["Arsenal or Draw (1X)"], ("DOUBLE_CHANCE", "1X"))

    def test_build_market_candidates_marks_secondary_markets_as_ineligible_on_thin_edge(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=50.0,
            market_prob_draw=27.0,
            market_prob_w2=23.0,
        )
        report_dict = {
            "prob_w1": 56.0,
            "prob_draw": 24.0,
            "prob_w2": 20.0,
            "prob_dnb_team1": 70.0,
            "prob_dnb_team2": 30.0,
            "prob_double_chance_1x": 78.0,
            "prob_double_chance_12": 74.0,
            "prob_double_chance_x2": 48.0,
        }
        odds = OddsData(
            team1_win_odds=1.85,
            draw_odds=3.30,
            team2_win_odds=3.90,
        )

        candidates = build_market_candidates(report_dict, snapshot, odds)
        eligibility = {
            candidate["label"]: bool(candidate.get("eligible_for_top_pick"))
            for candidate in candidates
        }

        self.assertTrue(eligibility["Arsenal"])
        self.assertFalse(eligibility["Arsenal Draw No Bet"])
        self.assertFalse(eligibility["Arsenal or Draw (1X)"])

    def test_apply_pick_policy_action_mode_replaces_no_bet_with_candidate(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.61,
            market_prob_w1=49.0,
            market_prob_draw=27.0,
            market_prob_w2=24.0,
            market_prob_over_2_5=45.0,
            market_prob_under_2_5=55.0,
            market_prob_btts_yes=50.0,
            market_prob_btts_no=50.0,
            llm_model_gap_pct=6.0,
        )
        report_dict = {
            "prob_w1": 50.0,
            "prob_draw": 27.0,
            "prob_w2": 23.0,
            "prob_over_2_5": 45.0,
            "prob_under_2_5": 55.0,
            "prob_btts_yes": 49.0,
            "prob_btts_no": 51.0,
            "prob_over_1_5": 75.0,
            "prob_under_1_5": 25.0,
            "prob_over_3_5": 24.0,
            "prob_under_3_5": 76.0,
            "prob_dnb_team1": 68.0,
            "prob_dnb_team2": 32.0,
            "prob_double_chance_1x": 77.0,
            "prob_double_chance_12": 73.0,
            "prob_double_chance_x2": 50.0,
            "top_recommendation": "Over 2.5",
            "top_confidence_pct": 52.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Thin pre-match edge.",
            "data_warnings": [],
        }
        odds = OddsData(
            team1_win_odds=2.08,
            draw_odds=3.30,
            team2_win_odds=3.95,
            over_1_5_odds=1.31,
            under_1_5_odds=3.50,
            over_2_5_odds=2.02,
            under_2_5_odds=1.84,
            over_3_5_odds=3.20,
            under_3_5_odds=1.42,
            btts_yes_odds=1.96,
            btts_no_odds=1.86,
        )

        with patch("config.RECOMMENDATION_MODE", "action"):
            apply_pick_policy(report_dict, snapshot, odds)
        apply_top_pick_identity(report_dict, snapshot, odds)

        self.assertFalse(report_dict["top_recommendation"].startswith("No bet"))
        self.assertTrue(any("Action mode pick" in warning for warning in report_dict["data_warnings"]))
        self.assertFalse(snapshot.no_bet_triggered)
        self.assertIsNotNone(report_dict["top_market_code"])
        self.assertIsNotNone(report_dict["top_selection_code"])

    def test_choose_action_pick_skips_secondary_market_without_extra_edge(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.66,
            market_prob_w1=50.0,
            market_prob_draw=27.0,
            market_prob_w2=23.0,
        )
        report_dict = {
            "prob_w1": 52.0,
            "prob_draw": 27.0,
            "prob_w2": 21.0,
            "prob_dnb_team1": 69.5,
            "prob_dnb_team2": 30.5,
            "prob_double_chance_1x": 78.0,
            "prob_double_chance_12": 73.0,
            "prob_double_chance_x2": 48.0,
        }
        odds = OddsData(
            team1_win_odds=2.08,
            draw_odds=3.28,
            team2_win_odds=3.95,
        )

        candidate = choose_action_pick(report_dict, snapshot, odds)

        self.assertIsNone(candidate)

    def test_choose_action_pick_prefers_close_core_market_when_trust_is_stronger(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=45.0,
            market_prob_draw=28.0,
            market_prob_w2=27.0,
            market_prob_btts_yes=55.0,
            market_prob_btts_no=45.0,
        )
        report_dict = {
            "prob_w1": 47.0,
            "prob_draw": 28.0,
            "prob_w2": 25.0,
            "prob_dnb_team1": 65.9,
            "prob_dnb_team2": 34.1,
            "prob_btts_yes": 58.0,
            "prob_btts_no": 42.0,
        }
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 780,
                "trust_score": 2.7,
            },
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "1x2",
                "sample_size": 780,
                "trust_score": -0.8,
            },
        ]

        candidates = build_market_candidates(report_dict, snapshot, None, profiles)
        viable = [candidate for candidate in candidates if candidate["eligible_for_action_pick"]]
        candidate = choose_action_pick(report_dict, snapshot, None, profiles)

        self.assertEqual(viable[0]["label"], "Arsenal Draw No Bet")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["label"], "BTTS Yes")

    def test_choose_action_pick_keeps_clear_derived_leader(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=45.0,
            market_prob_draw=28.0,
            market_prob_w2=27.0,
            market_prob_btts_yes=55.0,
            market_prob_btts_no=45.0,
        )
        report_dict = {
            "prob_w1": 47.0,
            "prob_draw": 28.0,
            "prob_w2": 25.0,
            "prob_dnb_team1": 68.5,
            "prob_dnb_team2": 31.5,
            "prob_btts_yes": 58.0,
            "prob_btts_no": 42.0,
        }
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 780,
                "trust_score": 2.7,
            },
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "1x2",
                "sample_size": 780,
                "trust_score": -0.8,
            },
        ]

        candidate = choose_action_pick(report_dict, snapshot, None, profiles)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["label"], "Arsenal Draw No Bet")

    def test_apply_pick_policy_rejects_secondary_market_with_thin_edge(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=50.0,
            market_prob_draw=27.0,
            market_prob_w2=23.0,
            llm_model_gap_pct=4.0,
        )
        report_dict = {
            "prob_w1": 52.0,
            "prob_draw": 26.0,
            "prob_w2": 22.0,
            "prob_dnb_team1": 69.8,
            "prob_dnb_team2": 30.2,
            "top_recommendation": "Arsenal Draw No Bet",
            "top_confidence_pct": 59.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Thin edge on a derived market.",
            "data_warnings": [],
        }
        odds = OddsData(
            team1_win_odds=2.06,
            draw_odds=3.30,
            team2_win_odds=3.85,
        )

        with patch("config.RECOMMENDATION_MODE", "balanced"):
            apply_pick_policy(report_dict, snapshot, odds)

        self.assertTrue(report_dict["top_recommendation"].startswith("No bet"))
        self.assertTrue(any("draw-no-bet edge not strong enough" in warning for warning in report_dict["data_warnings"]))
        self.assertTrue(snapshot.no_bet_triggered)

    def test_apply_pick_policy_normalizes_close_derived_top_pick_to_core_market(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=45.0,
            market_prob_draw=28.0,
            market_prob_w2=27.0,
            market_prob_btts_yes=55.0,
            market_prob_btts_no=45.0,
            llm_model_gap_pct=4.0,
        )
        report_dict = {
            "prob_w1": 48.0,
            "prob_draw": 27.0,
            "prob_w2": 25.0,
            "prob_btts_yes": 58.0,
            "prob_btts_no": 42.0,
            "prob_dnb_team1": 65.8,
            "prob_dnb_team2": 34.2,
            "top_recommendation": "Arsenal Draw No Bet",
            "top_confidence_pct": 68.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Initial top pick came from a thin derived edge.",
            "data_warnings": [],
        }
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 780,
                "trust_score": 2.7,
            },
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "1x2",
                "sample_size": 780,
                "trust_score": -0.8,
            },
        ]

        apply_pick_policy(report_dict, snapshot, None, profiles)

        self.assertEqual(report_dict["top_recommendation"], "BTTS Yes")
        self.assertIn("Top pick normalized to stronger core market", report_dict["data_warnings"])
        self.assertIn("normalized from Arsenal Draw No Bet to BTTS Yes", report_dict["analysis_summary"])
        self.assertFalse(snapshot.no_bet_triggered)

    def test_apply_pick_policy_keeps_clear_derived_top_pick_when_gap_is_large(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=45.0,
            market_prob_draw=28.0,
            market_prob_w2=27.0,
            market_prob_btts_yes=55.0,
            market_prob_btts_no=45.0,
            llm_model_gap_pct=4.0,
        )
        report_dict = {
            "prob_w1": 49.0,
            "prob_draw": 26.0,
            "prob_w2": 25.0,
            "prob_btts_yes": 58.0,
            "prob_btts_no": 42.0,
            "prob_dnb_team1": 66.2,
            "prob_dnb_team2": 33.8,
            "top_recommendation": "Arsenal Draw No Bet",
            "top_confidence_pct": 68.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Derived market is clearly ahead.",
            "data_warnings": [],
        }
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 780,
                "trust_score": 2.7,
            },
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "1x2",
                "sample_size": 780,
                "trust_score": -0.8,
            },
        ]

        apply_pick_policy(report_dict, snapshot, None, profiles)

        self.assertEqual(report_dict["top_recommendation"], "Arsenal Draw No Bet")
        self.assertNotIn("Top pick normalized to stronger core market", report_dict["data_warnings"])

    def test_build_market_candidates_uses_historical_family_quality_to_tighten_btts(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.7,
            market_prob_btts_yes=49.0,
            market_prob_btts_no=51.0,
        )
        report_dict = {
            "prob_btts_yes": 53.5,
            "prob_btts_no": 46.5,
        }

        candidates = build_market_candidates(
            report_dict,
            snapshot,
            None,
            [
                {
                    "scope": "COMPETITION",
                    "competition_key": "premier league",
                    "family": "btts",
                    "sample_size": 420,
                    "trust_score": -2.4,
                }
            ],
        )
        btts_candidate = next(candidate for candidate in candidates if candidate["label"] == "BTTS Yes")

        self.assertFalse(btts_candidate["eligible_for_top_pick"])
        self.assertFalse(btts_candidate["eligible_for_action_pick"])

    def test_build_market_candidates_promotes_core_btts_when_historical_trust_is_strong(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.74,
            market_prob_w1=50.0,
            market_prob_draw=26.0,
            market_prob_w2=24.0,
            market_prob_btts_yes=49.0,
            market_prob_btts_no=51.0,
        )
        report_dict = {
            "prob_w1": 52.0,
            "prob_draw": 26.0,
            "prob_w2": 22.0,
            "prob_btts_yes": 55.0,
            "prob_btts_no": 45.0,
        }
        positive_profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 620,
                "trust_score": 2.6,
            }
        ]

        neutral_candidates = build_market_candidates(report_dict, snapshot, None, [])
        positive_candidates = build_market_candidates(report_dict, snapshot, None, positive_profiles)

        neutral_btts = next(candidate for candidate in neutral_candidates if candidate["label"] == "BTTS Yes")
        positive_btts = next(candidate for candidate in positive_candidates if candidate["label"] == "BTTS Yes")

        self.assertGreater(positive_btts["score"], neutral_btts["score"])
        self.assertGreater(positive_btts["confidence_pct"], neutral_btts["confidence_pct"])
        self.assertGreater(positive_btts["family_quality_score_bonus"], 0.0)

    def test_strong_btts_trust_can_unlock_borderline_core_btts_candidate(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.38,
            market_prob_btts_yes=50.0,
            market_prob_btts_no=50.0,
        )
        report_dict = {
            "prob_btts_yes": 54.0,
            "prob_btts_no": 46.0,
        }
        strong_profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 640,
                "trust_score": 2.4,
            }
        ]

        neutral_candidates = build_market_candidates(report_dict, snapshot, None, [])
        strong_candidates = build_market_candidates(report_dict, snapshot, None, strong_profiles)

        neutral_btts = next(candidate for candidate in neutral_candidates if candidate["label"] == "BTTS Yes")
        strong_btts = next(candidate for candidate in strong_candidates if candidate["label"] == "BTTS Yes")

        self.assertFalse(neutral_btts["eligible_for_top_pick"])
        self.assertTrue(strong_btts["eligible_for_top_pick"])

    def test_btts_borderline_rerank_can_win_close_core_decision(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=53.5,
            market_prob_draw=25.0,
            market_prob_w2=21.5,
            market_prob_btts_yes=51.0,
            market_prob_btts_no=49.0,
        )
        report_dict = {
            "prob_w1": 55.8,
            "prob_draw": 24.2,
            "prob_w2": 20.0,
            "prob_btts_yes": 53.0,
            "prob_btts_no": 47.0,
        }
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 780,
                "trust_score": 2.7,
            },
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "1x2",
                "sample_size": 780,
                "trust_score": -1.1,
            },
        ]

        neutral_candidates = build_market_candidates(report_dict, snapshot, None, [])
        reranked_candidates = build_market_candidates(report_dict, snapshot, None, profiles)
        neutral_core = next(
            candidate for candidate in neutral_candidates
            if candidate["family"] in {"1x2", "totals", "btts"}
        )
        reranked_core = next(
            candidate for candidate in reranked_candidates
            if candidate["family"] in {"1x2", "totals", "btts"}
        )

        self.assertEqual(neutral_core["label"], "Arsenal")
        self.assertEqual(reranked_core["label"], "BTTS Yes")
        self.assertGreater(reranked_core["family_borderline_rerank_bonus"], 0.0)

    def test_btts_borderline_rerank_does_not_override_clear_core_pick(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.72,
            market_prob_w1=52.0,
            market_prob_draw=25.0,
            market_prob_w2=23.0,
            market_prob_btts_yes=51.0,
            market_prob_btts_no=49.0,
        )
        report_dict = {
            "prob_w1": 56.0,
            "prob_draw": 24.0,
            "prob_w2": 20.0,
            "prob_btts_yes": 53.0,
            "prob_btts_no": 47.0,
        }
        profiles = [
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "btts",
                "sample_size": 780,
                "trust_score": 2.7,
            },
            {
                "scope": "COMPETITION",
                "competition_key": "premier league",
                "family": "1x2",
                "sample_size": 780,
                "trust_score": -1.1,
            },
        ]

        reranked_candidates = build_market_candidates(report_dict, snapshot, None, profiles)
        reranked_core = next(
            candidate for candidate in reranked_candidates
            if candidate["family"] in {"1x2", "totals", "btts"}
        )
        best_btts = next(candidate for candidate in reranked_candidates if candidate["label"] == "BTTS Yes")

        self.assertEqual(reranked_core["label"], "Arsenal")
        self.assertEqual(best_btts["family_borderline_rerank_bonus"], 0.0)

    def test_apply_stake_recommendation_sets_standard_size_for_strong_signal(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.82,
            top_edge_pct=6.4,
        )
        report_dict = {
            "top_recommendation": "Arsenal Win",
            "top_confidence_pct": 72.0,
            "risk_level": "LOW",
            "data_warnings": [],
        }

        with patch("config.RECOMMENDATION_MODE", "balanced"):
            apply_stake_recommendation(report_dict, snapshot)

        self.assertEqual(report_dict["stake_units"], 1.0)
        self.assertEqual(report_dict["stake_label"], "Standard")
        self.assertIn("6.4", report_dict["stake_rationale"])
        self.assertEqual(report_dict["recommendation_mode"], "balanced")

    def test_apply_stake_recommendation_caps_action_mode_pick_to_minimal(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.61,
            top_edge_pct=1.4,
        )
        report_dict = {
            "top_recommendation": "Under 2.5",
            "top_confidence_pct": 56.0,
            "risk_level": "HIGH",
            "data_warnings": ["Action mode pick: thin edge accepted, use minimal stake only"],
        }

        apply_stake_recommendation(report_dict, snapshot)

        self.assertEqual(report_dict["stake_units"], 0.25)
        self.assertEqual(report_dict["stake_label"], "Minimal")
        self.assertIn("0.25u", report_dict["stake_rationale"])

    def test_apply_stake_recommendation_conservative_mode_trims_size(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.82,
            top_edge_pct=6.4,
        )
        report_dict = {
            "top_recommendation": "Arsenal Win",
            "top_confidence_pct": 72.0,
            "risk_level": "LOW",
            "data_warnings": [],
        }

        with patch("config.BANKROLL_MODE", "conservative"), patch("config.RECOMMENDATION_MODE", "balanced"):
            apply_stake_recommendation(report_dict, snapshot)

        self.assertEqual(report_dict["stake_units"], 0.75)
        self.assertEqual(report_dict["stake_label"], "Medium")
        self.assertIn("Conservative bankroll mode", report_dict["stake_rationale"])
        self.assertEqual(report_dict["recommendation_mode"], "balanced")

    def test_apply_stake_recommendation_aggressive_mode_can_expand_size(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.84,
            top_edge_pct=7.2,
        )
        report_dict = {
            "top_recommendation": "Arsenal Win",
            "top_confidence_pct": 74.0,
            "risk_level": "LOW",
            "data_warnings": [],
        }

        with patch("config.BANKROLL_MODE", "aggressive"), patch("config.RECOMMENDATION_MODE", "balanced"):
            apply_stake_recommendation(report_dict, snapshot)

        self.assertEqual(report_dict["stake_units"], 1.25)
        self.assertEqual(report_dict["stake_label"], "High")
        self.assertIn("Aggressive bankroll mode", report_dict["stake_rationale"])
        self.assertEqual(report_dict["recommendation_mode"], "balanced")

    def test_apply_stake_recommendation_persists_action_recommendation_mode(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.61,
            top_edge_pct=1.4,
        )
        report_dict = {
            "top_recommendation": "Under 2.5",
            "top_confidence_pct": 56.0,
            "risk_level": "HIGH",
            "data_warnings": ["Action mode pick: thin edge accepted, use minimal stake only"],
        }

        with patch("config.RECOMMENDATION_MODE", "action"):
            apply_stake_recommendation(report_dict, snapshot)

        self.assertEqual(report_dict["recommendation_mode"], "action")

    def test_build_value_candidate_snapshot_returns_structured_market_entries(self):
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.78,
            market_prob_w1=48.0,
            market_prob_draw=27.0,
            market_prob_w2=25.0,
            market_prob_over_2_5=49.0,
            market_prob_under_2_5=51.0,
            market_prob_btts_yes=47.0,
            market_prob_btts_no=53.0,
        )
        report_dict = {
            "prob_w1": 55.0,
            "prob_draw": 24.0,
            "prob_w2": 21.0,
            "prob_over_2_5": 57.0,
            "prob_under_2_5": 43.0,
            "prob_btts_yes": 46.0,
            "prob_btts_no": 54.0,
            "expected_goals": 2.7,
        }
        odds = OddsData(
            team1_win_odds=2.15,
            draw_odds=3.30,
            team2_win_odds=3.75,
            over_2_5_odds=2.04,
            under_2_5_odds=1.80,
            btts_yes_odds=1.98,
            btts_no_odds=1.84,
        )

        candidates = build_value_candidate_snapshot(report_dict, snapshot, odds)

        self.assertTrue(candidates)
        self.assertTrue(any(candidate.market_code == "1X2" for candidate in candidates))
        self.assertTrue(any(candidate.market_code == "TOTALS_2_5" for candidate in candidates))


if __name__ == "__main__":
    unittest.main()
