from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date

import database as db
from historical_importers.betexplorer import (
    BetexplorerHistoricalEnricher,
    _build_results_paths_for_range,
    _assign_derived_markets,
    _parse_results_date,
    parse_results_page,
)


def _sample_results_html() -> str:
    return """
    <html>
      <body>
        <table class="table-main">
          <tr>
            <th>Match</th>
          </tr>
          <tr>
            <td class="h-text-left">
              <a class="in-match" href="/football/portugal/liga-portugal/tondela-rio-ave/dvvFSE8c/">
                <span>Tondela</span> - <span>Rio Ave</span>
              </a>
            </td>
            <td class="h-text-center">
              <a href="/football/portugal/liga-portugal/tondela-rio-ave/dvvFSE8c/">2:1</a>
            </td>
            <td class="table-main__odds" data-odd="2.10"></td>
            <td class="table-main__odds"><span data-odd="3.30"></span></td>
            <td class="table-main__odds" data-odd="3.70"></td>
            <td class="h-text-right h-text-no-wrap">01.03.</td>
          </tr>
        </table>
      </body>
    </html>
    """


def _sample_ou_json() -> str:
    return json.dumps(
        {
            "odds": """
            <table data-handicap="1.50">
              <tr><td data-odd="1.31"></td><td data-odd="3.35"></td></tr>
            </table>
            <table data-handicap="2.50">
              <tr><td data-odd="1.86"></td><td data-odd="1.97"></td></tr>
            </table>
            <table data-handicap="3.50">
              <tr><td data-odd="2.95"></td><td data-odd="1.38"></td></tr>
            </table>
            """,
        }
    )


def _sample_bts_json() -> str:
    return json.dumps(
        {
            "odds": """
            <table>
              <tr><td data-odd="1.78"></td><td data-odd="2.02"></td></tr>
            </table>
            """,
        }
    )


def _sample_ah_json() -> str:
    return json.dumps(
        {
            "odds": """
            <table data-handicap="-0.25">
              <tr><td data-odd="1.91"></td><td data-odd="1.95"></td></tr>
              <tr><td data-odd="1.89"></td><td data-odd="1.96"></td></tr>
            </table>
            """,
        }
    )


