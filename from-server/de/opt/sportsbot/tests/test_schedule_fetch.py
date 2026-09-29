from __future__ import annotations

import json
import unittest

from scrapers.fotmob import ESPN_SCHEDULE_LEAGUES, fetch_today_matches


class _FakeScheduleScraper:
    def __init__(self, payloads: dict[str, object], failures: set[str] | None = None) -> None:
        self.payloads = payloads
        self.failures = failures or set()

    async def _fetch_with_retry(self, url: str, *, params=None, headers=None, json_mode=False) -> str:
        league_slug = url.split("/soccer/", 1)[1].split("/scoreboard", 1)[0]
        if league_slug in self.failures:
            raise RuntimeError(f"boom:{league_slug}")
        payload = self.payloads.get(league_slug, {"events": []})
        return json.dumps(payload)

    async def _close(self) -> None:
        return None


def _event(home: str, away: str, kickoff: str, *, completed: bool = False) -> dict:
    return {
        "date": kickoff,
        "competitions": [
            {
                "date": kickoff,
                "status": {"type": {"completed": completed}},
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": home}},
                    {"homeAway": "away", "team": {"displayName": away}},
                ],
            }
        ],
    }


class ScheduleFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_today_matches_parses_espn_scoreboard(self) -> None:
        scraper = _FakeScheduleScraper(
            {
                "esp.1": {
                    "events": [
                        _event("Espanyol", "Real Oviedo", "2026-03-09T20:00:00Z"),
                    ]
                }
            }
        )

        leagues = await fetch_today_matches(scraper=scraper, date_str="20260309")

        self.assertEqual(len(leagues), 1)
        self.assertEqual(leagues[0].league_name, "La Liga")
        self.assertEqual(leagues[0].country_flag, "🇪🇸")
        self.assertEqual(leagues[0].matches[0].home, "Espanyol")
        self.assertEqual(leagues[0].matches[0].away, "Real Oviedo")
        self.assertEqual(leagues[0].matches[0].time_utc, "20:00")

    async def test_fetch_today_matches_tolerates_partial_league_failures(self) -> None:
        scraper = _FakeScheduleScraper(
            {
                "eng.1": {
                    "events": [
                        _event("Arsenal", "Chelsea", "2026-03-09T15:00:00Z"),
                    ]
                }
            },
            failures={"esp.1"},
        )

        leagues = await fetch_today_matches(scraper=scraper, date_str="20260309")

        self.assertEqual(len(leagues), 1)
        self.assertEqual(leagues[0].league_name, ESPN_SCHEDULE_LEAGUES["eng.1"][0])
        self.assertEqual(leagues[0].matches[0].home, "Arsenal")
