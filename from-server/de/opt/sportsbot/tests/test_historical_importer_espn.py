from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date

import database as db
from historical_importers.espn import (
    ESPNHistoricalImporter,
    _american_to_decimal,
    _assign_derived_moneyline_markets,
    build_match_payload,
    build_team_snapshot_payload,
)


def _sample_scoreboard_event() -> dict:
    return {
        "id": "750470",
        "date": "2026-03-09T17:30:00Z",
        "season": {"year": 2025, "slug": "2025-26-primeira-liga"},
        "competitions": [
            {
                "date": "2026-03-09T17:30:00Z",
                "details": "Regular Season",
                "neutralSite": False,
                "venue": {"fullName": "Estadio Joao Cardoso"},
                "status": {
                    "type": {
                        "completed": True,
                        "shortDetail": "FT",
                        "detail": "Full Time",
                        "description": "Full Time",
                    }
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "form": "WLWWL",
                        "score": "1",
                        "records": [
                            {"name": "All Splits", "type": "total", "summary": "17-3-7", "abbreviation": "Total"}
                        ],
                        "team": {"displayName": "Tondela"},
                    },
                    {
                        "homeAway": "away",
                        "form": "LDLDL",
                        "score": "0",
                        "records": [
                            {"name": "All Splits", "type": "total", "summary": "8-2-17", "abbreviation": "Total"}
                        ],
                        "team": {"displayName": "Rio Ave"},
                    },
                ],
                "odds": [
                    {
                        "provider": {"displayName": "Draft Kings"},
                        "moneyline": {
                            "home": {"open": {"odds": "-105"}, "close": {"odds": "+115"}},
                            "draw": {"open": {"odds": "+225"}, "close": {"odds": "+220"}},
                            "away": {"open": {"odds": "+275"}, "close": {"odds": "+255"}},
                        },
                        "total": {
                            "over": {"open": {"line": "o2.5", "odds": "-105"}, "close": {"line": "o2.5", "odds": "+105"}},
                            "under": {"open": {"line": "u2.5", "odds": "-140"}, "close": {"line": "u2.5", "odds": "-140"}},
                        },
                        "pointSpread": {
                            "home": {"open": {"line": "-0.5", "odds": "-110"}, "close": {"line": "-0.5", "odds": "+105"}},
                            "away": {"open": {"line": "+0.5", "odds": "-135"}, "close": {"line": "+0.5", "odds": "-150"}},
                        },
                    }
                ],
            }
        ],
    }


def _sample_scoreboard_payload() -> dict:
    return {
        "leagues": [{"name": "Primeira Liga"}],
        "events": [_sample_scoreboard_event()],
    }


