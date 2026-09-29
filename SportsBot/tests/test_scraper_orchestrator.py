from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import scrapers
from models import FlashscoreData, SofascoreData
from scrapers.registry import ScraperSpec


class _GoodScraper:
    def __init__(self, team1: str, team2: str) -> None:
        self.team1 = team1
        self.team2 = team2

    async def scrape(self) -> FlashscoreData:
        await asyncio.sleep(0)
        return FlashscoreData(
            team1_name=self.team1,
            team2_name=self.team2,
            scrape_success=True,
        )


class _FailedScraper:
    def __init__(self, team1: str, team2: str) -> None:
        self.team1 = team1
        self.team2 = team2

    async def scrape(self) -> SofascoreData:
        await asyncio.sleep(0)
        return SofascoreData(
            team1_name=self.team1,
            team2_name=self.team2,
            scrape_success=False,
            error_message="lineups unavailable",
        )


class _CrashScraper:
    def __init__(self, team1: str, team2: str) -> None:
        self.team1 = team1
        self.team2 = team2

    async def scrape(self):
        await asyncio.sleep(0)
        raise TimeoutError("network timeout")


class _ClosableScraper:
    closed = 0

    def __init__(self, team1: str, team2: str) -> None:
        self.team1 = team1
        self.team2 = team2

    async def scrape(self) -> FlashscoreData:
        await asyncio.sleep(0)
        return FlashscoreData(
            team1_name=self.team1,
            team2_name=self.team2,
            scrape_success=True,
        )

    async def _close(self) -> None:
        type(self).closed += 1


class _ClosableCrashScraper:
    closed = 0

    def __init__(self, team1: str, team2: str) -> None:
        self.team1 = team1
        self.team2 = team2

    async def scrape(self):
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    async def _close(self) -> None:
        type(self).closed += 1


class ScraperOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        scrapers.reset_scrape_runtime_metrics()
        _ClosableScraper.closed = 0
        _ClosableCrashScraper.closed = 0

    async def test_orchestrator_tracks_progress_errors_and_metrics(self):
        statuses = []

        async def on_progress(status):
            statuses.append(status)

        registry = (
            ScraperSpec(
                key="fotmob_form",
                label="Form/H2H",
                field_name="flashscore",
                scraper_cls=_GoodScraper,
                provider="Fotmob",
                icon="📊",
            ),
            ScraperSpec(
                key="fotmob_squad",
                label="Squads",
                field_name="sofascore",
                scraper_cls=_FailedScraper,
                provider="Fotmob",
                icon="👥",
            ),
            ScraperSpec(
                key="transfermarkt",
                label="Values",
                field_name="transfermarkt",
                scraper_cls=_CrashScraper,
                provider="Transfermarkt",
                icon="💰",
            ),
        )

        with patch.object(scrapers, "SCRAPER_REGISTRY", registry):
            data = await scrapers.scrape_all_parallel("Arsenal", "Chelsea", on_progress=on_progress)

        self.assertEqual(data.total_source_count, 3)
        self.assertEqual(len(statuses), 3)

        status_map = {status.key: status for status in data.resolved_source_statuses}
        self.assertTrue(status_map["fotmob_form"].scrape_success)
        self.assertFalse(status_map["fotmob_squad"].scrape_success)
        self.assertEqual(status_map["fotmob_squad"].error_message, "lineups unavailable")
        self.assertIsNone(data.transfermarkt)
        self.assertEqual(status_map["transfermarkt"].exception_type, "TimeoutError")
        self.assertIn("network timeout", status_map["transfermarkt"].error_message or "")
        self.assertTrue(all(status.duration_ms is not None for status in statuses))

        metrics = scrapers.get_scrape_metrics_snapshot()
        self.assertEqual(metrics["fotmob_form"]["successes"], 1)
        self.assertEqual(metrics["fotmob_squad"]["failures"], 1)
        self.assertEqual(metrics["transfermarkt"]["failures"], 1)
        self.assertEqual(metrics["transfermarkt"]["last_exception_type"], "TimeoutError")

    async def test_orchestrator_closes_scraper_sessions_on_success_and_failure(self):
        registry = (
            ScraperSpec(
                key="good",
                label="Good",
                field_name="flashscore",
                scraper_cls=_ClosableScraper,
                provider="Test",
                icon="A",
            ),
            ScraperSpec(
                key="bad",
                label="Bad",
                field_name="sofascore",
                scraper_cls=_ClosableCrashScraper,
                provider="Test",
                icon="B",
            ),
        )

        with patch.object(scrapers, "SCRAPER_REGISTRY", registry):
            data = await scrapers.scrape_all_parallel("Arsenal", "Chelsea")

        self.assertEqual(data.total_source_count, 2)
        self.assertEqual(_ClosableScraper.closed, 1)
        self.assertEqual(_ClosableCrashScraper.closed, 1)


if __name__ == "__main__":
    unittest.main()
