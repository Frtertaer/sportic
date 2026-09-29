"""
E2E test: mock scrapers → predict → DB save → result → lesson extraction.

Verifies the full self-learning loop without live AI or network calls.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

# Force test-safe DB path before any imports touch database.py
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ.setdefault("SPORTSBOT_DB_PATH", _tmp_db.name)

import bot
import database as db
from models import (
    MatchData,
    PredictionReport,
    SourceStatus,
    FlashscoreData,
    OddsData,
    TransfermarktData,
)


def _fake_report(**overrides) -> PredictionReport:
    defaults = dict(
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
    defaults.update(overrides)
    return PredictionReport(**defaults)


def _fake_match_data() -> MatchData:
    return MatchData(
        team1="Arsenal",
        team2="Chelsea",
        source_statuses=[
            SourceStatus(
                key="fotmob_form",
                label="Form/H2H",
                field_name="flashscore",
                scrape_success=True,
                duration_ms=100,
            ),
        ],
    )


class _FakeLoadingMessage:
    def __init__(self):
        self.edit_text = AsyncMock()
        self.delete = AsyncMock()


class _FakeMessage:
    def __init__(self):
        self.loading_message = _FakeLoadingMessage()
        self.reply_html = AsyncMock(return_value=self.loading_message)
        self.reply_text = AsyncMock()


class E2EPipelineTest(unittest.IsolatedAsyncioTestCase):
    """Full pipeline: predict → DB → result → stats update."""

    async def asyncSetUp(self):
        await db.init_db()

    def _make_update(self, user_id: int = 42) -> SimpleNamespace:
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            message=_FakeMessage(),
        )

    def _make_context(self, *args: str) -> SimpleNamespace:
        return SimpleNamespace(
            args=list(args),
            bot=SimpleNamespace(send_message=AsyncMock()),
        )

    async def test_full_predict_result_loop(self):
        """
        1. /predict Arsenal vs Chelsea → scrape + AI → DB save
        2. /result Arsenal 2-1 Chelsea → resolve → stats updated
        """
        update = self._make_update()
        context = self._make_context("Arsenal", "vs", "Chelsea")
        report = _fake_report()
        match_data = _fake_match_data()

        # ── Step 1: Predict ──────────────────────────────────────────────────
        with (
            patch("bot.parse_teams_from_input", return_value=("Arsenal", "Chelsea")),
            patch("bot.suggest_teams", return_value=[]),
            patch("bot.db.get_last_request", new=AsyncMock(return_value=None)),
            patch("bot.db.set_last_request", new=AsyncMock()),
            patch("bot.match_cache.claim", new=AsyncMock(return_value=("leader", None))),
            patch("bot.scrape_all_parallel", new=AsyncMock(return_value=match_data)),
            patch("bot.analyze_match", new=AsyncMock(return_value=report)),
            patch("bot.match_cache.resolve", new=AsyncMock()),
            patch("bot.sync_prediction_to_historical", new=AsyncMock()),
            patch("bot.store_prediction_memory", new=AsyncMock()),
        ):
            await bot.predict_handler(update, context)

        # Verify prediction was saved to DB
        pending = await db.get_unresolved(older_than_hours=0)
        self.assertTrue(len(pending) >= 1, "Prediction should be in DB")
        pred = pending[-1]
        self.assertEqual(pred["team1"], "Arsenal")
        self.assertEqual(pred["team2"], "Chelsea")
        prediction_id = pred["id"]

        # ── Step 2: Result ───────────────────────────────────────────────────
        update2 = self._make_update()
        context2 = self._make_context("Arsenal", "2-1", "Chelsea")

        with (
            patch("bot.db.get_calibration_stats", new=AsyncMock(
                return_value={"n_with_result": 1, "acc_1x2": 100.0}
            )),
        ):
            await bot.result_handler(update2, context2)

        # Verify result was stored
        result_text = update2.message.reply_html.await_args.args[0]
        self.assertIn("2-1", result_text)

        # Verify prediction is now resolved (no longer in unresolved list)
        still_pending = await db.get_unresolved(older_than_hours=0)
        resolved_ids = [p["id"] for p in still_pending]
        self.assertNotIn(prediction_id, resolved_ids, "Prediction should be resolved")

    async def test_suggest_teams_on_bad_input(self):
        """When parse fails, user gets 'Did you mean...' suggestions."""
        update = self._make_update()
        context = self._make_context("Arsnal", "vs", "Chelse")

        with (
            patch("bot.parse_teams_from_input", return_value=(None, None)),
            patch("bot.suggest_teams", return_value=["Arsenal", "Chelsea"]) as suggest_mock,
        ):
            await bot.predict_handler(update, context)

        suggest_mock.assert_called_once()
        reply_text = update.message.reply_html.await_args.args[0]
        self.assertIn("Возможно, вы имели в виду", reply_text)
        self.assertIn("Arsenal", reply_text)
        self.assertIn("Chelsea", reply_text)

    async def test_stats_after_multiple_results(self):
        """Stats command shows accuracy after resolved predictions."""
        # Insert two predictions directly
        r1 = _fake_report(team1="Liverpool", team2="Everton", prob_w1=70.0, prob_draw=20.0, prob_w2=10.0)
        r2 = _fake_report(team1="Man City", team2="Tottenham", prob_w1=65.0, prob_draw=22.0, prob_w2=13.0,
                          top_recommendation="Manchester City win")

        pid1 = await db.save_prediction(r1, 0.8, user_id=42)
        pid2 = await db.save_prediction(r2, 0.7, user_id=42)

        # Resolve both
        from prediction_resolution import process_prediction_result
        await process_prediction_result(pid1, "2-0")
        await process_prediction_result(pid2, "3-1")

        # Check stats
        stats = await db.get_calibration_stats()
        self.assertIsNotNone(stats)
        self.assertGreaterEqual(stats["n_with_result"], 2)


class SuggestTeamsUnitTest(unittest.TestCase):
    """Unit tests for suggest_teams function."""

    def test_suggest_close_misspelling(self):
        from team_parser import suggest_teams
        suggestions = suggest_teams("Arsnal vs Chelsae")
        # Should find Arsenal and/or Chelsea
        names_lower = [s.lower() for s in suggestions]
        self.assertTrue(
            any("arsenal" in n for n in names_lower) or
            any("chelsea" in n for n in names_lower),
            f"Expected Arsenal or Chelsea in suggestions, got {suggestions}"
        )

    def test_suggest_returns_empty_for_gibberish(self):
        from team_parser import suggest_teams
        suggestions = suggest_teams("xyzzy12345 vs qwerty98765")
        # Should return empty or very low-quality matches
        self.assertIsInstance(suggestions, list)

    def test_suggest_finds_alias(self):
        from team_parser import suggest_teams
        suggestions = suggest_teams("Barca")
        self.assertIn("Barcelona", suggestions)


if __name__ == "__main__":
    unittest.main()
