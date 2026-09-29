import unittest

from scrapers.x_social import XSocialScraper


class XSocialUnavailableTest(unittest.IsolatedAsyncioTestCase):
    async def test_scraper_returns_unavailable_result(self):
        scraper = XSocialScraper("Arsenal", "Chelsea")

        result = await scraper.scrape(league="Premier League")

        self.assertEqual(result.team1_name, "Arsenal")
        self.assertEqual(result.team2_name, "Chelsea")
        self.assertFalse(result.scrape_success)
        self.assertEqual(result.signals, [])
        self.assertIn("unavailable", result.error_message)

    async def test_scraper_allows_team_override(self):
        scraper = XSocialScraper("Arsenal", "Chelsea")

        result = await scraper.scrape("Liverpool", "Everton")

        self.assertEqual(result.team1_name, "Liverpool")
        self.assertEqual(result.team2_name, "Everton")
        self.assertFalse(result.scrape_success)
