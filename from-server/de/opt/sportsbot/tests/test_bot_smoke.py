from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import bot
from telegram.error import TelegramError
from models import MatchData, PredictionReport, SourceStatus


def _fake_report() -> PredictionReport:
    return PredictionReport(
        team1="Arsenal",
        team2="Chelsea",
        prob_w1=58.0,
        prob_draw=24.0,
        prob_w2=18.0,
        result_confidence="MEDIUM",
        prob_over_2_5=55.0,
        prob_under_2_5=45.0,
        expected_goals=2.7,
        goals_confidence="MEDIUM",
        asian_handicap_rec="Arsenal -0.5",
        asian_handicap_confidence="MEDIUM",
        prob_btts_yes=53.0,
        prob_btts_no=47.0,
        btts_confidence="MEDIUM",
        team1_strengths=["Home form"],
        team1_weaknesses=["Set pieces"],
        team2_strengths=["Transition play"],
        team2_weaknesses=["Away defending"],
        top_recommendation="Arsenal win",
        top_recommendation_odds=1.8,
        top_confidence_pct=61.0,
        risk_level="MEDIUM",
        analysis_summary="Arsenal have the better setup.",
        data_warnings=[],
        value_bets=[],
        methodology_version="test-methodology",
        prompt_version="test-prompt",
    )


class _FakeLoadingMessage:
    def __init__(self) -> None:
        self.edit_text = AsyncMock()


class _FakeMessage:
    def __init__(self) -> None:
        self.loading_message = _FakeLoadingMessage()
        self.reply_html = AsyncMock(return_value=self.loading_message)
        self.reply_text = AsyncMock()


