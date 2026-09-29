"""Tests for the lessons table CRUD in database.py."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import database as db
from models import PredictionFeatureSnapshot, PredictionReport


def _fake_report(team1="Arsenal", team2="Chelsea", **overrides) -> PredictionReport:
    defaults = dict(
        team1=team1,
        team2=team2,
        prob_w1=60.0,
        prob_draw=22.0,
        prob_w2=18.0,
        result_confidence="MEDIUM",
        prob_over_2_5=55.0,
        prob_under_2_5=45.0,
        expected_goals=2.7,
        goals_confidence="MEDIUM",
        asian_handicap_rec="Arsenal -0.5",
        asian_handicap_confidence="MEDIUM",
        prob_btts_yes=52.0,
        prob_btts_no=48.0,
        btts_confidence="MEDIUM",
        team1_strengths=["Home form"],
        team1_weaknesses=["Set pieces"],
        team2_strengths=["Transition play"],
        team2_weaknesses=["Away defending"],
        top_recommendation="Arsenal Win",
        top_recommendation_odds=1.8,
        top_confidence_pct=60.0,
        risk_level="MEDIUM",
        analysis_summary="Test prediction.",
        data_warnings=["minor warning"],
        value_bets=["Arsenal edge"],
        market_ah_line="Arsenal -0.5",
        market_ah_odds=1.95,
        methodology_version="test-methodology-v1",
        prompt_version="test-prompt-v1",
    )
    defaults.update(overrides)
    return PredictionReport(**defaults)


class LessonsCRUDTest(unittest.IsolatedAsyncioTestCase):
    _tmp_path: str = ""

    async def asyncSetUp(self):
        self._tmp_path = tempfile.mktemp(suffix=".db")
        # Point database module to temp file
        db.DB_PATH = type(db.DB_PATH)(self._tmp_path)
        await db.init_db()

    async def asyncTearDown(self):
        try:
            os.unlink(self._tmp_path)
        except OSError:
            pass

    async def test_save_and_get_lesson(self):
        lid = await db.save_lesson(
            text="Favorites overrated after CL midweek",
            category="OVERCONFIDENCE",
            confidence=0.7,
            source_match="Arsenal vs Chelsea 2026-03-01",
            source_prediction_id=42,
        )
        self.assertIsInstance(lid, int)
        self.assertGreater(lid, 0)

        lessons = await db.get_lessons()
        self.assertTrue(any(l["id"] == lid for l in lessons))
        match = next(l for l in lessons if l["id"] == lid)
        self.assertEqual(match["category"], "OVERCONFIDENCE")
        self.assertAlmostEqual(match["confidence"], 0.7)
        self.assertEqual(match["active"], 1)
        self.assertEqual(match["embedding_synced"], 0)

    async def test_get_lessons_filter_by_category(self):
        await db.save_lesson("H2H matters in derbies", "H2H_IGNORED", 0.6)
        await db.save_lesson("Market always right", "MARKET_SIGNAL", 0.5)

        h2h_lessons = await db.get_lessons(category="H2H_IGNORED")
        self.assertTrue(all(l["category"] == "H2H_IGNORED" for l in h2h_lessons))

    async def test_get_lessons_filter_by_min_confidence(self):
        await db.save_lesson("Low confidence lesson", "BTTS_ERROR", 0.2)
        await db.save_lesson("High confidence lesson", "OVERCONFIDENCE", 0.9)
        high_only = await db.get_lessons(min_confidence=0.5)
        self.assertTrue(all(l["confidence"] >= 0.5 for l in high_only))

    async def test_mark_lesson_synced(self):
        lid = await db.save_lesson("Test sync", "FORM_REVERSAL", 0.5)
        unsynced = await db.get_unsynced_lessons()
        self.assertTrue(any(l["id"] == lid for l in unsynced))

        await db.mark_lesson_synced(lid)
        unsynced_after = await db.get_unsynced_lessons()
        self.assertFalse(any(l["id"] == lid for l in unsynced_after))

    async def test_increment_lesson_confirmed(self):
        lid = await db.save_lesson("Pattern", "INJURY_IMPACT", 0.6)
        lessons_before = await db.get_lessons()
        count_before = next(l["times_confirmed"] for l in lessons_before if l["id"] == lid)
        self.assertEqual(count_before, 0)

        await db.increment_lesson_confirmed(lid)
        await db.increment_lesson_confirmed(lid)

        lessons_after = await db.get_lessons()
        count_after = next(l["times_confirmed"] for l in lessons_after if l["id"] == lid)
        self.assertEqual(count_after, 2)

    async def test_deactivate_lesson(self):
        lid = await db.save_lesson("To deactivate", "TOTAL_GOALS_ERROR", 0.4)
        await db.deactivate_lesson(lid)
        active = await db.get_lessons()
        self.assertFalse(any(l["id"] == lid for l in active))

    async def test_idempotent_migration(self):
        """Running init_db twice should not raise."""
        await db.init_db()
        await db.init_db()

    async def test_get_unsynced_only_returns_active(self):
        lid = await db.save_lesson("Inactive lesson", "UNDERDOG_MISS", 0.5)
        await db.deactivate_lesson(lid)
        unsynced = await db.get_unsynced_lessons()
        self.assertFalse(any(l["id"] == lid for l in unsynced))


class ResolvedPredictionTest(unittest.IsolatedAsyncioTestCase):
    _tmp_path: str = ""

    async def asyncSetUp(self):
        self._tmp_path = tempfile.mktemp(suffix=".db")
        db.DB_PATH = type(db.DB_PATH)(self._tmp_path)
        await db.init_db()

    async def asyncTearDown(self):
        try:
            os.unlink(self._tmp_path)
        except OSError:
            pass

    async def test_get_resolved_prediction_returns_none_for_missing(self):
        result = await db.get_resolved_prediction(99999)
        self.assertIsNone(result)

    async def test_get_recent_resolved_empty(self):
        result = await db.get_recent_resolved(limit=5)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)


class PredictionPersistenceTest(unittest.IsolatedAsyncioTestCase):
    _tmp_path: str = ""

    async def asyncSetUp(self):
        self._tmp_path = tempfile.mktemp(suffix=".db")
        db.DB_PATH = type(db.DB_PATH)(self._tmp_path)
        await db.init_db()

    async def asyncTearDown(self):
        try:
            os.unlink(self._tmp_path)
        except OSError:
            pass

    async def test_legacy_predictions_schema_migrates_full_snapshot_columns(self):
        legacy_path = tempfile.mktemp(suffix=".db")
        try:
            with sqlite3.connect(legacy_path) as conn:
                conn.executescript("""
                CREATE TABLE predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team1 TEXT NOT NULL,
                    team2 TEXT NOT NULL,
                    predicted_at TEXT NOT NULL,
                    prob_w1 REAL,
                    prob_draw REAL,
                    prob_w2 REAL,
                    prob_over_2_5 REAL,
                    prob_btts_yes REAL,
                    top_rec TEXT,
                    completeness REAL,
                    actual_result TEXT,
                    actual_score TEXT,
                    result_at TEXT
                );
                CREATE TABLE calibration (
                    computed_at TEXT PRIMARY KEY,
                    n_total INTEGER,
                    n_with_result INTEGER,
                    acc_1x2 REAL,
                    brier_1x2 REAL,
                    brier_ou REAL,
                    brier_btts REAL
                );
                CREATE TABLE rate_limits (
                    user_id INTEGER PRIMARY KEY,
                    last_request TEXT NOT NULL
                );
                CREATE TABLE lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    category TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    times_confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_match TEXT,
                    source_prediction_id INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    embedding_synced INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE team_memory (
                    team TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (team, key)
                );
                """)

            db.DB_PATH = type(db.DB_PATH)(legacy_path)
            await db.init_db()

            with sqlite3.connect(legacy_path) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
                feature_cols = {row[1] for row in conn.execute("PRAGMA table_info(prediction_features)")}
            self.assertIn("top_confidence_pct", cols)
            self.assertIn("expected_goals", cols)
            self.assertIn("risk_level", cols)
            self.assertIn("team1_strengths", cols)
            self.assertIn("data_warnings", cols)
            self.assertIn("notification_sent", cols)
            self.assertIn("historical_match_id", cols)
            self.assertIn("top_market_code", cols)
            self.assertIn("top_selection_code", cols)
            self.assertIn("value_candidate_snapshot", cols)
            self.assertIn("methodology_version", cols)
            self.assertIn("prompt_version", cols)
            self.assertIn("policy_flags", cols)
            self.assertIn("blend_alpha", feature_cols)
            self.assertIn("model_prob_w1", feature_cols)
            self.assertIn("market_prob_w1", feature_cols)
        finally:
            db.DB_PATH = type(db.DB_PATH)(self._tmp_path)
            try:
                os.unlink(legacy_path)
            except OSError:
                pass

    async def test_save_prediction_persists_methodology_metadata(self):
        report = _fake_report(
            top_market_code="1X2",
            top_selection_code="TEAM1",
            methodology_version="method-2026-03-09",
            prompt_version="single-call-2026-03-09",
            policy_flags=["path_single_call", "top_market_core"],
        )
        prediction_id = await db.save_prediction(report, completeness=0.8, user_id=5)

        with sqlite3.connect(db.DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT top_market_code, top_selection_code, methodology_version, prompt_version, policy_flags
                FROM predictions
                WHERE id=?
                """,
                (prediction_id,),
            ).fetchone()

        self.assertEqual(row[0], "1X2")
        self.assertEqual(row[1], "TEAM1")
        self.assertEqual(row[2], "method-2026-03-09")
        self.assertEqual(row[3], "single-call-2026-03-09")
        self.assertEqual(json.loads(row[4]), ["path_single_call", "top_market_core"])

    async def test_save_prediction_persists_feature_snapshot(self):
        report = _fake_report(
            feature_snapshot=PredictionFeatureSnapshot(
                team1="Arsenal",
                team2="Chelsea",
                data_completeness=0.8,
                successful_source_count=4,
                total_source_count=5,
                market_prob_w1=51.0,
                market_prob_draw=26.0,
                market_prob_w2=23.0,
                llm_prob_w1=58.0,
                llm_prob_draw=22.0,
                llm_prob_w2=20.0,
                model_prob_w1=53.0,
                model_prob_draw=25.0,
                model_prob_w2=22.0,
                blend_alpha=0.3,
                no_bet_triggered=False,
            )
        )
        prediction_id = await db.save_prediction(report, completeness=0.8, user_id=7)

        snapshot = await db.get_prediction_features(prediction_id)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["prediction_id"], prediction_id)
        self.assertAlmostEqual(snapshot["market_prob_w1"], 51.0)
        self.assertAlmostEqual(snapshot["model_prob_w1"], 53.0)
        self.assertAlmostEqual(snapshot["blend_alpha"], 0.3)

    async def test_backtest_rows_include_prediction_feature_snapshot(self):
        report = _fake_report(
            feature_snapshot=PredictionFeatureSnapshot(
                team1="Arsenal",
                team2="Chelsea",
                data_completeness=0.7,
                market_prob_w1=50.0,
                market_prob_draw=27.0,
                market_prob_w2=23.0,
                llm_prob_w1=55.0,
                llm_prob_draw=25.0,
                llm_prob_w2=20.0,
                model_prob_w1=52.0,
                model_prob_draw=26.0,
                model_prob_w2=22.0,
                blend_alpha=0.25,
            )
        )
        prediction_id = await db.save_prediction(report, completeness=0.8, user_id=11)
        await db.mark_result_by_prediction_id(prediction_id, "2-1")

        rows = await db.get_backtest_prediction_rows(limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prediction_id"], prediction_id)
        self.assertAlmostEqual(rows[0]["market_prob_w1"], 50.0)
        self.assertEqual(rows[0]["actual_result"], "W1")

    async def test_mark_result_by_prediction_id_normalizes_reversed_manual_score(self):
        report = _fake_report()
        prediction_id = await db.save_prediction(report, completeness=0.75, user_id=10)

        latest = await db.find_latest_unresolved_prediction("Chelsea", "Arsenal")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["id"], prediction_id)

        stored = await db.mark_result_by_prediction_id(
            prediction_id,
            "1-2",
            score_team1="Chelsea",
            score_team2="Arsenal",
        )
        self.assertTrue(stored)

        resolved = await db.get_resolved_prediction(prediction_id)
        self.assertEqual(resolved["actual_score"], "2-1")
        self.assertEqual(resolved["actual_goals_home"], 2)
        self.assertEqual(resolved["actual_goals_away"], 1)
        self.assertEqual(resolved["actual_result"], "W1")

    async def test_prediction_history_returns_confidence_and_version_metadata(self):
        report = _fake_report(
            top_confidence_pct=67.0,
            risk_level="LOW",
            methodology_version="method-v9",
            prompt_version="prompt-v4",
        )
        await db.save_prediction(report, completeness=0.9, user_id=3)

        rows = await db.get_prediction_history(limit=1)

        self.assertEqual(rows[0]["top_confidence_pct"], 67.0)
        self.assertEqual(rows[0]["risk_level"], "LOW")
        self.assertEqual(rows[0]["methodology_version"], "method-v9")
        self.assertEqual(rows[0]["prompt_version"], "prompt-v4")

    async def test_prediction_history_returns_stake_metadata(self):
        report = _fake_report(
            stake_units=0.5,
            stake_label="Small",
            stake_rationale="Playable edge, but size stays controlled.",
        )
        await db.save_prediction(report, completeness=0.9, user_id=3)

        rows = await db.get_prediction_history(limit=1)

        self.assertEqual(rows[0]["stake_units"], 0.5)
        self.assertEqual(rows[0]["stake_label"], "Small")

    async def test_get_paper_bankroll_stats_summarizes_weighted_results(self):
        win_report = _fake_report(
            top_market_code="1X2",
            top_selection_code="TEAM1",
            top_recommendation_odds=2.00,
            risk_level="LOW",
            stake_units=1.0,
            bankroll_mode="balanced",
            recommendation_mode="strict",
            policy_flags=["path_agent_chain", "top_market_core"],
            feature_snapshot=PredictionFeatureSnapshot(
                team1="Arsenal",
                team2="Chelsea",
                competition_name="Premier League",
                data_completeness=0.9,
            ),
        )
        loss_report = _fake_report(
            team1="Liverpool",
            team2="Chelsea",
            top_market_code="TOTALS_2_5",
            top_selection_code="OVER",
            top_recommendation="Over 2.5",
            top_recommendation_odds=1.90,
            risk_level="HIGH",
            stake_units=0.5,
            bankroll_mode="balanced",
            recommendation_mode="balanced",
            policy_flags=["path_single_call", "top_market_core"],
            feature_snapshot=PredictionFeatureSnapshot(
                team1="Liverpool",
                team2="Chelsea",
                competition_name="Premier League",
                data_completeness=0.9,
            ),
        )
        push_report = _fake_report(
            team1="Roma",
            team2="Milan",
            top_market_code="DNB",
            top_selection_code="TEAM1",
            top_recommendation="Roma Draw No Bet",
            top_recommendation_odds=1.80,
            risk_level="MEDIUM",
            stake_units=0.75,
            bankroll_mode="conservative",
            recommendation_mode="action",
            policy_flags=["path_pre_ai_gate", "top_market_derived"],
            feature_snapshot=PredictionFeatureSnapshot(
                team1="Roma",
                team2="Milan",
                competition_name="Serie A",
                data_completeness=0.9,
            ),
        )

        win_id = await db.save_prediction(win_report, completeness=0.9, user_id=1)
        loss_id = await db.save_prediction(loss_report, completeness=0.9, user_id=1)
        push_id = await db.save_prediction(push_report, completeness=0.9, user_id=1)

        await db.mark_result_by_prediction_id(win_id, "2-1")
        await db.mark_result_by_prediction_id(loss_id, "1-1")
        await db.mark_result_by_prediction_id(push_id, "1-1")

        stats = await db.get_paper_bankroll_stats()

        self.assertIsNotNone(stats)
        self.assertEqual(stats["tracked_bets"], 3)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["pushes"], 1)
        self.assertEqual(stats["staked_units"], 2.25)
        self.assertEqual(stats["profit_units"], 0.5)
        self.assertAlmostEqual(stats["current_bankroll_units"], 100.5)
        self.assertAlmostEqual(stats["roi_pct"], 22.22, places=2)
        self.assertEqual(stats["by_market"][0]["segment"], "1X2")
        self.assertEqual(stats["by_market"][0]["profit_units"], 1.0)
        self.assertEqual(stats["by_market"][1]["segment"], "Totals")
        self.assertEqual(stats["by_competition"][0]["segment"], "Premier League")
        self.assertEqual(stats["by_competition"][0]["tracked_bets"], 2)
        self.assertEqual(stats["by_competition"][0]["profit_units"], 0.5)
        self.assertEqual(stats["by_competition_policy"][0]["segment"], "Premier League / top_market_core")
        self.assertEqual(stats["by_competition_policy"][0]["tracked_bets"], 2)
        self.assertEqual(stats["by_competition_policy"][0]["sample_gate"], "low-sample")
        self.assertEqual(stats["by_risk"][0]["segment"], "LOW")
        self.assertEqual(stats["by_risk"][0]["profit_units"], 1.0)
        self.assertEqual(stats["by_bankroll_mode"][0]["segment"], "balanced")
        self.assertEqual(stats["by_bankroll_mode"][0]["tracked_bets"], 2)
        self.assertEqual(stats["by_recommendation_mode"][0]["segment"], "strict")
        self.assertEqual(stats["by_recommendation_mode"][0]["profit_units"], 1.0)
        self.assertEqual(stats["by_policy_flag"][0]["segment"], "top_market_core")
        self.assertEqual(stats["by_policy_flag"][0]["tracked_bets"], 2)
        self.assertEqual(stats["by_policy_flag"][0]["sample_gate"], "low-sample")
        self.assertEqual(stats["recent_form_20"]["tracked_bets"], 3)
        self.assertEqual(stats["recent_form_20"]["profit_units"], 0.5)
        self.assertEqual(stats["recent_form_20"]["streak_label"], "L1")

    async def test_get_bankroll_guardrails_summarizes_today_and_loss_streak(self):
        loss_report_1 = _fake_report(
            top_market_code="1X2",
            top_selection_code="TEAM1",
            top_recommendation_odds=2.10,
            stake_units=1.0,
        )
        loss_report_2 = _fake_report(
            team1="Liverpool",
            team2="Chelsea",
            top_market_code="1X2",
            top_selection_code="TEAM1",
            top_recommendation="Liverpool Win",
            top_recommendation_odds=2.20,
            stake_units=0.5,
        )
        pending_report = _fake_report(
            team1="Roma",
            team2="Milan",
            top_market_code="TOTALS_2_5",
            top_selection_code="OVER",
            top_recommendation="Over 2.5",
            top_recommendation_odds=1.95,
            stake_units=0.75,
        )

        loss_id_1 = await db.save_prediction(loss_report_1, completeness=0.9, user_id=1)
        loss_id_2 = await db.save_prediction(loss_report_2, completeness=0.9, user_id=1)
        await db.save_prediction(pending_report, completeness=0.9, user_id=1)

        await db.mark_result_by_prediction_id(loss_id_1, "0-1")
        await db.mark_result_by_prediction_id(loss_id_2, "1-2")

        guardrails = await db.get_bankroll_guardrails()

        self.assertEqual(guardrails["recommended_bets_today"], 3)
        self.assertEqual(guardrails["staked_today_units"], 2.25)
        self.assertEqual(guardrails["settled_bets_today"], 2)
        self.assertEqual(guardrails["settled_profit_today_units"], -1.5)
        self.assertEqual(guardrails["recent_loss_streak"], 2)

    async def test_prediction_history_for_match_is_bidirectional(self):
        await db.save_prediction(_fake_report("Arsenal", "Chelsea"), completeness=0.9, user_id=3)
        await db.save_prediction(_fake_report("Chelsea", "Arsenal"), completeness=0.8, user_id=4)
        await db.save_prediction(_fake_report("Liverpool", "Arsenal"), completeness=0.7, user_id=5)

        rows = await db.get_prediction_history_for_match("Arsenal", "Chelsea", limit=10)

        self.assertEqual(len(rows), 2)
        self.assertEqual({(row["team1"], row["team2"]) for row in rows}, {
            ("Arsenal", "Chelsea"),
            ("Chelsea", "Arsenal"),
        })


if __name__ == "__main__":
    unittest.main()
