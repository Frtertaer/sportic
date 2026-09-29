from __future__ import annotations

import unittest

from formatter import (
    _truncate_telegram,
    format_analysing,
    format_bankroll,
    format_health,
    format_history,
    format_loading,
    format_report,
    format_stats,
)
from models import PredictionReport


def _base_report(**kwargs):
    defaults = dict(
        team1="Arsenal",
        team2="Chelsea",
        prob_w1=52.0,
        prob_draw=24.0,
        prob_w2=24.0,
        result_confidence="MEDIUM",
        prob_over_2_5=61.0,
        prob_under_2_5=39.0,
        expected_goals=2.8,
        goals_confidence="MEDIUM",
        asian_handicap_rec="Arsenal -0.5",
        asian_handicap_confidence="MEDIUM",
        prob_btts_yes=58.0,
        prob_btts_no=42.0,
        btts_confidence="MEDIUM",
        top_recommendation="Arsenal Win",
        top_confidence_pct=74.0,
        risk_level="LOW",
        analysis_summary="Arsenal in strong form.",
    )
    defaults.update(kwargs)
    return PredictionReport(**defaults)


class FormatterTests(unittest.TestCase):
    def test_report_uses_clean_utf8_labels(self) -> None:
        text = format_report(_base_report())
        self.assertIn("ПРОГНОЗ МАТЧА", text)
        self.assertIn("КЛЮЧЕВЫЕ ФАКТОРЫ", text)
        self.assertNotIn("РІР‚", text)
        self.assertNotIn("СЂСџ", text)

    def test_loading_mentions_current_sources(self) -> None:
        text = format_loading("Arsenal", "Chelsea")
        self.assertIn("Fotmob", text)
        self.assertIn("Transfermarkt", text)
        self.assertIn("Betexplorer", text)
        self.assertIn("Market signals", text)
        self.assertIn("Schedule/load", text)
        self.assertNotIn("Oddsportal", text)

    def test_analysing_shows_applicable_coverage_when_provider_gap_exists(self) -> None:
        text = format_analysing(
            "Tondela",
            "Rio Ave",
            33,
            applicable_completeness=100,
            unsupported_count=6,
        )
        self.assertIn("100%", text)
        self.assertIn("Raw completeness: 33%", text)
        self.assertIn("6", text)

    def test_market_ah_line_shown_when_present(self):
        report = _base_report(market_ah_line="Arsenal -0.5", market_ah_odds=1.87)
        text = format_report(report)
        self.assertIn("Arsenal -0.5", text)
        self.assertIn("1.87", text)

    def test_market_ah_line_hidden_when_absent(self):
        report = _base_report()
        text = format_report(report)
        self.assertNotIn("1.87", text)

    def test_value_bets_section_shown_when_present(self):
        report = _base_report(
            value_bets=["Arsenal Win: 55% vs 42% buk. (+13% edge)"]
        )
        text = format_report(report)
        self.assertIn("Arsenal Win: 55% vs 42% buk.", text)
        self.assertIn("+13%", text)

    def test_value_bets_section_hidden_when_empty(self):
        report = _base_report()
        text = format_report(report)
        self.assertNotIn("+13% edge", text)

    def test_dynamic_fields_are_html_escaped(self):
        report = _base_report(
            analysis_summary="Edge < 10 & rising",
            top_recommendation="Arsenal <Win>",
            value_bets=["Under 2.5 < 1.90"],
        )
        text = format_report(report)
        self.assertIn("Edge &lt; 10 &amp; rising", text)
        self.assertIn("Arsenal &lt;Win&gt;", text)
        self.assertIn("Under 2.5 &lt; 1.90", text)

    def test_truncate_telegram_keeps_html_balanced(self):
        text = _truncate_telegram("<b>" + ("x" * 5000) + "</b>")

        self.assertLessEqual(len(text), 4090)
        self.assertEqual(text.count("<b>"), 1)
        self.assertEqual(text.count("</b>"), 1)
        self.assertTrue(text.endswith("</b>"))

    def test_top_pick_context_and_extra_markets_are_rendered(self):
        report = _base_report(
            top_pick_context="Chosen from expanded market set.",
            stake_units=0.5,
            stake_label="Small",
            stake_rationale="Playable edge, but keep the size contained.",
            prob_over_1_5=78.0,
            prob_under_1_5=22.0,
            prob_over_3_5=26.0,
            prob_under_3_5=74.0,
            prob_dnb_team1=68.0,
            prob_dnb_team2=32.0,
            prob_double_chance_1x=76.0,
            prob_double_chance_12=74.0,
            prob_double_chance_x2=56.0,
        )
        text = format_report(report)
        self.assertIn("Chosen from expanded market set.", text)
        self.assertIn("0.50u", text)
        self.assertIn("Playable edge, but keep the size contained.", text)
        self.assertIn("ДОП. РЫНКИ", text)
        self.assertIn("O/U 1.5", text)
        self.assertIn("1X / 12 / X2", text)

    def test_format_history_shows_confidence_risk_and_versions(self):
        text = format_history([
            {
                "team1": "Arsenal",
                "team2": "Chelsea",
                "predicted_at": "2026-03-09T12:00:00+00:00",
                "top_rec": "Arsenal Win",
                "top_confidence_pct": 74.0,
                "risk_level": "LOW",
                "stake_units": 0.75,
                "stake_label": "Medium",
                "methodology_version": "method-v1",
                "prompt_version": "prompt-v2",
                "actual_result": "W1",
                "actual_score": "2-1",
                "completeness": 0.8,
            }
        ])
        self.assertIn("Arsenal Win", text)
        self.assertIn("confidence 74%", text)
        self.assertIn("risk LOW", text)
        self.assertIn("stake 0.75u Medium", text)
        self.assertIn("method method-v1", text)
        self.assertIn("prompt prompt-v2", text)

    def test_format_history_supports_custom_title_and_footer(self):
        text = format_history(
            [{"team1": "Arsenal", "team2": "Chelsea", "predicted_at": "2026-03-09"}],
            title="Custom title",
            footer="Custom footer",
        )
        self.assertIn("Custom title", text)
        self.assertIn("Custom footer", text)

    def test_format_stats_shows_paper_bankroll_summary(self):
        text = format_stats(
            {"n_with_result": 12, "acc_1x2": 58.3, "brier_1x2": 0.2142},
            20,
            {
                "starting_bankroll_units": 100.0,
                "current_bankroll_units": 104.5,
                "profit_units": 4.5,
                "roi_pct": 8.18,
                "tracked_bets": 14,
                "wins": 7,
                "losses": 5,
                "pushes": 2,
                "hit_rate": 58.33,
                "avg_stake_units": 0.62,
                "max_drawdown_units": 2.75,
            },
        )
        self.assertIn("Paper bankroll", text)
        self.assertIn("100.00u → 104.50u", text)
        self.assertIn("+4.50u", text)
        self.assertIn("+8.18%", text)
        self.assertIn("W 7 / L 5 / P 2", text)
        self.assertIn("2.75u", text)

    def test_format_health_shows_runtime_summary(self):
        text = format_health(
            {
                "backend": "Anthropic",
                "agent_chain_enabled": True,
                "source_count": 9,
                "prediction_count": 42,
                "unresolved_count": 3,
                "active_lessons_count": 7,
                "unsynced_lessons_count": 2,
                "scrape_metrics": {
                    "fotmob_form": {
                        "runs": 5,
                        "failures": 1,
                        "last_error_message": "timeout",
                    }
                },
                "analysis_metrics": {
                    "total_calls": 12,
                    "pre_ai_gate_skips": 3,
                    "live_hybrid_1x2_applied": 4,
                    "agent_chain_runs": 5,
                    "single_call_runs": 4,
                    "fallback_reports": 1,
                    "anthropic_requests": 9,
                    "openclaw_requests": 0,
                    "openclaw_fallbacks": 0,
                    "policy_flag_counts": {
                        "path_agent_chain": 5,
                        "top_market_core": 4,
                        "no_bet": 3,
                    },
                },
                "cache_stats": {"entries": 4, "inflight": 1, "ttl_seconds": 1800},
                "vector_stats": {"matches": 10, "lessons": 5, "team_patterns": 2},
                "debug_snapshots_enabled": True,
                "debug_snapshots_dir": "C:/tmp/debug",
            }
        )
        self.assertIn("Anthropic", text)
        self.assertIn("42", text)
        self.assertIn("timeout", text)
        self.assertIn("pre-AI skips", text)
        self.assertIn("1X2 hybrid", text)
        self.assertIn("Anthropic <b>9</b>", text)
        self.assertIn("Policy flags:", text)
        self.assertIn("agent chain <b>5</b>", text)
        self.assertIn("C:/tmp/debug", text)

    def test_format_stats_shows_segment_breakdowns(self):
        text = format_stats(
            {"n_with_result": 12, "acc_1x2": 58.3, "brier_1x2": 0.2142},
            20,
            {
                "starting_bankroll_units": 100.0,
                "current_bankroll_units": 104.5,
                "profit_units": 4.5,
                "roi_pct": 8.18,
                "tracked_bets": 14,
                "wins": 7,
                "losses": 5,
                "pushes": 2,
                "hit_rate": 58.33,
                "avg_stake_units": 0.62,
                "max_drawdown_units": 2.75,
                "by_market": [
                    {"segment": "1X2", "tracked_bets": 8, "profit_units": 3.25, "roi_pct": 12.5, "sample_gate": "developing"},
                    {"segment": "BTTS", "tracked_bets": 4, "profit_units": -0.5, "roi_pct": -6.2, "sample_gate": "thin"},
                ],
                "by_competition": [
                    {"segment": "Premier League", "tracked_bets": 6, "profit_units": 2.1, "roi_pct": 9.8, "sample_gate": "developing"},
                ],
                "by_competition_policy": [
                    {"segment": "Premier League / path_agent_chain", "tracked_bets": 4, "profit_units": 1.4, "roi_pct": 11.1, "sample_gate": "thin"},
                ],
                "by_risk": [
                    {"segment": "LOW", "tracked_bets": 5, "profit_units": 1.8, "roi_pct": 11.2, "sample_gate": "developing"},
                ],
                "by_bankroll_mode": [
                    {"segment": "balanced", "tracked_bets": 9, "profit_units": 2.9, "roi_pct": 8.7, "sample_gate": "developing"},
                ],
                "by_recommendation_mode": [
                    {"segment": "action", "tracked_bets": 3, "profit_units": 0.9, "roi_pct": 6.1, "sample_gate": "thin"},
                ],
                "by_policy_flag": [
                    {"segment": "path_agent_chain", "tracked_bets": 7, "profit_units": 1.7, "roi_pct": 9.3, "sample_gate": "developing"},
                ],
                "recent_form_20": {
                    "window_size": 20,
                    "tracked_bets": 6,
                    "wins": 4,
                    "losses": 2,
                    "pushes": 0,
                    "profit_units": 1.6,
                    "roi_pct": 9.4,
                    "streak_label": "W2",
                },
            },
        )
        self.assertIn("Best by market", text)
        self.assertIn("1X2", text)
        self.assertIn("Premier League", text)
        self.assertIn("Best by competition + policy", text)
        self.assertIn("Premier League / path_agent_chain", text)
        self.assertIn("Best by risk", text)
        self.assertIn("LOW", text)
        self.assertIn("Best by bankroll mode", text)
        self.assertIn("balanced", text)
        self.assertIn("Best by recommendation mode", text)
        self.assertIn("action", text)
        self.assertIn("Best by policy flag", text)
        self.assertIn("path_agent_chain", text)
        self.assertIn("[developing]", text)
        self.assertIn("[thin]", text)

    def test_format_bankroll_shows_dedicated_bankroll_view(self):
        text = format_bankroll(
            {
                "starting_bankroll_units": 100.0,
                "current_bankroll_units": 103.2,
                "profit_units": 3.2,
                "roi_pct": 8.0,
                "tracked_bets": 10,
                "wins": 5,
                "losses": 4,
                "pushes": 1,
                "hit_rate": 55.56,
                "avg_stake_units": 0.4,
                "max_drawdown_units": 1.9,
                "by_market": [{"segment": "1X2", "tracked_bets": 6, "profit_units": 2.1, "roi_pct": 10.5, "sample_gate": "developing"}],
                "by_competition": [{"segment": "Premier League", "tracked_bets": 4, "profit_units": 1.0, "roi_pct": 7.5, "sample_gate": "thin"}],
                "by_competition_policy": [{"segment": "Premier League / path_agent_chain", "tracked_bets": 3, "profit_units": 0.8, "roi_pct": 9.1, "sample_gate": "thin"}],
                "by_risk": [{"segment": "LOW", "tracked_bets": 3, "profit_units": 1.4, "roi_pct": 11.0, "sample_gate": "thin"}],
                "by_bankroll_mode": [{"segment": "balanced", "tracked_bets": 10, "profit_units": 3.2, "roi_pct": 8.0, "sample_gate": "developing"}],
                "by_recommendation_mode": [{"segment": "action", "tracked_bets": 3, "profit_units": 0.7, "roi_pct": 5.8, "sample_gate": "thin"}],
                "by_policy_flag": [{"segment": "path_agent_chain", "tracked_bets": 5, "profit_units": 1.1, "roi_pct": 8.4, "sample_gate": "developing"}],
                "recent_form_20": {
                    "window_size": 20,
                    "tracked_bets": 10,
                    "wins": 5,
                    "losses": 4,
                    "pushes": 1,
                    "profit_units": 0.9,
                    "roi_pct": 4.5,
                    "streak_label": "L1",
                },
            }
        )
        self.assertIn("PAPER BANKROLL", text)
        self.assertIn("103.20u", text)
        self.assertIn("Recent form", text)
        self.assertIn("Best by market", text)
        self.assertIn("Premier League", text)
        self.assertIn("Best by competition + policy", text)
        self.assertIn("Recent form", text)
        self.assertIn("L1", text)
        self.assertIn("Best by policy flag", text)
        self.assertIn("path_agent_chain", text)
        self.assertIn("[developing]", text)
        self.assertIn("[thin]", text)


    def test_format_stats_shows_paper_bankroll_summary(self):
        text = format_stats(
            {"n_with_result": 12, "acc_1x2": 58.3, "brier_1x2": 0.2142},
            20,
            {
                "starting_bankroll_units": 100.0,
                "current_bankroll_units": 104.5,
                "profit_units": 4.5,
                "roi_pct": 8.18,
                "tracked_bets": 14,
                "wins": 7,
                "losses": 5,
                "pushes": 2,
                "hit_rate": 58.33,
                "avg_stake_units": 0.62,
                "max_drawdown_units": 2.75,
            },
        )
        self.assertIn("Paper bankroll", text)
        self.assertIn("100.00u", text)
        self.assertIn("104.50u", text)
        self.assertIn("+4.50u", text)
        self.assertIn("+8.18%", text)
        self.assertIn("W 7 / L 5 / P 2", text)
        self.assertIn("2.75u", text)

    def test_format_stats_shows_calibration_curve(self):
        cal_curve = {
            "ece": 5.3,
            "n_total": 20,
            "bins": [
                {"range_lo": 0.0, "range_hi": 0.1, "n": 0, "avg_confidence": 0.0, "accuracy": 0.0, "gap": 0.0},
                {"range_lo": 0.4, "range_hi": 0.5, "n": 5, "avg_confidence": 45.0, "accuracy": 40.0, "gap": 5.0},
                {"range_lo": 0.6, "range_hi": 0.7, "n": 8, "avg_confidence": 65.0, "accuracy": 62.5, "gap": 2.5},
                {"range_lo": 0.8, "range_hi": 0.9, "n": 7, "avg_confidence": 85.0, "accuracy": 71.4, "gap": 13.6},
            ],
        }
        text = format_stats(
            {"n_with_result": 20, "acc_1x2": 60.0, "brier_1x2": 0.2000},
            25,
            calibration_curve=cal_curve,
        )
        self.assertIn("ECE", text)
        self.assertIn("5.3%", text)
        self.assertIn("Калибровочная кривая", text)
        self.assertIn("n=20", text)
        # Bins with data are rendered
        self.assertIn("40-50", text)
        self.assertIn("60-70", text)
        # Empty bins are skipped
        self.assertNotIn("0-10", text)

    def test_format_stats_no_calibration_curve(self):
        text = format_stats(
            {"n_with_result": 3, "acc_1x2": 66.7, "brier_1x2": 0.2500},
            5,
            calibration_curve=None,
        )
        self.assertNotIn("ECE", text)
        self.assertNotIn("Калибровочная кривая", text)