class BotSmokeTests(unittest.IsolatedAsyncioTestCase):
    def _make_update(self, user_id: int = 101) -> SimpleNamespace:
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            message=_FakeMessage(),
        )

    def _make_context(self, *args: str) -> SimpleNamespace:
        return SimpleNamespace(
            args=list(args),
            bot=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_start_handler_mentions_dynamic_registry_and_debug_command(self):
        update = self._make_update()

        await bot.start_handler(update, self._make_context())

        text = update.message.reply_html.await_args.args[0]
        self.assertIn(str(len(bot.SCRAPER_REGISTRY)), text)
        self.assertIn("/bankroll", text)
        self.assertIn("/debugsources", text)
        self.assertIn("/health", text)
        self.assertIn("/historymatch", text)

    async def test_debugsources_handler_renders_metrics_snapshot(self):
        update = self._make_update()
        snapshot = {
            "fotmob_form": {
                "label": "Form/H2H",
                "runs": 3,
                "successes": 2,
                "failures": 1,
                "avg_duration_ms": 123.4,
                "max_duration_ms": 210,
                "last_duration_ms": 150,
                "last_error_message": "timeout",
                "last_exception_type": "TimeoutError",
            }
        }

        with patch("bot.get_scrape_metrics_snapshot", return_value=snapshot):
            await bot.debugsources_handler(update, self._make_context())

        text = update.message.reply_html.await_args.args[0]
        self.assertIn("Form/H2H", text)
        self.assertIn("timeout", text)

    async def test_health_handler_renders_operational_summary(self):
        update = self._make_update()

        with (
            patch("bot.db.get_prediction_count", new=AsyncMock(return_value=12)),
            patch("bot.db.get_unresolved_prediction_count", new=AsyncMock(return_value=2)),
            patch("bot.db.get_active_lessons_count", new=AsyncMock(return_value=5)),
            patch("bot.db.get_unsynced_lessons_count", new=AsyncMock(return_value=1)),
            patch("bot.get_scrape_metrics_snapshot", return_value={}),
            patch("bot.match_cache.get_stats", new=AsyncMock(return_value={"entries": 3, "inflight": 0, "ttl_seconds": 1800})),
            patch("memory.get_shared_vector_memory", new=AsyncMock()) as get_vm_mock,
        ):
            fake_vm = AsyncMock()
            fake_vm.get_stats = AsyncMock(return_value={"matches": 4, "lessons": 2, "team_patterns": 1})
            get_vm_mock.return_value = fake_vm
            await bot.health_handler(update, self._make_context())

        text = update.message.reply_html.await_args.args[0]
        self.assertIn("Health", text)
        self.assertIn("12", text)
        self.assertIn("Vector memory", text)

    async def test_history_handler_accepts_custom_limit(self):
        update = self._make_update()

        with patch("bot.db.get_prediction_history", new=AsyncMock(return_value=[])) as history_mock:
            await bot.history_handler(update, self._make_context("20"))

        history_mock.assert_awaited_once_with(limit=20)
        text = update.message.reply_html.await_args.args[0]
        self.assertIn("История пуста", text)

    async def test_historymatch_handler_filters_specific_pair(self):
        update = self._make_update()
        rows = [
            {
                "team1": "Arsenal",
                "team2": "Chelsea",
                "predicted_at": "2026-03-09T12:00:00+00:00",
                "top_rec": "Arsenal Win",
                "top_confidence_pct": 60.0,
                "risk_level": "MEDIUM",
                "methodology_version": "m1",
                "prompt_version": "p1",
                "actual_result": None,
                "actual_score": None,
                "completeness": 0.8,
            }
        ]

        with (
            patch("bot.parse_teams_from_input", return_value=("Arsenal", "Chelsea")),
            patch("bot.db.get_prediction_history_for_match", new=AsyncMock(return_value=rows)) as history_mock,
        ):
            await bot.historymatch_handler(update, self._make_context("5", "Arsenal", "vs", "Chelsea"))

        history_mock.assert_awaited_once_with("Arsenal", "Chelsea", limit=5)
        text = update.message.reply_html.await_args.args[0]
        self.assertIn("История: Arsenal vs Chelsea", text)
        self.assertIn("Arsenal Win", text)

    async def test_stats_handler_includes_paper_bankroll_summary(self):
        update = self._make_update()

        with (
            patch("bot.db.get_calibration_stats", new=AsyncMock(return_value={"n_with_result": 3, "acc_1x2": 66.7, "brier_1x2": 0.2142})),
            patch("bot.db.get_prediction_count", new=AsyncMock(return_value=11)),
            patch(
                "bot.db.get_paper_bankroll_stats",
                new=AsyncMock(
                    return_value={
                        "starting_bankroll_units": 100.0,
                        "current_bankroll_units": 101.2,
                        "profit_units": 1.2,
                        "roi_pct": 10.0,
                        "tracked_bets": 4,
                        "wins": 2,
                        "losses": 1,
                        "pushes": 1,
                        "hit_rate": 66.7,
                        "avg_stake_units": 0.5,
                        "max_drawdown_units": 0.8,
                        "by_market": [
                            {"segment": "1X2", "tracked_bets": 3, "profit_units": 1.5, "roi_pct": 20.0},
                        ],
                        "by_competition": [
                            {"segment": "Premier League", "tracked_bets": 2, "profit_units": 0.9, "roi_pct": 15.0},
                        ],
                        "by_competition_policy": [
                            {"segment": "Premier League / path_agent_chain", "tracked_bets": 2, "profit_units": 0.7, "roi_pct": 11.0},
                        ],
                        "by_risk": [
                            {"segment": "LOW", "tracked_bets": 2, "profit_units": 1.1, "roi_pct": 18.0},
                        ],
                        "by_bankroll_mode": [
                            {"segment": "balanced", "tracked_bets": 4, "profit_units": 1.2, "roi_pct": 10.0},
                        ],
                        "by_recommendation_mode": [
                            {"segment": "action", "tracked_bets": 2, "profit_units": 0.4, "roi_pct": 5.0},
                        ],
                        "recent_form_20": {
                            "window_size": 20,
                            "tracked_bets": 4,
                            "wins": 2,
                            "losses": 1,
                            "pushes": 1,
                            "profit_units": 0.7,
                            "roi_pct": 7.0,
                            "streak_label": "W1",
                        },
                    }
                ),
            ),
        ):
            await bot.stats_handler(update, self._make_context())

        text = update.message.reply_html.await_args.args[0]
        self.assertIn("66.7", text)
        self.assertIn("Paper bankroll", text)
        self.assertIn("101.20u", text)
        self.assertIn("Best by market", text)
        self.assertIn("Best by competition + policy", text)
        self.assertIn("Premier League", text)
        self.assertIn("Best by risk", text)
        self.assertIn("Best by bankroll mode", text)
        self.assertIn("Best by recommendation mode", text)
        self.assertIn("Recent form", text)

    async def test_bankroll_handler_renders_paper_bankroll_view(self):
        update = self._make_update()

        with patch(
            "bot.db.get_paper_bankroll_stats",
            new=AsyncMock(
                return_value={
                    "starting_bankroll_units": 100.0,
                    "current_bankroll_units": 102.4,
                    "profit_units": 2.4,
                    "roi_pct": 9.6,
                    "tracked_bets": 6,
                    "wins": 3,
                    "losses": 2,
                    "pushes": 1,
                    "hit_rate": 60.0,
                    "avg_stake_units": 0.5,
                    "max_drawdown_units": 1.1,
                    "by_market": [{"segment": "1X2", "tracked_bets": 4, "profit_units": 1.8, "roi_pct": 12.0}],
                    "by_competition": [{"segment": "Premier League", "tracked_bets": 3, "profit_units": 1.2, "roi_pct": 10.0}],
                    "by_risk": [{"segment": "LOW", "tracked_bets": 2, "profit_units": 1.0, "roi_pct": 14.0}],
                    "by_bankroll_mode": [{"segment": "balanced", "tracked_bets": 6, "profit_units": 2.4, "roi_pct": 9.6}],
                    "by_recommendation_mode": [{"segment": "action", "tracked_bets": 2, "profit_units": 0.5, "roi_pct": 5.0}],
                    "recent_form_20": {
                        "window_size": 20,
                        "tracked_bets": 6,
                        "wins": 3,
                        "losses": 2,
                        "pushes": 1,
                        "profit_units": 1.1,
                        "roi_pct": 8.5,
                        "streak_label": "W1",
                    },
                }
            ),
        ):
            await bot.bankroll_handler(update, self._make_context())

        text = update.message.reply_html.await_args.args[0]
        self.assertIn("PAPER BANKROLL", text)
        self.assertIn("102.40u", text)
        self.assertIn("Recent form", text)

    async def test_predict_handler_smoke_persists_prediction(self):
        update = self._make_update()
        context = self._make_context("Arsenal", "vs", "Chelsea")
        report = _fake_report()
        match_data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            source_statuses=[
                SourceStatus(
                    key="fotmob_form",
                    label="Form/H2H",
                    field_name="flashscore",
                    scrape_success=True,
                    duration_ms=120,
                )
            ],
        )

        with (
            patch("bot.parse_teams_from_input", return_value=("Arsenal", "Chelsea")),
            patch("bot.db.get_last_request", new=AsyncMock(return_value=None)),
            patch("bot.db.set_last_request", new=AsyncMock()),
            patch("bot.match_cache.claim", new=AsyncMock(return_value=("leader", None))),
            patch("bot.scrape_all_parallel", new=AsyncMock(return_value=match_data)),
            patch("bot.analyze_match", new=AsyncMock(return_value=report)),
            patch("bot.match_cache.resolve", new=AsyncMock()),
            patch("bot.db.save_prediction", new=AsyncMock(return_value=77)) as save_prediction_mock,
            patch("bot.store_prediction_memory", new=AsyncMock()) as store_prediction_memory_mock,
        ):
            await bot.predict_handler(update, context)

        self.assertGreaterEqual(update.message.loading_message.edit_text.await_count, 2)
        save_prediction_mock.assert_awaited_once()
        store_prediction_memory_mock.assert_awaited_once()

    async def test_predict_handler_falls_back_to_reply_when_final_edit_fails(self):
        update = self._make_update()
        context = self._make_context("Arsenal", "vs", "Chelsea")
        report = _fake_report()
        match_data = MatchData(team1="Arsenal", team2="Chelsea", source_statuses=[])
        update.message.loading_message.edit_text = AsyncMock(
            side_effect=[None, TelegramError("can't parse entities")]
        )

        with (
            patch("bot.parse_teams_from_input", return_value=("Arsenal", "Chelsea")),
            patch("bot.db.get_last_request", new=AsyncMock(return_value=None)),
            patch("bot.db.set_last_request", new=AsyncMock()),
            patch("bot.match_cache.claim", new=AsyncMock(return_value=("compute", None))),
            patch("bot.scrape_all_parallel", new=AsyncMock(return_value=match_data)),
            patch("bot.analyze_match", new=AsyncMock(return_value=report)),
            patch("bot.match_cache.resolve", new=AsyncMock()),
            patch("bot.db.save_prediction", new=AsyncMock(return_value=77)),
            patch("bot.sync_prediction_to_historical", new=AsyncMock()),
            patch("bot.store_prediction_memory", new=AsyncMock()),
        ):
            await bot.predict_handler(update, context)

        self.assertEqual(update.message.reply_html.await_count, 2)
        self.assertIn("Arsenal", update.message.reply_html.await_args_list[-1].args[0])

    async def test_result_handler_smoke_updates_stats_message(self):
        update = self._make_update()
        context = self._make_context("Arsenal", "2-1", "Chelsea")

        with (
            patch("bot.db.find_latest_unresolved_prediction", new=AsyncMock(return_value={"id": 11})),
            patch(
                "bot.process_prediction_result",
                new=AsyncMock(
                    return_value={
                        "team1": "Arsenal",
                        "team2": "Chelsea",
                        "actual_score": "2-1",
                        "actual_result": "W1",
                    }
                ),
            ),
            patch(
                "bot.db.get_calibration_stats",
                new=AsyncMock(return_value={"n_with_result": 3, "acc_1x2": 66.7}),
            ),
        ):
            await bot.result_handler(update, context)

        text = update.message.reply_html.await_args.args[0]
        self.assertIn("2-1", text)
        self.assertIn("66.7", text)

    async def test_check_results_job_smoke_sends_notification_and_marks_sent(self):
        context = self._make_context()
        resolved = {
            "prediction_id": 44,
            "user_id": 9001,
            "team1": "Arsenal",
            "team2": "Chelsea",
            "score": "2-1",
            "actual_result": "W1",
            "top_rec": "Arsenal win",
            "acc_line": "Точность: 5 матчей, 60.0% (1X2)",
        }

        with (
            patch("bot.check_pending_results", new=AsyncMock(return_value=[resolved])),
            patch("bot.db.mark_notification_sent", new=AsyncMock()) as mark_notification_sent_mock,
        ):
            await bot.check_results_job(context)

        context.bot.send_message.assert_awaited_once()
        mark_notification_sent_mock.assert_awaited_once_with(44)
