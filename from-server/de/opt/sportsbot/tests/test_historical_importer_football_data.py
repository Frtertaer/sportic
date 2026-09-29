from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

import database as db
from historical_importers.football_data import FootballDataHistoricalImporter


def _write_csv(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


class FootballDataHistoricalImporterTests(unittest.IsolatedAsyncioTestCase):
    _tmp_db: str = ""
    _tmp_dir: str = ""

    async def asyncSetUp(self):
        self._tmp_db = tempfile.mktemp(suffix=".db")
        self._tmp_dir = tempfile.mkdtemp()
        db.DB_PATH = type(db.DB_PATH)(self._tmp_db)
        await db.init_db()

    async def asyncTearDown(self):
        try:
            os.unlink(self._tmp_db)
        except OSError:
            pass
        try:
            for child in Path(self._tmp_dir).iterdir():
                child.unlink()
            Path(self._tmp_dir).rmdir()
        except OSError:
            pass

    async def test_importer_persists_matches_open_close_markets_and_rolling_snapshot(self):
        csv_path = Path(self._tmp_dir) / "F1.csv"
        _write_csv(
            csv_path,
            """
            Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,HST,AST,HC,AC,AvgH,AvgD,AvgA,Avg>2.5,Avg<2.5,AHh,AvgAHH,AvgAHA,AvgCH,AvgCD,AvgCA,AvgC>2.5,AvgC<2.5,AHCh,AvgCAHH,AvgCAHA
            F1,01/08/2025,19:45,Rennes,Marseille,1,0,H,10,8,4,2,6,3,3.20,3.40,2.20,1.85,1.95,0.5,1.80,2.00,3.40,3.50,2.10,1.82,2.00,0.5,1.86,1.94
            F1,08/08/2025,20:00,Lyon,Rennes,0,2,A,11,9,3,5,4,7,2.50,3.25,2.80,1.90,1.92,-0.25,1.95,1.89,2.60,3.15,2.70,1.88,1.96,0,1.90,1.96
            F1,15/08/2025,18:00,Marseille,Rennes,1,1,D,12,10,5,4,6,5,2.45,3.15,2.95,1.91,1.94,0,1.91,1.95,2.52,3.08,2.88,1.89,1.95,-0.25,1.98,1.88
            """,
        )

        importer = FootballDataHistoricalImporter(csv_path=csv_path)
        summary = await importer.run()

        self.assertEqual(summary.rows_processed, 3)
        self.assertEqual(summary.match_rows_inserted, 3)
        self.assertEqual(summary.market_rows_inserted, 6)
        self.assertEqual(summary.snapshot_rows_inserted, 2)
        self.assertEqual(summary.skipped_rows, 0)
        self.assertIsNotNone(summary.run_id)
        self.assertEqual(summary.division_code, "F1")
        self.assertEqual(summary.betexplorer_league_key, "fra.1")

        matches = await db.get_historical_matches_for_range("2025-08-01", "2025-08-15", competition_name="Ligue 1")
        self.assertEqual(len(matches), 3)

        marseille_match = next(match for match in matches if match["team1_name"] == "Marseille")
        self.assertEqual(marseille_match["actual_result"], "DRAW")
        self.assertEqual(marseille_match["season_label"], "2025/26")
        self.assertEqual(marseille_match["provider_name"], "football-data.co.uk")

        lines = await db.get_historical_market_lines(marseille_match["id"])
        self.assertEqual(len(lines), 2)
        open_line = next(line for line in lines if line["snapshot_kind"] == "OPEN")
        close_line = next(line for line in lines if line["snapshot_kind"] == "CLOSE")
        self.assertAlmostEqual(open_line["odds_w1"], 2.45, places=2)
        self.assertAlmostEqual(close_line["odds_w1"], 2.52, places=2)
        self.assertAlmostEqual(close_line["odds_over_2_5"], 1.89, places=2)
        self.assertEqual(close_line["ah_main_line"], "Marseille -0.25")
        self.assertIsNotNone(close_line["odds_dnb_team1"])
        self.assertIsNotNone(close_line["odds_dc_1x"])

        snapshot = await db.get_historical_team_snapshot(marseille_match["id"])
        assert snapshot is not None
        self.assertEqual(snapshot["snapshot_source"], "football_data_csv_rolling")
        self.assertEqual(snapshot["team1_points"], 0)
        self.assertEqual(snapshot["team2_points"], 6)
        self.assertAlmostEqual(snapshot["team2_form_points_last5"], 6.0)
        self.assertEqual(snapshot["team1_table_position"], 2)
        self.assertEqual(snapshot["team2_table_position"], 1)
        self.assertAlmostEqual(snapshot["team1_shots_avg"], 8.0)
        self.assertAlmostEqual(snapshot["team2_shots_avg"], 9.5)
        self.assertAlmostEqual(snapshot["team1_shots_on_target_avg"], 2.0)
        self.assertAlmostEqual(snapshot["team2_shots_on_target_avg"], 4.5)
        self.assertAlmostEqual(snapshot["team1_corners_avg"], 3.0)
        self.assertAlmostEqual(snapshot["team2_corners_avg"], 6.5)
        self.assertIsNotNone(snapshot["rest_gap_hours"])
        self.assertIs(snapshot["team1_short_rest"], False)
        self.assertIs(snapshot["team2_short_rest"], False)

        coverage = await db.get_historical_provider_coverage(marseille_match["id"])
        self.assertEqual(len(coverage), 1)
        self.assertEqual(coverage[0]["status"], "OK")
        self.assertEqual(coverage[0]["source_key"], "football_data_csv")

        backtest_rows = await db.get_historical_backtest_rows(limit=10)
        self.assertEqual(len(backtest_rows), 3)
        with_snapshot = next(row for row in backtest_rows if row["team1"] == "Marseille")
        self.assertAlmostEqual(with_snapshot["form_points_last5_diff"], -6.0)
        self.assertAlmostEqual(with_snapshot["standings_point_gap"], -6.0)
        self.assertAlmostEqual(with_snapshot["shots_avg_diff"], -1.5)
        self.assertAlmostEqual(with_snapshot["shots_on_target_avg_diff"], -2.5)
        self.assertAlmostEqual(with_snapshot["corners_avg_diff"], -3.5)
        self.assertAlmostEqual(with_snapshot["shots_balance_diff"], -2.0)
        self.assertAlmostEqual(with_snapshot["shots_on_target_balance_diff"], -4.0)
        self.assertAlmostEqual(with_snapshot["corners_balance_diff"], -6.0)
        self.assertIsNotNone(with_snapshot["market_prob_w1"])

    async def test_importer_dry_run_skips_db_writes(self):
        csv_path = Path(self._tmp_dir) / "F1.csv"
        _write_csv(
            csv_path,
            """
            Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,AvgH,AvgD,AvgA
            F1,01/08/2025,19:45,Rennes,Marseille,1,0,H,3.20,3.40,2.20
            """,
        )

        importer = FootballDataHistoricalImporter(csv_path=csv_path, dry_run=True)
        summary = await importer.run()

        self.assertEqual(summary.rows_processed, 1)
        self.assertEqual(summary.match_rows_inserted, 1)
        self.assertEqual(summary.market_rows_inserted, 1)
        self.assertIsNone(summary.run_id)
        self.assertEqual(summary.division_code, "F1")
        self.assertEqual(summary.betexplorer_league_key, "fra.1")

        matches = await db.get_historical_matches_for_range("2025-08-01", "2025-08-01")
        self.assertEqual(matches, [])
