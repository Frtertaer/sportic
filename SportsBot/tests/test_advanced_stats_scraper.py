from __future__ import annotations

import unittest
from unittest.mock import patch

from scrapers.advanced_stats import AdvancedStatsScraper
from scrapers.fotmob import ResolvedFotmobTeam


def _resolved_team(name: str, team_id: int, data: dict) -> ResolvedFotmobTeam:
    return ResolvedFotmobTeam(
        requested_name=name,
        search_term=name,
        team_id=team_id,
        data=data,
    )


def _team_stats_payload(team_name: str, *, rating: float, xg: float, xga: float, xg_diff: float) -> dict:
    return {
        "details": {"name": team_name},
        "stats": {
            "teams": [
                {
                    "stat": "rating_team",
                    "participant": {
                        "value": rating,
                        "rank": 2,
                        "stat": {"name": "rating_team"},
                    },
                },
                {
                    "stat": "expected_goals_team",
                    "participant": {
                        "value": xg,
                        "rank": 4,
                        "stat": {"name": "expected_goals_team"},
                    },
                },
                {
                    "stat": "expected_goals_conceded_team",
                    "participant": {
                        "value": xga,
                        "rank": 1,
                        "stat": {"name": "expected_goals_conceded_team"},
                    },
                },
                {
                    "stat": "_xg_diff_team",
                    "participant": {
                        "value": xg_diff,
                        "rank": 1,
                        "stat": {"name": "_xg_diff_team"},
                    },
                },
                {
                    "stat": "possession_percentage_team",
                    "participant": {
                        "value": 56.4,
                        "rank": 4,
                        "stat": {"name": "possession_percentage_team"},
                    },
                },
                {
                    "stat": "ontarget_scoring_att_team",
                    "participant": {
                        "value": 4.9,
                        "rank": 3,
                        "stat": {"name": "ontarget_scoring_att_team"},
                    },
                },
                {
                    "stat": "corner_taken_team",
                    "participant": {
                        "value": 173,
                        "rank": 4,
                        "stat": {"name": "corner_taken_team"},
                    },
                },
            ]
        },
    }


class AdvancedStatsScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_extracts_high_value_team_metrics(self):
        data1 = _team_stats_payload("Arsenal", rating=7.08, xg=50.0, xga=21.1, xg_diff=28.9)
        data2 = _team_stats_payload("Chelsea", rating=6.88, xg=41.2, xga=32.4, xg_diff=8.8)

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with patch("scrapers.advanced_stats.resolve_fotmob_team", side_effect=fake_resolve):
            result = await AdvancedStatsScraper("Arsenal", "Chelsea").scrape()

        self.assertTrue(result.scrape_success)
        self.assertAlmostEqual(result.team1_metrics.xg or 0.0, 50.0)
        self.assertAlmostEqual(result.team1_metrics.xga or 0.0, 21.1)
        self.assertAlmostEqual(result.team1_metrics.xg_diff or 0.0, 28.9)
        self.assertAlmostEqual(result.team1_metrics.possession_pct or 0.0, 56.4)
        self.assertAlmostEqual(result.team2_metrics.rating or 0.0, 6.88)
        self.assertEqual(result.team2_metrics.corners, 173)

    async def test_scrape_fails_when_stats_payload_has_no_useful_metrics(self):
        data1 = {"details": {"name": "Arsenal"}, "stats": {"teams": []}}
        data2 = {"details": {"name": "Chelsea"}, "stats": {"teams": []}}

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with patch("scrapers.advanced_stats.resolve_fotmob_team", side_effect=fake_resolve):
            result = await AdvancedStatsScraper("Arsenal", "Chelsea").scrape()

        self.assertFalse(result.scrape_success)
        self.assertIn("advanced team stats", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
