from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import config
import database as db
import prediction_resolution
import result_fetcher
from memory import reset_shared_vector_memory
from models import (
    MatchContextData,
    FlashscoreData,
    H2HRecord,
    MatchData,
    MatchResult,
    OddsData,
    PredictionReport,
    RecentMatch,
    ScheduleLoadData,
    SourceStatus,
    StandingsContextData,
    TableEntrySummary,
    TeamScheduleLoadSummary,
    TransfermarktData,
)
from prediction_resolution import (
    process_prediction_result,
    store_prediction_memory,
    sync_prediction_to_historical,
)


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
        top_recommendation_odds=1.82,
        top_confidence_pct=60.0,
        risk_level="MEDIUM",
        analysis_summary="Arsenal edge at home.",
        data_warnings=[],
        value_bets=["Arsenal edge"],
        market_ah_line="Arsenal -0.5",
        market_ah_odds=1.95,
    )
    defaults.update(overrides)
    return PredictionReport(**defaults)


def _fake_match_data(team1="Arsenal", team2="Chelsea") -> MatchData:
    flashscore = FlashscoreData(
        team1_name=team1,
        team2_name=team2,
        league_name="Premier League",
        team1_form=[
            RecentMatch(
                date="2026-03-01",
                opponent="Spurs",
                result=MatchResult.WIN,
                score="2-1",
                home_away="H",
            )
        ],
        team2_form=[
            RecentMatch(
                date="2026-03-01",
                opponent="Liverpool",
                result=MatchResult.LOSS,
                score="0-2",
                home_away="A",
            )
        ],
        h2h_matches=[
            H2HRecord(
                date="2025-12-01",
                home_team=team1,
                away_team=team2,
                score="2-1",
            )
        ],
        scrape_success=True,
    )
    return MatchData(team1=team1, team2=team2, flashscore=flashscore)


