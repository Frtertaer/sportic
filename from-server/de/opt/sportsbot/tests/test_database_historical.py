from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

import database as db
from models import PredictionFeatureSnapshot, PredictionReport


def _clv_report(team1: str = "Arsenal", team2: str = "Chelsea") -> PredictionReport:
    return PredictionReport(
        team1=team1,
        team2=team2,
        prob_w1=56.0,
        prob_draw=25.0,
        prob_w2=19.0,
        result_confidence="MEDIUM",
        prob_over_2_5=54.0,
        prob_under_2_5=46.0,
        expected_goals=2.6,
        goals_confidence="MEDIUM",
        asian_handicap_rec=f"{team1} -0.5",
        asian_handicap_confidence="MEDIUM",
        prob_btts_yes=51.0,
        prob_btts_no=49.0,
        btts_confidence="MEDIUM",
        team1_strengths=["Home edge"],
        team1_weaknesses=["Set pieces"],
        team2_strengths=["Transitions"],
        team2_weaknesses=["Away defending"],
        top_recommendation=f"{team1} Win",
        top_recommendation_odds=1.82,
        top_market_code="1X2",
        top_selection_code="TEAM1",
        top_confidence_pct=61.0,
        risk_level="MEDIUM",
        analysis_summary="Test prediction.",
        data_warnings=[],
        value_bets=[],
        value_candidate_snapshot=[],
        market_ah_line=f"{team1} -0.5",
        market_ah_odds=1.95,
    )


class HistoricalDatabaseTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_init_db_creates_historical_tables(self):
        with sqlite3.connect(self._tmp_path) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("historical_matches", names)
        self.assertIn("historical_market_lines", names)
        self.assertIn("historical_team_snapshots", names)
        self.assertIn("historical_provider_coverage", names)
        self.assertIn("historical_import_runs", names)
        self.assertIn("numeric_recalibration_profiles", names)
        self.assertIn("market_family_quality_profiles", names)

    async def test_init_db_migrates_new_historical_event_snapshot_columns(self):
        with sqlite3.connect(self._tmp_path) as conn:
            conn.execute("DROP TABLE historical_team_snapshots")
            conn.execute(
                """
                CREATE TABLE historical_team_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    historical_match_id INTEGER NOT NULL UNIQUE,
                    snapshot_source TEXT NOT NULL,
                    captured_at TEXT,
                    data_completeness REAL,
                    successful_source_count INTEGER,
                    total_source_count INTEGER,
                    team1_form_points_last5 REAL,
                    team2_form_points_last5 REAL,
                    team1_goal_diff_last5 REAL,
                    team2_goal_diff_last5 REAL,
                    team1_scored_avg REAL,
                    team2_scored_avg REAL,
                    team1_conceded_avg REAL,
                    team2_conceded_avg REAL,
                    competition_name TEXT,
                    schedule_density_note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

        await db.init_db()

        with sqlite3.connect(self._tmp_path) as conn:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(historical_team_snapshots)").fetchall()
            }
        self.assertIn("team1_shots_avg", columns)
        self.assertIn("team2_shots_on_target_avg", columns)
        self.assertIn("team1_corners_allowed_avg", columns)

    async def test_historical_match_market_snapshot_and_coverage_round_trip(self):
        match_id = await db.upsert_historical_match(
            {
                "provider_match_key": "fotmob:match:123",
                "provider_name": "fotmob",
                "competition_name": "Primeira Liga",
                "season_label": "2025/26",
                "match_date_utc": "2026-03-09",
                "kickoff_utc": "2026-03-09T17:30:00+00:00",
                "team1_name": "Tondela",
                "team2_name": "Rio Ave",
                "team1_normalized": "tondela",
                "team2_normalized": "rio ave",
                "is_neutral_venue": False,
                "venue_name": "Estadio Joao Cardoso",
                "status": "FINISHED",
            }
        )
        self.assertGreater(match_id, 0)

        same_match_id = await db.upsert_historical_match(
            {
                "provider_match_key": "fotmob:match:123",
                "provider_name": "fotmob",
                "competition_name": "Primeira Liga",
                "season_label": "2025/26",
                "match_date_utc": "2026-03-09",
                "team1_name": "Tondela",
                "team2_name": "Rio Ave",
                "team1_normalized": "tondela",
                "team2_normalized": "rio ave",
                "team1_score": 1,
                "team2_score": 0,
                "actual_result": "W1",
                "went_extra_time": False,
                "went_penalties": False,
                "status": "FINISHED",
            }
        )
        self.assertEqual(match_id, same_match_id)

        stored_match = await db.get_historical_match_by_provider_key("fotmob", "fotmob:match:123")
        assert stored_match is not None
        self.assertEqual(stored_match["id"], match_id)
        self.assertEqual(stored_match["actual_result"], "W1")
        self.assertEqual(stored_match["team1_score"], 1)
        self.assertIs(stored_match["is_neutral_venue"], False)

        market_line_id = await db.upsert_historical_market_line(
            {
                "historical_match_id": match_id,
                "snapshot_kind": "CLOSE",
                "source_provider": "betexplorer",
                "bookmaker_count": 22,
                "odds_w1": 2.35,
                "odds_draw": 3.10,
                "odds_w2": 3.20,
                "implied_prob_w1": 41.0,
                "implied_prob_draw": 31.0,
                "implied_prob_w2": 28.0,
                "odds_under_2_5": 1.74,
                "odds_btts_no": 1.78,
                "ah_main_line": "Tondela -0.25",
                "ah_team1_odds": 1.95,
                "ah_team2_odds": 1.91,
            }
        )
        self.assertGreater(market_line_id, 0)

        snapshot_id = await db.upsert_historical_team_snapshot(
            {
                "historical_match_id": match_id,
                "snapshot_source": "sportsbot-live-snapshot",
                "data_completeness": 0.67,
                "successful_source_count": 6,
                "total_source_count": 9,
                "team1_form_points_last5": 10.0,
                "team2_form_points_last5": 7.0,
                "team1_xg_for": 1.42,
                "team2_xg_for": 1.08,
                "rest_gap_hours": 18.0,
                "team1_short_rest": False,
                "team2_short_rest": True,
                "competition_name": "Primeira Liga",
            }
        )
        self.assertGreater(snapshot_id, 0)

        coverage_id = await db.upsert_historical_provider_coverage(
            {
                "historical_match_id": match_id,
                "source_key": "advanced_stats",
                "source_label": "Advanced stats",
                "status": "UNSUPPORTED",
                "error_type": "coverage_gap",
                "error_message": "Teams not found on provider",
                "duration_ms": 182,
                "provider_name": "fotmob",
            }
        )
        self.assertGreater(coverage_id, 0)

        lines = await db.get_historical_market_lines(match_id)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["snapshot_kind"], "CLOSE")
        self.assertEqual(lines[0]["ah_main_line"], "Tondela -0.25")

        snapshot = await db.get_historical_team_snapshot(match_id)
        assert snapshot is not None
        self.assertAlmostEqual(snapshot["team1_xg_for"], 1.42)
        self.assertIs(snapshot["team1_short_rest"], False)
        self.assertIs(snapshot["team2_short_rest"], True)

        coverage = await db.get_historical_provider_coverage(match_id)
        self.assertEqual(len(coverage), 1)
        self.assertEqual(coverage[0]["status"], "UNSUPPORTED")
        self.assertEqual(coverage[0]["error_type"], "coverage_gap")

    async def test_historical_import_run_round_trip(self):
        run_id = await db.start_historical_import_run(
            "BACKFILL",
            "betexplorer",
            season_label="2025/26",
            competition_name="La Liga",
            requested_from="2025-08-01",
            requested_to="2026-05-31",
        )
        self.assertGreater(run_id, 0)

        await db.finish_historical_import_run(
            run_id,
            status="PARTIAL",
            records_inserted=120,
            records_updated=8,
            records_failed=3,
            error_summary="3 rows had incomplete market data",
        )

        runs = await db.get_recent_historical_import_runs(limit=5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["status"], "PARTIAL")
        self.assertEqual(runs[0]["records_inserted"], 120)
        self.assertEqual(runs[0]["records_failed"], 3)
        self.assertIsNotNone(runs[0]["finished_at"])

    async def test_get_historical_backtest_rows_returns_snapshot_compatible_shape(self):
        match_id = await db.upsert_historical_match(
            {
                "provider_match_key": "espn:888",
                "provider_name": "espn",
                "competition_name": "La Liga",
                "match_date_utc": "2026-03-08",
                "kickoff_utc": "2026-03-08T20:00:00+00:00",
                "team1_name": "Villarreal",
                "team2_name": "Elche",
                "team1_normalized": "villarreal",
                "team2_normalized": "elche",
                "is_neutral_venue": False,
                "team1_score": 2,
                "team2_score": 1,
                "actual_result": "W1",
                "status": "FINISHED",
            }
        )
        await db.upsert_historical_team_snapshot(
            {
                "historical_match_id": match_id,
                "snapshot_source": "espn_scoreboard_basic",
                "data_completeness": 1.0,
                "successful_source_count": 1,
                "total_source_count": 1,
                "team1_form_points_last5": 10.0,
                "team2_form_points_last5": 4.0,
                "team1_points": 54,
                "team2_points": 26,
                "team1_shots_avg": 13.0,
                "team2_shots_avg": 8.0,
                "team1_shots_allowed_avg": 9.0,
                "team2_shots_allowed_avg": 12.0,
                "team1_shots_on_target_avg": 4.8,
                "team2_shots_on_target_avg": 2.9,
                "team1_shots_on_target_allowed_avg": 3.1,
                "team2_shots_on_target_allowed_avg": 4.0,
                "team1_corners_avg": 5.7,
                "team2_corners_avg": 4.1,
                "team1_corners_allowed_avg": 3.8,
                "team2_corners_allowed_avg": 5.2,
                "competition_name": "La Liga",
            }
        )
        await db.upsert_historical_market_line(
            {
                "historical_match_id": match_id,
                "snapshot_kind": "CLOSE",
                "source_provider": "espn:draft-kings",
                "odds_w1": 1.95,
                "odds_draw": 3.40,
                "odds_w2": 4.20,
                "implied_prob_w1": 48.5,
                "implied_prob_draw": 27.8,
                "implied_prob_w2": 23.7,
                "odds_over_2_5": 2.05,
                "odds_under_2_5": 1.75,
                "ah_main_line": "Villarreal -0.5",
                "ah_team1_odds": 1.98,
                "ah_team2_odds": 1.88,
            }
        )

        rows = await db.get_historical_backtest_rows(limit=5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["team1"], "Villarreal")
        self.assertEqual(row["team2"], "Elche")
        self.assertEqual(row["actual_result"], "W1")
        self.assertEqual(row["actual_goals_home"], 2)
        self.assertEqual(row["actual_goals_away"], 1)
        self.assertAlmostEqual(row["form_points_last5_diff"], 6.0)
        self.assertAlmostEqual(row["standings_point_gap"], 28.0)
        self.assertAlmostEqual(row["shots_avg_diff"], 5.0)
        self.assertAlmostEqual(row["shots_on_target_avg_diff"], 1.9)
        self.assertAlmostEqual(row["corners_avg_diff"], 1.6)
        self.assertAlmostEqual(row["shots_balance_diff"], 8.0)
        self.assertAlmostEqual(row["shots_on_target_balance_diff"], 2.8)
        self.assertAlmostEqual(row["corners_balance_diff"], 3.0)
        self.assertIs(row["is_team1_home"], True)
        self.assertIs(row["is_neutral_venue"], False)
        self.assertIsNotNone(row["market_prob_over_2_5"])
        self.assertIsNotNone(row["market_prob_under_2_5"])

        snapshot = PredictionFeatureSnapshot.model_validate(row)
        self.assertEqual(snapshot.team1, "Villarreal")
        self.assertEqual(snapshot.team2, "Elche")
        self.assertAlmostEqual(snapshot.form_points_last5_diff or 0.0, 6.0)
        self.assertAlmostEqual(snapshot.standings_point_gap or 0.0, 28.0)
        self.assertAlmostEqual(snapshot.shots_avg_diff or 0.0, 5.0)
        self.assertAlmostEqual(snapshot.shots_on_target_balance_diff or 0.0, 2.8)

    async def test_get_historical_backtest_rows_falls_back_to_mid_market_snapshot(self):
        match_id = await db.upsert_historical_match(
            {
                "provider_match_key": "sportsbot:fixture:123",
                "provider_name": "sportsbot_live",
                "competition_name": "Premier League",
                "match_date_utc": "2026-03-20",
                "kickoff_utc": "2026-03-20T19:00:00+00:00",
                "team1_name": "Arsenal",
                "team2_name": "Chelsea",
                "team1_normalized": "arsenal",
                "team2_normalized": "chelsea",
                "is_neutral_venue": False,
                "team1_score": 2,
                "team2_score": 1,
                "actual_result": "W1",
                "status": "FINISHED",
            }
        )
        await db.upsert_historical_team_snapshot(
            {
                "historical_match_id": match_id,
                "snapshot_source": "sportsbot_live_prediction",
                "data_completeness": 0.66,
                "successful_source_count": 2,
                "total_source_count": 3,
                "team1_form_points_last5": 10.0,
                "team2_form_points_last5": 4.0,
            }
        )
        await db.upsert_historical_market_line(
            {
                "historical_match_id": match_id,
                "snapshot_kind": "MID",
                "source_provider": "sportsbot:live-predict",
                "odds_w1": 1.91,
                "odds_draw": 3.55,
                "odds_w2": 4.30,
                "implied_prob_w1": 50.3,
                "implied_prob_draw": 27.1,
                "implied_prob_w2": 22.6,
            }
        )

        rows = await db.get_historical_backtest_rows(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["market_prob_w1"], 50.3)
        self.assertAlmostEqual(rows[0]["market_prob_draw"], 27.1)
        self.assertAlmostEqual(rows[0]["market_prob_w2"], 22.6)

    async def test_get_historical_backtest_rows_merges_market_fields_across_close_sources(self):
        match_id = await db.upsert_historical_match(
            {
                "provider_match_key": "fd:fra1:merged",
                "provider_name": "football-data.co.uk",
                "competition_name": "Ligue 1",
                "match_date_utc": "2026-01-15",
                "kickoff_utc": "2026-01-15T20:00:00+00:00",
                "team1_name": "Rennes",
                "team2_name": "Nice",
                "team1_normalized": "rennes",
                "team2_normalized": "nice",
                "is_neutral_venue": False,
                "team1_score": 2,
                "team2_score": 1,
                "actual_result": "W1",
                "status": "FINISHED",
            }
        )
        await db.upsert_historical_team_snapshot(
            {
                "historical_match_id": match_id,
                "snapshot_source": "football_data_csv_rolling",
                "data_completeness": 1.0,
                "successful_source_count": 3,
                "total_source_count": 3,
                "team1_form_points_last5": 11.0,
                "team2_form_points_last5": 7.0,
            }
        )
        await db.upsert_historical_market_line(
            {
                "historical_match_id": match_id,
                "snapshot_kind": "CLOSE",
                "source_provider": "football-data.co.uk:consensus",
                "odds_w1": 2.02,
                "odds_draw": 3.35,
                "odds_w2": 3.85,
                "implied_prob_w1": 46.2,
                "implied_prob_draw": 27.9,
                "implied_prob_w2": 25.9,
                "odds_over_2_5": 1.91,
                "odds_under_2_5": 1.94,
            }
        )
        await db.upsert_historical_market_line(
            {
                "historical_match_id": match_id,
                "snapshot_kind": "CLOSE",
                "source_provider": "betexplorer:historical-close",
                "odds_btts_yes": 1.82,
                "odds_btts_no": 1.98,
                "ah_main_line": "Rennes -0.25",
                "ah_team1_odds": 1.93,
                "ah_team2_odds": 1.95,
                "odds_dnb_team1": 1.47,
                "odds_dnb_team2": 2.65,
                "odds_dc_1x": 1.24,
                "odds_dc_12": 1.38,
                "odds_dc_x2": 1.79,
            }
        )

        rows = await db.get_historical_backtest_rows(limit=5)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["market_prob_w1"], 46.2)
        self.assertAlmostEqual(row["odds_over_2_5"], 1.91)
        self.assertAlmostEqual(row["odds_btts_yes"], 1.82)
        self.assertAlmostEqual(row["odds_btts_no"], 1.98)
        self.assertIsNotNone(row["market_prob_btts_yes"])
        self.assertIsNotNone(row["market_prob_btts_no"])
        self.assertEqual(row["ah_main_line"], "Rennes -0.25")
        self.assertAlmostEqual(row["odds_dnb_team1"], 1.47)
        self.assertAlmostEqual(row["odds_dc_x2"], 1.79)

    async def test_recompute_numeric_recalibration_writes_profiles(self):
        for idx in range(4):
            match_id = await db.upsert_historical_match(
                {
                    "provider_match_key": f"espn:cal:{idx}",
                    "provider_name": "espn",
                    "competition_name": "La Liga",
                    "match_date_utc": f"2026-03-0{idx + 1}",
                    "kickoff_utc": f"2026-03-0{idx + 1}T20:00:00+00:00",
                    "team1_name": f"Home {idx}",
                    "team2_name": f"Away {idx}",
                    "team1_normalized": f"home-{idx}",
                    "team2_normalized": f"away-{idx}",
                    "is_neutral_venue": False,
                    "team1_score": 0,
                    "team2_score": 1,
                    "actual_result": "W2",
                    "status": "FINISHED",
                }
            )
            await db.upsert_historical_team_snapshot(
                {
                    "historical_match_id": match_id,
                    "snapshot_source": "espn_scoreboard_basic",
                    "data_completeness": 1.0,
                    "successful_source_count": 1,
                    "total_source_count": 1,
                    "team1_form_points_last5": 10.0,
                    "team2_form_points_last5": 4.0,
                    "team1_points": 60,
                    "team2_points": 42,
                    "competition_name": "La Liga",
                }
            )
            await db.upsert_historical_market_line(
                {
                    "historical_match_id": match_id,
                    "snapshot_kind": "CLOSE",
                    "source_provider": "espn:draft-kings",
                    "odds_w1": 1.95,
                    "odds_draw": 3.40,
                    "odds_w2": 4.20,
                    "implied_prob_w1": 48.5,
                    "implied_prob_draw": 27.8,
                    "implied_prob_w2": 23.7,
                    "odds_over_2_5": 2.05,
                    "odds_under_2_5": 1.75,
                    "odds_btts_yes": 1.95,
                    "odds_btts_no": 1.85,
                }
            )

        summary = await db.recompute_numeric_recalibration()
        self.assertGreater(summary["profiles_written"], 0)
        self.assertGreater(summary["family_profiles_written"], 0)

        profiles = await db.get_numeric_recalibration_profiles("La Liga")
        self.assertTrue(any(profile["scope"] == "GLOBAL" for profile in profiles))
        self.assertTrue(any(profile["scope"] == "COMPETITION" for profile in profiles))
        self.assertTrue(any(profile["market"] == "1X2_TOP_PICK" for profile in profiles))
        self.assertTrue(any(str(profile["market"]).startswith("BTTS_YES_") for profile in profiles))

        family_profiles = await db.get_market_family_quality_profiles("La Liga")
        self.assertTrue(any(profile["scope"] == "GLOBAL" for profile in family_profiles))
        self.assertTrue(any(profile["scope"] == "COMPETITION" for profile in family_profiles))
        self.assertTrue(any(profile["family"] == "1x2" for profile in family_profiles))

    async def test_get_prediction_clv_rows_joins_linked_open_and_close_lines(self):
        report = _clv_report()
        prediction_id = await db.save_prediction(report, completeness=0.66, user_id=5)
        await db.mark_result_by_prediction_id(prediction_id, "2-1")

        match_id = await db.upsert_historical_match(
            {
                "provider_match_key": "sportsbot:fixture:clv",
                "provider_name": "sportsbot_live",
                "competition_name": "Premier League",
                "match_date_utc": "2026-03-20",
                "kickoff_utc": "2026-03-20T19:00:00+00:00",
                "team1_name": "Arsenal",
                "team2_name": "Chelsea",
                "team1_normalized": "arsenal",
                "team2_normalized": "chelsea",
                "team1_score": 2,
                "team2_score": 1,
                "actual_result": "W1",
                "status": "FINISHED",
            }
        )
        await db.link_prediction_to_historical_match(prediction_id, match_id)
        await db.upsert_historical_market_line(
            {
                "historical_match_id": match_id,
                "snapshot_kind": "OPEN",
                "source_provider": "sportsbot:live-predict",
                "odds_w1": 1.91,
                "odds_draw": 3.55,
                "odds_w2": 4.30,
                "odds_over_2_5": 2.01,
                "odds_under_2_5": 1.82,
            }
        )
        await db.upsert_historical_market_line(
            {
                "historical_match_id": match_id,
                "snapshot_kind": "CLOSE",
                "source_provider": "sportsbot:live-predict",
                "odds_w1": 1.74,
                "odds_draw": 3.70,
                "odds_w2": 4.80,
                "odds_over_2_5": 1.95,
                "odds_under_2_5": 1.89,
            }
        )
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute(
                """
                UPDATE predictions
                SET value_candidate_snapshot=?
                WHERE id=?
                """,
                (
                    '[{"label":"Over 2.5","family":"totals","market_code":"TOTALS_2_5","selection_code":"OVER","probability":58.0,"market_probability":52.0,"edge":6.0,"odds":2.02}]',
                    prediction_id,
                ),
            )
            conn.commit()

        rows = await db.get_prediction_clv_rows(limit=5)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["prediction_id"], prediction_id)
        self.assertEqual(row["competition_name"], "Premier League")
        self.assertEqual(row["top_recommendation"], "Arsenal Win")
        self.assertEqual(row["top_market_code"], "1X2")
        self.assertEqual(row["top_selection_code"], "TEAM1")
        self.assertEqual(row["actual_result"], "W1")
        self.assertEqual(row["actual_goals_home"], 2)
        self.assertEqual(row["actual_goals_away"], 1)
        self.assertEqual(len(row["value_candidate_snapshot"]), 1)
        self.assertEqual(row["value_candidate_snapshot"][0]["market_code"], "TOTALS_2_5")
        self.assertAlmostEqual(row["entry_odds"], 1.82)
        self.assertAlmostEqual(row["open_odds_w1"], 1.91)
        self.assertAlmostEqual(row["close_odds_w1"], 1.74)
