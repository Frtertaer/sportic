from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import config
from scrapers.base import BaseScraper, RateLimitError, ScraperError
from scrapers.oddsportal import OddsportalScraper, clear_betexplorer_caches
from scrapers.transfermarkt import TransfermarktScraper


class _DummyScraper(BaseScraper):
    DOMAIN = "example.com"

    async def scrape(self):
        return None


class _TimeoutContext:
    async def __aenter__(self):
        raise asyncio.TimeoutError()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _TimeoutSession:
    def get(self, *args, **kwargs):
        return _TimeoutContext()


class _ResponseContext:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body
        self.headers = {"content-type": "text/html"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self) -> str:
        return self._body


class _ResponseSession:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body

    def get(self, *args, **kwargs):
        return _ResponseContext(self.status, self.body)


class BaseScraperHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_error_is_not_retried(self):
        scraper = _DummyScraper("A", "B")
        calls = 0

        async def fake_fetch(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise RateLimitError("blocked")

        scraper._fetch = fake_fetch  # type: ignore[method-assign]

        with self.assertRaises(RateLimitError):
            await scraper._fetch_with_retry("https://example.com")

        self.assertEqual(calls, 1)

    async def test_timeout_is_wrapped_as_scraper_error(self):
        scraper = _DummyScraper("A", "B")
        scraper._get_session = lambda: asyncio.sleep(0, result=_TimeoutSession())  # type: ignore[method-assign]

        with self.assertRaises(ScraperError):
            await scraper._fetch("https://example.com")

    async def test_failed_http_response_writes_debug_snapshot_when_enabled(self):
        scraper = _DummyScraper("A", "B")
        temp_dir = tempfile.mkdtemp(prefix="scraper_debug_")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        scraper._get_session = lambda: asyncio.sleep(  # type: ignore[method-assign]
            0,
            result=_ResponseSession(500, "<html>boom</html>"),
        )

        with (
            patch.object(config, "SCRAPER_DEBUG_SNAPSHOTS", True),
            patch.object(config, "SCRAPER_DEBUG_SNAPSHOTS_DIR", temp_dir),
            patch.object(config, "SCRAPER_DEBUG_SNAPSHOT_MAX_BYTES", 20000),
        ):
            with self.assertRaises(ScraperError):
                await scraper._fetch("https://example.com/debug")

        snapshot_files = list(sorted(__import__("pathlib").Path(temp_dir).glob("*.json")))
        self.assertEqual(len(snapshot_files), 1)
        payload = json.loads(snapshot_files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], 500)
        self.assertEqual(payload["reason"], "server_error")
        self.assertIn("boom", payload["body"])


class _TransfermarktSearchScraper(TransfermarktScraper):
    def __init__(self, html: str):
        super().__init__("Arsenal", "Chelsea")
        self._html = html

    async def _fetch_with_retry(self, *args, **kwargs):
        return self._html


class _TransfermarktValueScraper(TransfermarktScraper):
    async def _search_team(self, name: str):
        return {"id": "1", "slug": name.lower().replace(" ", "-"), "name": name}

    async def _fetch_squad_value(self, slug: str, team_id: str):
        if "arsenal" in slug:
            return None, None
        return 220.0, 8.1


class TransfermarktHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_prefers_matching_club_over_first_row(self):
        html = """
        <table class="items">
          <thead><tr><th>Club</th></tr></thead>
          <tbody>
            <tr><td class="hauptlink"><a href="/chelsea-fc/startseite/verein/631">Chelsea FC</a></td></tr>
            <tr><td class="hauptlink"><a href="/arsenal-fc/startseite/verein/11">Arsenal FC</a></td></tr>
          </tbody>
        </table>
        """
        scraper = _TransfermarktSearchScraper(html)

        result = await scraper._search_team("Arsenal")

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "11")
        self.assertEqual(result["name"], "Arsenal FC")

    async def test_scrape_success_false_when_market_values_missing(self):
        scraper = _TransfermarktValueScraper("Arsenal", "Chelsea")

        result = await scraper.scrape()

        self.assertFalse(result.scrape_success)
        self.assertIn("Arsenal", result.error_message)


