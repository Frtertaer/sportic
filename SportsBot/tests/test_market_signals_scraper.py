from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from scrapers.market_signals import MarketSignalsScraper


_HTML_1X2 = """
<table>
  <tbody>
    <tr>
      <td data-odd="1.48"><i class="icon icon__decreasing archiveOddsMovement__icon_arrowMovement"></i></td>
      <td data-odd="4.90"><i class="icon icon__increasing archiveOddsMovement__icon_arrowMovement"></i></td>
      <td data-odd="8.80"><i class="icon icon__increasing archiveOddsMovement__icon_arrowMovement"></i></td>
    </tr>
    <tr>
      <td data-odd="1.31"><i class="icon icon__decreasing archiveOddsMovement__icon_arrowMovement"></i></td>
      <td data-odd="5.10"></td>
      <td data-odd="9.20"><i class="icon icon__increasing archiveOddsMovement__icon_arrowMovement"></i></td>
    </tr>
  </tbody>
</table>
"""

_HTML_OU = """
<table data-handicap="2.50">
  <tbody>
    <tr>
      <td data-odd="1.88"><i class="icon icon__increasing archiveOddsMovement__icon_arrowMovement"></i></td>
      <td data-odd="1.96"><i class="icon icon__decreasing archiveOddsMovement__icon_arrowMovement"></i></td>
    </tr>
    <tr>
      <td data-odd="1.84"></td>
      <td data-odd="2.00"></td>
    </tr>
  </tbody>
</table>
"""

_HTML_BTTS = """
<table data-handicap="0">
  <tbody>
    <tr>
      <td data-odd="1.72"><i class="icon icon__decreasing archiveOddsMovement__icon_arrowMovement"></i></td>
      <td data-odd="2.08"></td>
    </tr>
    <tr>
      <td data-odd="1.75"></td>
      <td data-odd="2.02"><i class="icon icon__increasing archiveOddsMovement__icon_arrowMovement"></i></td>
    </tr>
  </tbody>
</table>
"""

_HTML_AH = """
<table data-handicap="-1.50">
  <tbody>
    <tr>
      <td data-odd="2.35"></td>
      <td data-odd="1.60"></td>
    </tr>
  </tbody>
</table>
<table data-handicap="-0.50">
  <tbody>
    <tr>
      <td data-odd="1.36"><i class="icon icon__decreasing archiveOddsMovement__icon_arrowMovement"></i></td>
      <td data-odd="3.15"></td>
    </tr>
    <tr>
      <td data-odd="1.34"></td>
      <td data-odd="3.20"></td>
    </tr>
    <tr>
      <td data-odd="1.38"></td>
      <td data-odd="3.05"></td>
    </tr>
  </tbody>
</table>
"""


class MarketSignalsScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_builds_market_consensus_and_movement_signals(self):
        scraper = MarketSignalsScraper("Arsenal", "Everton")

        async def fake_fetch(match_id: str, bet_type: str, referer: str) -> str:
            return {
                "1x2": _HTML_1X2,
                "ou": _HTML_OU,
                "bts": _HTML_BTTS,
                "ah": _HTML_AH,
            }[bet_type]

        with (
            patch.object(scraper, "_find_match_url", new=AsyncMock(return_value={"url": "https://example.com/m/ABC123/", "status": ""})),
            patch.object(scraper, "_fetch_best_odds_html", side_effect=fake_fetch),
        ):
            result = await scraper.scrape()

        self.assertTrue(result.scrape_success)
        self.assertEqual(result.favourite_label, "team1")
        self.assertGreater(result.one_x_two["team1"].implied_prob or 0.0, 0.6)
        self.assertEqual(result.one_x_two["team1"].movement_score, 2)
        self.assertEqual(result.one_x_two["team2"].movement_score, -2)
        self.assertAlmostEqual(result.asian_handicap_line or 0.0, -0.5)
        self.assertEqual(result.asian_handicap["team1"].bookmaker_count, 3)
        self.assertIn("Favourite price dispersion is high across bookmakers", result.market_warnings)
        self.assertIn("Asian handicap movement is mixed", result.market_warnings)

    async def test_scrape_fails_when_1x2_consensus_is_unavailable(self):
        scraper = MarketSignalsScraper("Arsenal", "Everton")

        with (
            patch.object(scraper, "_find_match_url", new=AsyncMock(return_value={"url": "https://example.com/m/ABC123/", "status": ""})),
            patch.object(scraper, "_fetch_best_odds_html", new=AsyncMock(return_value="<table></table>")),
        ):
            result = await scraper.scrape()

        self.assertFalse(result.scrape_success)
        self.assertIn("market consensus", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