def _historical_ready_match_data(team1="Arsenal", team2="Chelsea") -> MatchData:
    flashscore = FlashscoreData(
        team1_name=team1,
        team2_name=team2,
        league_name="Premier League",
        team1_form=[
            RecentMatch(date="2026-03-01", opponent="Spurs", result=MatchResult.WIN, score="2-1", home_away="H"),
            RecentMatch(date="2026-02-24", opponent="Everton", result=MatchResult.DRAW, score="1-1", home_away="A"),
        ],
        team2_form=[
            RecentMatch(date="2026-03-01", opponent="Liverpool", result=MatchResult.LOSS, score="0-2", home_away="A"),
            RecentMatch(date="2026-02-23", opponent="Villa", result=MatchResult.WIN, score="3-1", home_away="H"),
        ],
        h2h_matches=[
            H2HRecord(date="2025-12-01", home_team=team1, away_team=team2, score="2-1"),
            H2HRecord(date="2025-08-15", home_team=team2, away_team=team1, score="1-1"),
        ],
        team1_goals_scored_avg=1.8,
        team1_goals_conceded_avg=1.0,
        team2_goals_scored_avg=1.2,
        team2_goals_conceded_avg=1.4,
        scrape_success=True,
    )
    odds = OddsData(
        team1_win_odds=1.91,
        draw_odds=3.55,
        team2_win_odds=4.3,
        over_1_5_odds=1.33,
        under_1_5_odds=3.4,
        over_2_5_odds=2.01,
        under_2_5_odds=1.82,
        over_3_5_odds=3.45,
        under_3_5_odds=1.34,
        dnb_team1_odds=1.35,
        dnb_team2_odds=3.2,
        double_chance_1x_odds=1.22,
        double_chance_12_odds=1.31,
        double_chance_x2_odds=2.02,
        asian_handicap=f"{team1} -0.5",
        asian_handicap_line=-0.5,
        asian_handicap_home_odds=1.95,
        asian_handicap_away_odds=1.91,
        btts_yes_odds=1.93,
        btts_no_odds=1.87,
        implied_prob_w1=0.503,
        implied_prob_draw=0.271,
        implied_prob_w2=0.226,
        scrape_success=True,
    )
    standings = StandingsContextData(
        team1_name=team1,
        team2_name=team2,
        league_name="Premier League",
        team1_overall=TableEntrySummary(position=2, points=60),
        team2_overall=TableEntrySummary(position=8, points=42),
        scrape_success=True,
    )
    match_context = MatchContextData(
        team1_name=team1,
        team2_name=team2,
        competition_name="Premier League",
        kickoff_utc="2026-03-20T19:00:00+00:00",
        venue_name="Emirates Stadium",
        is_neutral_venue=False,
        is_team1_home=True,
        stage="Round 30",
        scrape_success=True,
    )
    schedule = ScheduleLoadData(
        team1_name=team1,
        team2_name=team2,
        team1=TeamScheduleLoadSummary(short_rest=False, matches_last_7d=1),
        team2=TeamScheduleLoadSummary(short_rest=True, matches_last_7d=2),
        rest_gap_hours=36.0,
        load_warnings=["Away side on shorter rest"],
        scrape_success=True,
    )
    transfermarkt = TransfermarktData(
        team1_name=team1,
        team2_name=team2,
        team1_market_value_eur=800.0,
        team2_market_value_eur=520.0,
        scrape_success=True,
    )
    statuses = [
        SourceStatus(
            key="fotmob_form",
            label="Form/H2H",
            field_name="flashscore",
            scrape_success=True,
            provider="fotmob",
            duration_ms=120,
        ),
        SourceStatus(
            key="betexplorer",
            label="Odds",
            field_name="odds",
            scrape_success=True,
            provider="betexplorer",
            duration_ms=210,
        ),
        SourceStatus(
            key="advanced_stats",
            label="Advanced stats",
            field_name="advanced_stats",
            scrape_success=False,
            applicable=False,
            coverage_gap=True,
            provider="fotmob",
            error_message="Teams not found on provider",
            duration_ms=80,
        ),
    ]
    return MatchData(
        team1=team1,
        team2=team2,
        flashscore=flashscore,
        odds=odds,
        standings_context=standings,
        match_context=match_context,
        schedule_load=schedule,
        transfermarkt=transfermarkt,
        source_statuses=statuses,
    )