class ResultNotificationTest(unittest.TestCase):
    def test_correct_prediction_notification(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "2-1",
            "top_rec": "Arsenal Win",
            "actual_result": "W1",
        }
        text = format_result_notification(resolved)
        self.assertIn("Arsenal", text)
        self.assertIn("2-1", text)
        self.assertIn("Arsenal Win", text)
        self.assertIn("/stats", text)

    def test_wrong_prediction_notification(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "0-2",
            "top_rec": "Arsenal Win",
            "actual_result": "W2",
        }
        text = format_result_notification(resolved)
        self.assertIn("Chelsea", text)
        self.assertIn("0-2", text)
        self.assertIn("Arsenal Win", text)

    def test_w1_win_for_wrong_team_is_marked_incorrect(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Liverpool",
            "score": "2-0",
            "top_rec": "Liverpool Win",
            "actual_result": "W1",
        }
        text = format_result_notification(resolved)
        self.assertIn("Liverpool Win", text)
        self.assertNotIn("Arsenal Win", text)

    def test_total_market_notification_is_evaluated_by_score(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "2-1",
            "top_rec": "Over 2.5",
            "actual_result": "W1",
        }
        text = format_result_notification(resolved)
        self.assertIn("Over 2.5", text)

    def test_asian_handicap_push_notification_is_neutral(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "1-0",
            "top_rec": "Arsenal -1.0",
            "actual_result": "W1",
        }
        text = format_result_notification(resolved)
        self.assertIn("Arsenal -1.0", text)

    def test_draw_no_bet_notification_marks_draw_as_push(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "1-1",
            "top_rec": "Arsenal Draw No Bet",
            "actual_result": "DRAW",
        }
        text = format_result_notification(resolved)
        self.assertIn("Возврат", text)

    def test_double_chance_notification_is_evaluated(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "1-1",
            "top_rec": "Arsenal or Draw (1X)",
            "actual_result": "DRAW",
        }
        text = format_result_notification(resolved)
        self.assertIn("ВЕРНО", text)

    def test_alt_total_notification_is_evaluated(self):
        from formatter import format_result_notification

        resolved = {
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "2-0",
            "top_rec": "Under 3.5",
            "actual_result": "W1",
        }
        text = format_result_notification(resolved)
        self.assertIn("ВЕРНО", text)


class ScheduleFormatterTest(unittest.TestCase):
    def test_format_schedule_empty(self):
        from formatter import format_schedule

        text = format_schedule([])
        self.assertIn("<i>", text)
        self.assertNotIn("/predict", text)

    def test_format_schedule_shows_teams(self):
        from formatter import format_schedule
        from scrapers.fotmob import LeagueSchedule, ScheduledMatch

        leagues = [
            LeagueSchedule(
                league_name="Premier League",
                country_flag="🏴",
                matches=[
                    ScheduledMatch(time_utc="15:00", home="Arsenal", away="Chelsea"),
                    ScheduledMatch(time_utc="17:30", home="Man City", away="Liverpool"),
                ],
            )
        ]
        text = format_schedule(leagues)
        self.assertIn("Premier League", text)
        self.assertIn("Arsenal", text)
        self.assertIn("Chelsea", text)
        self.assertIn("15:00", text)
        self.assertIn("/predict", text)


if __name__ == "__main__":
    unittest.main()