class BetexplorerHistoricalImporterTests(unittest.IsolatedAsyncioTestCase):
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

    def test_parse_results_date_handles_relative_and_implicit_year(self):
        self.assertEqual(
            _parse_results_date("Yesterday", reference_date=date(2026, 3, 10)),
            "2026-03-09",
        )
        self.assertEqual(
            _parse_results_date("01.03.", reference_date=date(2026, 3, 10)),
            "2026-03-01",
        )
        self.assertEqual(
            _parse_results_date("31.12.", reference_date=date(2026, 1, 5)),
            "2025-12-31",
        )

    def test_assign_derived_markets_populates_dnb_and_double_chance(self):
        payload = {"odds_w1": 2.10, "odds_draw": 3.30, "odds_w2": 3.70}
        _assign_derived_markets(payload)

        self.assertIsNotNone(payload["implied_prob_w1"])
        self.assertIsNotNone(payload["implied_prob_draw"])
        self.assertIsNotNone(payload["implied_prob_w2"])
        self.assertIsNotNone(payload["odds_dnb_team1"])
        self.assertIsNotNone(payload["odds_dnb_team2"])
        self.assertIsNotNone(payload["odds_dc_1x"])
        self.assertIsNotNone(payload["odds_dc_12"])
        self.assertIsNotNone(payload["odds_dc_x2"])

    def test_build_results_paths_for_range_includes_archive_seasons(self):
        paths = _build_results_paths_for_range(
            "/football/england/premier-league/results/",
            date_from=date(2024, 8, 1),
            date_to=date(2025, 5, 31),
            reference_date=date(2026, 3, 15),
        )
        self.assertEqual(
            paths,
            [
                "/football/england/premier-league/results/",
                "/football/england/premier-league-2024-2025/results/",
            ],
        )

    def test_parse_results_page_extracts_match_row(self):
        rows = parse_results_page(_sample_results_html(), reference_date=date(2026, 3, 10))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["match_date_utc"], "2026-03-01")
        self.assertEqual(row["home"], "Tondela")
        self.assertEqual(row["away"], "Rio Ave")
        self.assertEqual(row["score_home"], 2)
        self.assertEqual(row["score_away"], 1)
        self.assertEqual(row["odds_w1"], 2.10)
        self.assertEqual(row["odds_draw"], 3.30)
        self.assertEqual(row["odds_w2"], 3.70)
        self.assertTrue(row["href"].endswith("/dvvFSE8c/"))

    async def test_fetch_results_rows_reads_archive_results_page_for_old_ranges(self):
        async def fake_fetcher(url: str) -> str:
            if url.endswith("/premier-league/results/"):
                return "<html><body><table class='table-main'></table></body></html>"
            if url.endswith("/premier-league-2024-2025/results/"):
                return _sample_results_html().replace("01.03.", "24.08.2024").replace("Tondela", "Arsenal").replace("Rio Ave", "Wolves")
            raise AssertionError(f"Unexpected URL: {url}")

        enricher = BetexplorerHistoricalEnricher(
            league_key="eng.1",
            date_from=date(2024, 8, 24),
            date_to=date(2024, 8, 24),
            fetcher=fake_fetcher,
            dry_run=True,
            reference_date=date(2026, 3, 15),
        )

        rows = await enricher._fetch_results_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["match_date_utc"], "2024-08-24")
        self.assertEqual(rows[0]["home"], "Arsenal")
        self.assertEqual(rows[0]["away"], "Wolves")

    async def test_enricher_persists_close_market_line_and_provider_coverage(self):
        historical_match_id = await db.upsert_historical_match(
            {
                "provider_match_key": "espn-1",
                "provider_name": "espn",
                "competition_name": "Primeira Liga",
                "match_date_utc": "2026-03-01",
                "kickoff_utc": "2026-03-01T17:00:00+00:00",
                "team1_name": "Tondela",
                "team2_name": "Rio Ave",
                "team1_normalized": "tondela",
                "team2_normalized": "rio ave",
                "team1_score": 2,
                "team2_score": 1,
                "actual_result": "W1",
                "status": "FINISHED",
            }
        )

        async def fake_fetcher(url: str) -> str:
            if url.endswith("/results/"):
                return _sample_results_html()
            if "/match-odds/" in url and "/ou/" in url:
                return _sample_ou_json()
            if "/match-odds/" in url and "/bts/" in url:
                return _sample_bts_json()
            if "/match-odds/" in url and "/ah/" in url:
                return _sample_ah_json()
            raise AssertionError(f"Unexpected URL: {url}")

        enricher = BetexplorerHistoricalEnricher(
            league_key="por.1",
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 1),
            fetcher=fake_fetcher,
            reference_date=date(2026, 3, 10),
        )
        summary = await enricher.run()

        self.assertEqual(summary.candidate_matches, 1)
        self.assertEqual(summary.matched_rows, 1)
        self.assertEqual(summary.market_rows_inserted, 1)
        self.assertEqual(summary.market_rows_updated, 0)
        self.assertEqual(summary.unmatched_rows, 0)
        self.assertEqual(summary.odds_fetch_failures, 0)
        self.assertIsNotNone(summary.run_id)

        lines = await db.get_historical_market_lines(historical_match_id)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["snapshot_kind"], "CLOSE")
        self.assertEqual(line["source_provider"], "betexplorer:historical-close")
        self.assertAlmostEqual(line["odds_w1"], 2.10, places=2)
        self.assertAlmostEqual(line["odds_draw"], 3.30, places=2)
        self.assertAlmostEqual(line["odds_w2"], 3.70, places=2)
        self.assertAlmostEqual(line["odds_over_2_5"], 1.86, places=2)
        self.assertAlmostEqual(line["odds_under_2_5"], 1.97, places=2)
        self.assertAlmostEqual(line["odds_btts_yes"], 1.78, places=2)
        self.assertAlmostEqual(line["odds_btts_no"], 2.02, places=2)
        self.assertEqual(line["ah_main_line"], "Tondela -0.25")
        self.assertAlmostEqual(line["ah_team1_odds"], 1.91, places=2)
        self.assertAlmostEqual(line["ah_team2_odds"], 1.96, places=2)
        self.assertIsNotNone(line["odds_dnb_team1"])
        self.assertIsNotNone(line["odds_dnb_team2"])
        self.assertIsNotNone(line["odds_dc_1x"])
        self.assertIsNotNone(line["odds_dc_12"])
        self.assertIsNotNone(line["odds_dc_x2"])

        coverage_rows = await db.get_historical_provider_coverage(historical_match_id)
        self.assertEqual(len(coverage_rows), 1)
        self.assertEqual(coverage_rows[0]["status"], "OK")
        self.assertEqual(coverage_rows[0]["source_key"], "betexplorer_historical_close")

        runs = await db.get_recent_historical_import_runs(limit=5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["provider_name"], "betexplorer")
        self.assertEqual(runs[0]["status"], "SUCCESS")

    async def test_enricher_dry_run_skips_writes(self):
        await db.upsert_historical_match(
            {
                "provider_match_key": "espn-1",
                "provider_name": "espn",
                "competition_name": "Primeira Liga",
                "match_date_utc": "2026-03-01",
                "kickoff_utc": "2026-03-01T17:00:00+00:00",
                "team1_name": "Tondela",
                "team2_name": "Rio Ave",
                "team1_normalized": "tondela",
                "team2_normalized": "rio ave",
                "team1_score": 2,
                "team2_score": 1,
                "actual_result": "W1",
                "status": "FINISHED",
            }
        )

        async def fake_fetcher(url: str) -> str:
            if url.endswith("/results/"):
                return _sample_results_html()
            if "/ou/" in url:
                return _sample_ou_json()
            if "/bts/" in url:
                return _sample_bts_json()
            if "/ah/" in url:
                return _sample_ah_json()
            raise AssertionError(f"Unexpected URL: {url}")

        enricher = BetexplorerHistoricalEnricher(
            league_key="por.1",
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 1),
            fetcher=fake_fetcher,
            dry_run=True,
            reference_date=date(2026, 3, 10),
        )
        summary = await enricher.run()

        self.assertEqual(summary.market_rows_inserted, 1)
        self.assertIsNone(summary.run_id)

        matches = await db.get_historical_matches_for_range("2026-03-01", "2026-03-01")
        self.assertEqual(len(matches), 1)
        lines = await db.get_historical_market_lines(matches[0]["id"])
        self.assertEqual(lines, [])
        runs = await db.get_recent_historical_import_runs(limit=5)
        self.assertEqual(runs, [])
