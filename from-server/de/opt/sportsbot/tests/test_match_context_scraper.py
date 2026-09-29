from __future__ import annotations

import unittest
from unittest.mock import patch

from scrapers.fotmob import ResolvedFotmobTeam
from scrapers.match_context import MatchContextScraper


def _resolved_team(name: str, team_id: int, data: dict) -> ResolvedFotmobTeam:
    return ResolvedFotmobTeam(
        requested_name=name,
        search_term=name,
        team_id=team_id,
        data=data,
    )


class MatchContextScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_extracts_upcoming_fixture_context(self):
        fixture = {
            "status": {"finished": False, "utcTime": "2026-03-10T20:00:00Z"},
            "home": {"id": 1, "name": "Arsenal"},
            "away": {"id": 2, "name": "Chelsea"},
            "opponent": {"id": 2, "name": "Chelsea"},
            "tournament": {"name": "Champions League"},
            "venue": {"name": "Emirates Stadium"},
            "roundName": "Semi-final second leg",
            "isNeutralGround": False,
        }
        data1 = {
            "details": {"name": "Arsenal"},
            "fixtures": {"allFixtures": {"fixtures": [fixture]}},
        }
        data2 = {
            "details": {"name": "Chelsea"},
            "fixtures": {"allFixtures": {"fixtures": []}},
        }

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with patch("scrapers.match_context.resolve_fotmob_team", side_effect=fake_resolve):
            result = await MatchContextScraper("Arsenal", "Chelsea").scrape()

        self.assertTrue(result.scrape_success)
        self.assertEqual(result.competition_name, "Champions League")
        self.assertEqual(result.kickoff_utc, "2026-03-10T20:00:00Z")
        self.assertEqual(result.venue_name, "Emirates Stadium")
        self.assertTrue(result.is_team1_home)
        self.assertFalse(result.is_neutral_venue)
        self.assertTrue(result.is_knockout)
        self.assertTrue(result.is_second_leg)
        self.assertEqual(result.stage, "Semi-final second leg")

    async def test_scrape_returns_failed_model_when_fixture_missing(self):
        data1 = {
            "details": {"name": "Arsenal"},
            "fixtures": {"allFixtures": {"fixtures": []}},
        }
        data2 = {
            "details": {"name": "Chelsea"},
            "fixtures": {"allFixtures": {"fixtures": []}},
        }

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with patch("scrapers.match_context.resolve_fotmob_team", side_effect=fake_resolve):
            result = await MatchContextScraper("Arsenal", "Chelsea").scrape()

        self.assertFalse(result.scrape_success)
        self.assertIn("Upcoming fixture not found", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