class PredictionResolutionTest(unittest.IsolatedAsyncioTestCase):
    _tmp_db: str = ""
    _tmp_chroma: str = ""

    async def asyncSetUp(self):
        self._tmp_db = tempfile.mktemp(suffix=".db")
        db.DB_PATH = type(db.DB_PATH)(self._tmp_db)
        await db.init_db()
        reset_shared_vector_memory()

        self._tmp_chroma = tempfile.mkdtemp(prefix="resolution_chroma_")
        config.CHROMADB_PATH = self._tmp_chroma
        self._orig_pinecone = config.PINECONE_ENABLED
        config.PINECONE_ENABLED = False

    async def asyncTearDown(self):
        config.PINECONE_ENABLED = self._orig_pinecone
        try:
            os.unlink(self._tmp_db)
        except OSError:
            pass
        shutil.rmtree(self._tmp_chroma, ignore_errors=True)

    async def test_prediction_saved_then_result_updates_vector_record(self):
        from memory import VectorMemory

        report = _fake_report()
        match_data = _fake_match_data()
        prediction_id = await db.save_prediction(report, completeness=0.75, user_id=77)
        await store_prediction_memory(prediction_id, report, match_data)

        vm = VectorMemory()
        await vm.initialize()
        before = await vm.find_similar_matches("Arsenal", "Chelsea")
        self.assertTrue(before)
        self.assertIsNone(before[0].actual_result)

        with patch("prediction_resolution.LearningModule.process_result", new=AsyncMock(return_value=1)):
            resolved = await process_prediction_result(prediction_id, "2-1")

        self.assertIsNotNone(resolved)

        vm_after = VectorMemory()
        await vm_after.initialize()
        after = await vm_after.find_similar_matches("Arsenal", "Chelsea")
        self.assertTrue(after)
        self.assertEqual(after[0].actual_result, "W1")
        self.assertEqual(after[0].actual_score, "2-1")

    async def test_background_result_resolution_keeps_prediction_id_user_mapping(self):
        report1 = _fake_report()
        report2 = _fake_report(top_recommendation="Draw")
        pred1 = await db.save_prediction(report1, completeness=0.75, user_id=111)
        pred2 = await db.save_prediction(report2, completeness=0.75, user_id=222)

        old_ts = "2026-03-01T00:00:00+00:00"
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute(
                "UPDATE predictions SET predicted_at=? WHERE id IN (?, ?)",
                (old_ts, pred1, pred2),
            )
            conn.commit()

        with patch("result_fetcher.fetch_actual_result", new=AsyncMock(return_value=("W1", "2-1"))):
            with patch("prediction_resolution.LearningModule.process_result", new=AsyncMock(return_value=1)):
                with patch("prediction_resolution.update_prediction_memory_result", new=AsyncMock()):
                    resolved_list = await result_fetcher.check_pending_results()

        self.assertEqual(len(resolved_list), 2)
        self.assertEqual({item["prediction_id"] for item in resolved_list}, {pred1, pred2})
        self.assertEqual({item["user_id"] for item in resolved_list}, {111, 222})

        resolved1 = await db.get_resolved_prediction(pred1)
        resolved2 = await db.get_resolved_prediction(pred2)
        self.assertEqual(resolved1["actual_score"], "2-1")
        self.assertEqual(resolved2["actual_score"], "2-1")

    async def test_vector_memory_singleton_is_reused_inside_hot_path(self):
        fake_vm = AsyncMock()
        fake_vm.store_match = AsyncMock()
        fake_vm.update_match_result = AsyncMock()

        with patch("prediction_resolution.get_shared_vector_memory", new=AsyncMock(return_value=fake_vm)):
            await store_prediction_memory(1, _fake_report(), _fake_match_data())
            await prediction_resolution.update_prediction_memory_result(1, "W1", "2-1")

        self.assertEqual(fake_vm.store_match.await_count, 1)
        self.assertEqual(fake_vm.update_match_result.await_count, 1)

    async def test_sync_prediction_to_historical_captures_live_artifacts(self):
        report = _fake_report()
        match_data = _historical_ready_match_data()
        prediction_id = await db.save_prediction(report, completeness=match_data.data_completeness, user_id=77)

        historical_match_id = await sync_prediction_to_historical(prediction_id, report, match_data)
        self.assertIsNotNone(historical_match_id)

        with sqlite3.connect(db.DB_PATH) as conn:
            linked = conn.execute(
                "SELECT historical_match_id FROM predictions WHERE id=?",
                (prediction_id,),
            ).fetchone()
        self.assertEqual(linked[0], historical_match_id)

        historical_match = await db.get_historical_match(historical_match_id)
        assert historical_match is not None
        self.assertEqual(historical_match["provider_name"], "sportsbot_live")
        self.assertEqual(historical_match["competition_name"], "Premier League")
        self.assertEqual(historical_match["status"], "SCHEDULED")

        market_lines = await db.get_historical_market_lines(historical_match_id)
        self.assertEqual(len(market_lines), 2)
        self.assertEqual([line["snapshot_kind"] for line in market_lines], ["OPEN", "MID"])
        self.assertTrue(all(line["source_provider"] == "sportsbot:live-predict" for line in market_lines))
        self.assertAlmostEqual(market_lines[0]["odds_w1"], 1.91)
        self.assertAlmostEqual(market_lines[1]["odds_w1"], 1.91)

        snapshot = await db.get_historical_team_snapshot(historical_match_id)
        assert snapshot is not None
        self.assertAlmostEqual(snapshot["team1_form_points_last5"], 4.0)
        self.assertEqual(snapshot["team1_points"], 60)
        self.assertEqual(snapshot["team2_points"], 42)

        coverage = await db.get_historical_provider_coverage(historical_match_id)
        self.assertEqual(len(coverage), 3)
        unsupported = next(item for item in coverage if item["source_key"] == "advanced_stats")
        self.assertEqual(unsupported["status"], "UNSUPPORTED")
        self.assertEqual(unsupported["error_type"], "coverage_gap")

    async def test_sync_prediction_to_historical_reuses_same_fixture_row(self):
        report1 = _fake_report()
        report2 = _fake_report(top_recommendation="Under 2.5 Goals")
        match_data = _historical_ready_match_data()

        prediction_id_1 = await db.save_prediction(report1, completeness=match_data.data_completeness, user_id=1)
        prediction_id_2 = await db.save_prediction(report2, completeness=match_data.data_completeness, user_id=2)

        historical_match_id_1 = await sync_prediction_to_historical(prediction_id_1, report1, match_data)
        historical_match_id_2 = await sync_prediction_to_historical(prediction_id_2, report2, match_data)

        self.assertEqual(historical_match_id_1, historical_match_id_2)
        with sqlite3.connect(db.DB_PATH) as conn:
            count = conn.execute("SELECT COUNT(*) FROM historical_matches").fetchone()[0]
        self.assertEqual(count, 1)

    async def test_sync_prediction_to_historical_preserves_open_and_refreshes_mid(self):
        report = _fake_report()
        match_data = _historical_ready_match_data()
        prediction_id_1 = await db.save_prediction(report, completeness=match_data.data_completeness, user_id=1)
        historical_match_id = await sync_prediction_to_historical(prediction_id_1, report, match_data)

        updated_match_data = _historical_ready_match_data()
        assert updated_match_data.odds is not None
        updated_match_data.odds.team1_win_odds = 2.05
        updated_match_data.odds.draw_odds = 3.40
        updated_match_data.odds.team2_win_odds = 4.05
        prediction_id_2 = await db.save_prediction(report, completeness=updated_match_data.data_completeness, user_id=2)
        await sync_prediction_to_historical(prediction_id_2, report, updated_match_data)

        market_lines = await db.get_historical_market_lines(historical_match_id)
        self.assertEqual(len(market_lines), 2)
        open_line = next(line for line in market_lines if line["snapshot_kind"] == "OPEN")
        mid_line = next(line for line in market_lines if line["snapshot_kind"] == "MID")
        self.assertAlmostEqual(open_line["odds_w1"], 1.91)
        self.assertAlmostEqual(mid_line["odds_w1"], 2.05)

    async def test_process_prediction_result_updates_linked_historical_match(self):
        report = _fake_report()
        match_data = _historical_ready_match_data()
        prediction_id = await db.save_prediction(report, completeness=match_data.data_completeness, user_id=88)
        historical_match_id = await sync_prediction_to_historical(prediction_id, report, match_data)

        with patch("prediction_resolution.LearningModule.process_result", new=AsyncMock(return_value=1)):
            with patch("prediction_resolution.update_prediction_memory_result", new=AsyncMock()):
                await process_prediction_result(prediction_id, "2-1")

        historical_match = await db.get_historical_match(historical_match_id)
        assert historical_match is not None
        self.assertEqual(historical_match["actual_result"], "W1")
        self.assertEqual(historical_match["team1_score"], 2)
        self.assertEqual(historical_match["team2_score"], 1)
        self.assertEqual(historical_match["status"], "FINISHED")

        market_lines = await db.get_historical_market_lines(historical_match_id)
        self.assertEqual([line["snapshot_kind"] for line in market_lines], ["OPEN", "MID", "CLOSE"])
        close_line = next(line for line in market_lines if line["snapshot_kind"] == "CLOSE")
        self.assertAlmostEqual(close_line["odds_w1"], 1.91)


if __name__ == "__main__":
    unittest.main()
