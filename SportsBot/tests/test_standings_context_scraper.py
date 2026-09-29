from __future__ import annotations

import unittest
from unittest.mock import patch

from scrapers.fotmob import ResolvedFotmobTeam
from scrapers.standings_context import StandingsContextScraper


def _resolved_team(name: str, team_id: int, data: dict) -> ResolvedFotmobTeam:
    return ResolvedFotmobTeam(
        requested_name=name,
        search_term=name,
        team_id=team_id,
        data=data,
    )


def _table_row(team_id: int, name: str, position: int, points: int, goal_diff: int, *, qual_color: str | None = None) -> dict:
    return {
        "id": team_id,
        "name": name,
        "idx": position,
        "played": 30,
        "pts": points,
        "goalConDiff": goal_diff,
        "scoresStr": f"{points}-{points - goal_diff}",
        "qualColor": qual_color,
    }


class StandingsContextScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_extracts_standings_and_split_context(self):
        standings_table = [
            {
                "data": {
                    "leagueName": "Premier League",
                    "legend": [
                        {"title": "Champions League", "indices": [0, 1, 2, 3], "color": "#2AD572"},
                        {"title": "Europa League", "indices": [4], "color": "#0046A7"},
                    ],
                    "table": {
                        "all": [
                            _table_row(1, "Arsenal", 1, 67, 37, qual_color="#2AD572"),
                            _table_row(2, "Chelsea", 5, 48, 19, qual_color="#0046A7"),
                        ],
                        "home": [
                            _table_row(1, "Arsenal", 2, 35, 24),
                            _table_row(2, "Chelsea", 8, 24, 3),
                        ],
                        "away": [
                            _table_row(1, "Arsenal", 3, 32, 13),
                            _table_row(2, "Chelsea", 6, 24, 16),
                        ],
                    },
                }
            }
        ]
        data1 = {"details": {"name": "Arsenal"}, "overview": {"table": standings_table}}
        data2 = {"details": {"name": "Chelsea"}, "overview": {"table": standings_table}}

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with patch("scrapers.standings_context.resolve_fotmob_team", side_effect=fake_resolve):
            result = await StandingsContextScraper("Arsenal", "Chelsea").scrape()

        self.assertTrue(result.scrape_success)
        self.assertEqual(result.league_name, "Premier League")
        self.assertEqual(result.team1_overall.position, 1)
        self.assertEqual(result.team2_overall.position, 5)
        self.assertEqual(result.point_gap, 19)
        self.assertEqual(result.position_gap, 4)
        self.assertTrue(result.team1_above_team2)
        self.assertEqual(result.team1_overall.qualification_label, "Champions League")
        self.assertEqual(result.team2_overall.qualification_label, "Europa League")
        self.assertEqual(result.team1_home.position, 2)
        self.assertEqual(result.team2_away.position, 6)

    async def test_scrape_fails_when_shared_table_missing(self):
        data1 = {"details": {"name": "Arsenal"}, "overview": {"table": []}}
        data2 = {"details": {"name": "Chelsea"}, "overview": {"table": []}}

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with patch("scrapers.standings_context.resolve_fotmob_team", side_effect=fake_resolve):
            result = await StandingsContextScraper("Arsenal", "Chelsea").scrape()

        self.assertFalse(result.scrape_success)
        self.assertIn("shared standings table", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