class _CachedOddsScraper(OddsportalScraper):
    def __init__(self):
        super().__init__("Arsenal", "Chelsea")
        self.fetches = 0

    async def _fetch_with_retry(self, url: str, *args, **kwargs):
        self.fetches += 1
        if "premier-league" in url:
            return """
            <script type="application/ld+json">
              {
                "@type": "SportsEvent",
                "name": "Arsenal vs Chelsea",
                "url": "https://www.betexplorer.com/soccer/england/premier-league/arsenal-chelsea/ABC123/",
                "homeTeam": {"name": "Arsenal"},
                "awayTeam": {"name": "Chelsea"}
              }
            </script>
            """
        return """
        <script type="application/ld+json">
          {
            "@type": "SportsEvent",
            "name": "Barcelona vs Real Madrid",
            "url": "https://example.com/other"
          }
        </script>
        """


class _EarlyExitOddsScraper(OddsportalScraper):
    def __init__(self):
        super().__init__("Arsenal", "Chelsea")
        self.cancelled: list[str] = []

    async def _check_league_for_match(self, league_path: str):
        if league_path == "/fast":
            await asyncio.sleep(0)
            return "https://www.betexplorer.com/match/FAST123/"
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled.append(league_path)
            raise
        return None


class BetexplorerHardeningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_betexplorer_caches()

    async def test_match_url_uses_cache_on_second_lookup(self):
        scraper = _CachedOddsScraper()

        with patch(
            "scrapers.oddsportal._LEAGUE_PAGES",
            ["/soccer/england/premier-league/", "/soccer/spain/laliga/"],
        ):
            first = await scraper._find_match_url()
            fetches_after_first = scraper.fetches
            second = await scraper._find_match_url()

        self.assertEqual(first, second)
        self.assertEqual(scraper.fetches, fetches_after_first)

    async def test_match_url_cancels_pending_tasks_after_first_match(self):
        scraper = _EarlyExitOddsScraper()

        with patch("scrapers.oddsportal._LEAGUE_PAGES", ["/fast", "/slow-1", "/slow-2"]):
            result = await scraper._find_match_url()

        self.assertEqual(result, "https://www.betexplorer.com/match/FAST123/")
        self.assertGreaterEqual(len(scraper.cancelled), 1)

    async def test_extract_total_line_odds_supports_1_5_and_3_5(self):
        html = """
        <table data-handicap="1.50">
          <tbody>
            <tr><td data-odd="1.28"></td><td data-odd="3.60"></td></tr>
            <tr><td data-odd="1.31"></td><td data-odd="3.45"></td></tr>
          </tbody>
        </table>
        <table data-handicap="3.50">
          <tbody>
            <tr><td data-odd="3.10"></td><td data-odd="1.40"></td></tr>
            <tr><td data-odd="3.25"></td><td data-odd="1.37"></td></tr>
          </tbody>
        </table>
        """

        self.assertEqual(
            OddsportalScraper._extract_total_line_odds(html, 1.5),
            {"over": 1.31, "under": 3.6},
        )
        self.assertEqual(
            OddsportalScraper._extract_total_line_odds(html, 3.5),
            {"over": 3.25, "under": 1.4},
        )

    def test_fill_derived_markets_populates_dnb_and_double_chance_from_1x2(self):
        from models import OddsData

        result = OddsData(
            team1_win_odds=2.15,
            draw_odds=3.2,
            team2_win_odds=3.55,
        )

        OddsportalScraper._fill_implied_probs(result)
        OddsportalScraper._fill_derived_markets(result)

        self.assertIsNotNone(result.dnb_team1_odds)
        self.assertIsNotNone(result.dnb_team2_odds)
        self.assertIsNotNone(result.double_chance_1x_odds)
        self.assertIsNotNone(result.double_chance_12_odds)
        self.assertIsNotNone(result.double_chance_x2_odds)
        self.assertAlmostEqual(result.double_chance_12_odds or 0.0, 1.4184, places=4)


if __name__ == "__main__":
    unittest.main()