class ESPNHistoricalImporterTest(unittest.IsolatedAsyncioTestCase):
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

    def test_american_to_decimal(self):
        self.assertAlmostEqual(_american_to_decimal("+115"), 2.15)
        self.assertAlmostEqual(_american_to_decimal(-105), 1.9524, places=4)
        self.assertIsNone(_american_to_decimal(None))

    def test_build_match_payload(self):
        payload = build_match_payload(_sample_scoreboard_event(), competition_name="Primeira Liga")
        assert payload is not None
        self.assertEqual(payload["provider_match_key"], "750470")
        self.assertEqual(payload["team1_name"], "Tondela")
        self.assertEqual(payload["team2_name"], "Rio Ave")
        self.assertEqual(payload["actual_result"], "W1")
        self.assertEqual(payload["venue_name"], "Estadio Joao Cardoso")
        self.assertFalse(payload["is_neutral_venue"])

    def test_build_match_payload_serializes_non_scalar_details(self):
        event = _sample_scoreboard_event()
        event["competitions"][0]["details"] = [{"note": "Championship Round"}]
        payload = build_match_payload(event, competition_name="Primeira Liga")
        assert payload is not None
        self.assertEqual(payload["stage_name"], '[{"note": "Championship Round"}]')

    def test_build_team_snapshot_payload(self):
        payload = build_team_snapshot_payload(_sample_scoreboard_event(), competition_name="Primeira Liga")
        assert payload is not None
        self.assertEqual(payload["snapshot_source"], "espn_scoreboard_basic")
        self.assertEqual(payload["team1_form_points_last5"], 9.0)
        self.assertEqual(payload["team2_form_points_last5"], 2.0)
        self.assertEqual(payload["team1_points"], 54)
        self.assertEqual(payload["team2_points"], 26)
        self.assertAlmostEqual(payload["data_completeness"], 1.0)

    def test_assign_derived_moneyline_markets_populates_dnb_and_double_chance(self):
        payload: dict = {}
        _assign_derived_moneyline_markets(
            payload,
            implied_w1=45.0,
            implied_draw=25.0,
            implied_w2=30.0,
        )

        self.assertAlmostEqual(payload["odds_dnb_team1"], 1.6667, places=4)
        self.assertAlmostEqual(payload["odds_dnb_team2"], 2.5, places=4)
        self.assertAlmostEqual(payload["odds_dc_1x"], 1.4286, places=4)
        self.assertAlmostEqual(payload["odds_dc_12"], 1.3333, places=4)
        self.assertAlmostEqual(payload["odds_dc_x2"], 1.8182, places=4)

    async def test_importer_persists_matches_market_lines_and_run(self):
        async def fake_fetcher(_date_str: str) -> dict:
            return _sample_scoreboard_payload()

        importer = ESPNHistoricalImporter(
            league_slug="por.1",
            date_from=date(2026, 3, 9),
            date_to=date(2026, 3, 9),
            fetcher=fake_fetcher,
        )
        summary = await importer.run()

        self.assertEqual(summary.days_processed, 1)
        self.assertEqual(summary.failed_days, 0)
        self.assertEqual(summary.match_rows_inserted, 1)
        self.assertEqual(summary.snapshot_rows_inserted, 1)
        self.assertEqual(summary.market_rows_inserted, 2)
        self.assertIsNotNone(summary.run_id)

        match = await db.get_historical_match_by_provider_key("espn", "750470")
        assert match is not None
        self.assertEqual(match["competition_name"], "Primeira Liga")
        self.assertEqual(match["actual_result"], "W1")

        lines = await db.get_historical_market_lines(match["id"])
        self.assertEqual(len(lines), 2)
        close_line = next(line for line in lines if line["snapshot_kind"] == "CLOSE")
        self.assertEqual(close_line["source_provider"], "espn:draft-kings")
        self.assertAlmostEqual(close_line["odds_w1"], 2.15, places=2)
        self.assertAlmostEqual(close_line["odds_under_2_5"], 1.7143, places=4)
        self.assertIsNotNone(close_line["odds_dnb_team1"])
        self.assertIsNotNone(close_line["odds_dnb_team2"])
        self.assertIsNotNone(close_line["odds_dc_1x"])
        self.assertIsNotNone(close_line["odds_dc_12"])
        self.assertIsNotNone(close_line["odds_dc_x2"])
        self.assertEqual(close_line["ah_main_line"], "Tondela -0.5")

        snapshot = await db.get_historical_team_snapshot(match["id"])
        assert snapshot is not None
        self.assertEqual(snapshot["snapshot_source"], "espn_scoreboard_basic")
        self.assertEqual(snapshot["team1_points"], 54)
        self.assertEqual(snapshot["team2_points"], 26)
        self.assertEqual(snapshot["team1_form_points_last5"], 9.0)
        self.assertEqual(snapshot["team2_form_points_last5"], 2.0)

        runs = await db.get_recent_historical_import_runs(limit=5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "SUCCESS")

    async def test_importer_dry_run_skips_db_writes(self):
        async def fake_fetcher(_date_str: str) -> dict:
            return _sample_scoreboard_payload()

        importer = ESPNHistoricalImporter(
            league_slug="por.1",
            date_from=date(2026, 3, 9),
            date_to=date(2026, 3, 9),
            fetcher=fake_fetcher,
            dry_run=True,
        )
        summary = await importer.run()

        self.assertEqual(summary.match_rows_inserted, 1)
        self.assertEqual(summary.snapshot_rows_inserted, 1)
        self.assertEqual(summary.market_rows_inserted, 2)
        self.assertIsNone(summary.run_id)
        self.assertIsNone(await db.get_historical_match_by_provider_key("espn", "750470"))
