from __future__ import annotations
import unittest
from models import (
    FlashscoreData,
    MatchData,
    PredictionFeatureSnapshot,
    PredictionReport,
    SourceStatus,
)


class ModelFieldTests(unittest.TestCase):
    def test_flashscore_has_league_name(self):
        fs = FlashscoreData(team1_name="Arsenal", team2_name="Chelsea")
        self.assertIsNone(fs.league_name)

    def test_flashscore_league_name_set(self):
        fs = FlashscoreData(
            team1_name="Arsenal", team2_name="Chelsea",
            league_name="Premier League"
        )
        self.assertEqual(fs.league_name, "Premier League")

    def _make_report(self, **kwargs) -> PredictionReport:
        defaults = dict(
            team1="A", team2="B",
            prob_w1=50.0, prob_draw=25.0, prob_w2=25.0, result_confidence="MEDIUM",
            prob_over_2_5=55.0, prob_under_2_5=45.0, expected_goals=2.5,
            goals_confidence="MEDIUM", asian_handicap_rec="A -0.5",
            asian_handicap_confidence="MEDIUM", prob_btts_yes=50.0,
            prob_btts_no=50.0, btts_confidence="MEDIUM",
            top_recommendation="A Win", top_confidence_pct=55.0,
            risk_level="MEDIUM", analysis_summary="Test",
        )
        defaults.update(kwargs)
        return PredictionReport(**defaults)

    def test_prediction_report_has_value_bets(self):
        r = self._make_report()
        self.assertEqual(r.value_bets, [])
        self.assertIsNone(r.stake_units)
        self.assertIsNone(r.stake_label)
        self.assertIsNone(r.stake_rationale)

    def test_prediction_report_value_bets_set(self):
        r = self._make_report(value_bets=["Arsenal Win: 55% vs 42% бук. (+13% edge)"])
        self.assertEqual(len(r.value_bets), 1)

    def test_prediction_report_has_market_ah_fields(self):
        r = self._make_report()
        self.assertIsNone(r.market_ah_line)
        self.assertIsNone(r.market_ah_odds)

    def test_prediction_report_market_ah_set(self):
        r = self._make_report(market_ah_line="Arsenal -0.5", market_ah_odds=1.87)
        self.assertEqual(r.market_ah_line, "Arsenal -0.5")
        self.assertAlmostEqual(r.market_ah_odds, 1.87)

    def test_prediction_report_can_embed_feature_snapshot(self):
        snapshot = PredictionFeatureSnapshot(
            team1="A",
            team2="B",
            data_completeness=0.75,
            model_prob_w1=52.0,
            model_prob_draw=26.0,
            model_prob_w2=22.0,
        )
        r = self._make_report(feature_snapshot=snapshot)

        self.assertIsNotNone(r.feature_snapshot)
        self.assertAlmostEqual(r.feature_snapshot.model_prob_w1 or 0.0, 52.0)

    def test_match_data_prefers_source_statuses_for_completeness(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            source_statuses=[
                SourceStatus(
                    key="fotmob_form",
                    label="Form/H2H",
                    field_name="flashscore",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="fotmob_squad",
                    label="Squads",
                    field_name="sofascore",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="transfermarkt",
                    label="Values",
                    field_name="transfermarkt",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="betexplorer",
                    label="Odds",
                    field_name="odds",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="match_context",
                    label="Match context",
                    field_name="match_context",
                    scrape_success=False,
                    error_message="Fixture not found",
                ),
            ],
        )

        self.assertEqual(data.successful_source_count, 4)
        self.assertEqual(data.total_source_count, 5)
        self.assertAlmostEqual(data.data_completeness, 0.8)
        self.assertEqual(data.failed_sources, ["Match context"])
        self.assertEqual(data.failed_source_messages, ["Match context: Fixture not found"])

    def test_source_status_supports_runtime_metadata(self):
        status = SourceStatus(
            key="betexplorer",
            label="Odds",
            field_name="odds",
            scrape_success=False,
            error_message="network timeout",
            duration_ms=1530,
            exception_type="TimeoutError",
        )

        self.assertEqual(status.duration_ms, 1530)
        self.assertEqual(status.exception_type, "TimeoutError")

    def test_provider_coverage_gap_does_not_penalize_completeness(self):
        data = MatchData(
            team1="Tondela",
            team2="Rio Ave",
            source_statuses=[
                SourceStatus(
                    key="transfermarkt",
                    label="Values",
                    field_name="transfermarkt",
                    scrape_success=True,
                    provider="Transfermarkt",
                ),
                SourceStatus(
                    key="betexplorer",
                    label="Odds",
                    field_name="odds",
                    scrape_success=True,
                    provider="Betexplorer",
                ),
                SourceStatus(
                    key="market_signals",
                    label="Market signals",
                    field_name="market_signals",
                    scrape_success=True,
                    provider="Betexplorer",
                ),
                SourceStatus(
                    key="fotmob_form",
                    label="Form/H2H",
                    field_name="flashscore",
                    scrape_success=False,
                    error_message="Teams not found on Fotmob: Tondela, Rio Ave",
                    provider="Fotmob",
                    applicable=False,
                    coverage_gap=True,
                ),
                SourceStatus(
                    key="match_context",
                    label="Match context",
                    field_name="match_context",
                    scrape_success=False,
                    error_message="Teams not found on Fotmob: Tondela, Rio Ave",
                    provider="Fotmob",
                    applicable=False,
                    coverage_gap=True,
                ),
            ],
        )

        self.assertEqual(data.successful_source_count, 3)
        self.assertEqual(data.total_source_count, 5)
        self.assertEqual(data.successful_applicable_source_count, 3)
        self.assertEqual(data.applicable_source_count, 3)
        self.assertEqual(data.unsupported_source_count, 2)
        self.assertEqual(data.unsupported_sources, ["Form/H2H", "Match context"])
        self.assertAlmostEqual(data.data_completeness, 0.6)
        self.assertAlmostEqual(data.applicable_data_completeness, 1.0)
        self.assertEqual(data.failed_sources, [])
        self.assertEqual(
            data.unsupported_source_messages,
            ["Fotmob coverage unavailable for this match: Form/H2H, Match context"],
        )


if __name__ == "__main__":
    unittest.main()
