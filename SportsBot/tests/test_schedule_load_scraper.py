from __future__ import annotations

import unittest
from unittest.mock import patch

from scrapers.fotmob import ResolvedFotmobTeam
from scrapers.schedule_load import ScheduleLoadScraper


def _resolved_team(name: str, team_id: int, data: dict) -> ResolvedFotmobTeam:
    return ResolvedFotmobTeam(
        requested_name=name,
        search_term=name,
        team_id=team_id,
        data=data,
    )


class ScheduleLoadScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_extracts_rest_and_congestion_signals(self):
        target_fixture = {
            "status": {"finished": False, "utcTime": "2026-03-10T20:00:00Z"},
            "home": {"id": 1, "name": "Arsenal"},
            "away": {"id": 2, "name": "Chelsea"},
            "opponent": {"id": 2, "name": "Chelsea"},
        }
        arsenal_data = {
            "details": {"name": "Arsenal"},
            "fixtures": {
                "allFixtures": {
                    "fixtures": [
                        {
                            "status": {"finished": True, "utcTime": "2026-03-07T17:00:00Z"},
                            "home": {"id": 1, "name": "Arsenal"},
                            "away": {"id": 11, "name": "Everton"},
                            "opponent": {"id": 11, "name": "Everton"},
                        },
                        {
                            "status": {"finished": True, "utcTime": "2026-03-03T19:00:00Z"},
                            "home": {"id": 12, "name": "PSV"},
                            "away": {"id": 1, "name": "Arsenal"},
                            "opponent": {"id": 12, "name": "PSV"},
                        },
                        target_fixture,
                        {
                            "status": {"finished": False, "utcTime": "2026-03-14T16:00:00Z"},
                            "home": {"id": 4, "name": "Tottenham"},
                            "away": {"id": 1, "name": "Arsenal"},
                            "opponent": {"id": 4, "name": "Tottenham"},
                        },
                    ]
                }
            },
        }
        chelsea_data = {
            "details": {"name": "Chelsea"},
            "fixtures": {
                "allFixtures": {
                    "fixtures": [
                        {
                            "status": {"finished": True, "utcTime": "2026-03-09T20:00:00Z"},
                            "home": {"id": 2, "name": "Chelsea"},
                            "away": {"id": 21, "name": "Liverpool"},
                            "opponent": {"id": 21, "name": "Liverpool"},
                        },
                        {
                            "status": {"finished": True, "utcTime": "2026-03-05T19:00:00Z"},
                            "home": {"id": 2, "name": "Chelsea"},
                            "away": {"id": 22, "name": "Leeds"},
                            "opponent": {"id": 22, "name": "Leeds"},
                        },
                        {
                            "status": {"finished": True, "utcTime": "2026-03-02T19:00:00Z"},
                            "home": {"id": 23, "name": "Brighton"},
                            "away": {"id": 2, "name": "Chelsea"},
                            "opponent": {"id": 23, "name": "Brighton"},
                        },
                        {
                            "status": {"finished": False, "utcTime": "2026-03-12T18:00:00Z"},
                            "home": {"id": 2, "name": "Chelsea"},
                            "away": {"id": 24, "name": "Newcastle"},
                            "opponent": {"id": 24, "name": "Newcastle"},
                        },
                    ]
                }
            },
        }

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, arsenal_data), ["Arsenal"]
            return _resolved_team("Chelsea", 2, chelsea_data), ["Chelsea"]

        with patch("scrapers.schedule_load.resolve_fotmob_team", side_effect=fake_resolve):
            result = await ScheduleLoadScraper("Arsenal", "Chelsea").scrape()

        self.assertTrue(result.scrape_success)
        self.assertAlmostEqual(result.team1.rest_hours or 0.0, 75.0)
        self.assertAlmostEqual(result.team2.rest_hours or 0.0, 24.0)
        self.assertEqual(result.team1.matches_last_7d, 1)
        self.assertEqual(result.team2.matches_last_7d, 2)
        self.assertEqual(result.team1.next_opponent, "Tottenham")
        self.assertEqual(result.team2.next_opponent, "Newcastle")
        self.assertAlmostEqual(result.rest_gap_hours or 0.0, 51.0)
        self.assertTrue(result.team2.short_rest)
        self.assertTrue(any("Chelsea on short rest" in warning for warning in result.load_warnings))
        self.assertTrue(any("Arsenal has about 51h more rest" in warning for warning in result.load_warnings))

    async def test_scrape_returns_failed_model_when_fixture_missing(self):
        arsenal_data = {
            "details": {"name": "Arsenal"},
            "fixtures": {"allFixtures": {"fixtures": []}},
        }
        chelsea_data = {
            "details": {"name": "Chelsea"},
            "fixtures": {"allFixtures": {"fixtures": []}},
        }

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, arsenal_data), ["Arsenal"]
            return _resolved_team("Chelsea", 2, chelsea_data), ["Chelsea"]

        with patch("scrapers.schedule_load.resolve_fotmob_team", side_effect=fake_resolve):
            result = await ScheduleLoadScraper("Arsenal", "Chelsea").scrape()

        self.assertFalse(result.scrape_success)
        self.assertIn("Upcoming fixture not found", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
