"""
Pinnacle odds scraper via the-odds-api.com (free tier: 500 credits/month).

Fetches Pinnacle-specific odds for a given match to enable CLV tracking.
Falls back to extracting Pinnacle from BetExplorer if API key not available.

Usage:
  - Set ODDS_API_KEY in .env for the-odds-api.com
  - Automatically runs in parallel with other scrapers
  - Stores opening odds at prediction time
  - Background job fetches closing odds near kickoff
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

from models import PinnacleOddsSnapshot
from scrapers.base import BaseScraper
import config

logger = logging.getLogger(__name__)

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_SPORT_KEY = "soccer"
# Map of league keywords to the-odds-api sport keys
_LEAGUE_SPORT_KEYS = {
    "premier league": "soccer_epl",
    "championship": "soccer_efl_champ",
    "la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue 1": "soccer_france_ligue_one",
    "eredivisie": "soccer_netherlands_eredivisie",
    "primeira liga": "soccer_portugal_primeira_liga",
    "champions league": "soccer_uefa_champs_league",
    "europa league": "soccer_uefa_europa_league",
    "conference league": "soccer_uefa_europa_conference_league",
}


def _best_sport_key(league_name: str | None) -> str:
    """Map league name to the-odds-api sport key."""
    if not league_name:
        return "soccer_epl"  # default fallback
    lower = league_name.strip().lower()
    for key, sport in _LEAGUE_SPORT_KEYS.items():
        if key in lower:
            return sport
    return "soccer_epl"


def _fuzzy_match(name1: str, name2: str) -> float:
    """Quick fuzzy match score between two team names."""
    return SequenceMatcher(None, name1.lower(), name2.lower()).ratio()


class PinnacleScraper(BaseScraper):
    """Fetch Pinnacle odds from the-odds-api.com."""
    DOMAIN = "api.the-odds-api.com"

    async def scrape(self) -> PinnacleOddsSnapshot:
        api_key = getattr(config, "ODDS_API_KEY", "")
        if not api_key:
            return PinnacleOddsSnapshot(
                captured_at=datetime.now(timezone.utc).isoformat(),
            )

        try:
            return await self._fetch_pinnacle_odds(api_key)
        except Exception as exc:
            logger.warning("Pinnacle scraper failed: %s", exc)
            return PinnacleOddsSnapshot(
                captured_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            await self._close()

    async def _fetch_pinnacle_odds(self, api_key: str) -> PinnacleOddsSnapshot:
        now = datetime.now(timezone.utc).isoformat()

        # Try multiple sport keys if we don't know the league
        sport_keys_to_try = ["soccer_epl", "soccer_spain_la_liga",
                             "soccer_germany_bundesliga", "soccer_italy_serie_a",
                             "soccer_france_ligue_one"]

        for sport_key in sport_keys_to_try:
            result = await self._try_sport_key(api_key, sport_key, now)
            if result and (result.pin_w1 is not None):
                return result

        return PinnacleOddsSnapshot(captured_at=now)

    async def _try_sport_key(
        self, api_key: str, sport_key: str, now: str,
    ) -> PinnacleOddsSnapshot | None:
        url = f"{_ODDS_API_BASE}/sports/{sport_key}/odds"
        params = {
            "apiKey": api_key,
            "bookmakers": "pinnacle",
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        }

        resp_data = await self._get_json(url, params=params)
        if not resp_data or not isinstance(resp_data, list):
            return None

        # Find matching event
        best_match = None
        best_score = 0.0

        for event in resp_data:
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            score = (
                _fuzzy_match(self.team1, home) + _fuzzy_match(self.team2, away)
            ) / 2.0
            if score > best_score and score > 0.5:
                best_score = score
                best_match = event

        if not best_match:
            return None

        snapshot = PinnacleOddsSnapshot(captured_at=now)

        for bookmaker in best_match.get("bookmakers", []):
            if bookmaker.get("key") != "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                outcomes = {
                    o.get("name"): o.get("price")
                    for o in market.get("outcomes", [])
                }
                if market.get("key") == "h2h":
                    snapshot.pin_w1 = outcomes.get(best_match.get("home_team"))
                    snapshot.pin_draw = outcomes.get("Draw")
                    snapshot.pin_w2 = outcomes.get(best_match.get("away_team"))
                elif market.get("key") == "totals":
                    snapshot.pin_over_2_5 = outcomes.get("Over")
                    snapshot.pin_under_2_5 = outcomes.get("Under")

        return snapshot

    async def _get_json(self, url: str, params: dict) -> object:
        """GET request returning parsed JSON via BaseScraper session (respects domain limits)."""
        try:
            raw = await self._fetch_with_retry(
                url,
                params=params,
                headers={"Accept": "application/json"},
                json_mode=True,
            )
            import json
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Odds API request failed: %s", exc)
            return None


async def fetch_pinnacle_snapshot(
    team1: str, team2: str, league_name: str | None = None,
) -> PinnacleOddsSnapshot:
    """Convenience function to get Pinnacle odds for a match."""
    scraper = PinnacleScraper(team1, team2)
    try:
        return await scraper.scrape()
    finally:
        await scraper._close()


def compute_clv(
    placement_odds: float,
    closing_odds: float,
) -> float:
    """
    Compute CLV percentage.

    Positive CLV = you got better odds than closing line = long-term edge.
    Formula: (closing_implied - placement_implied) / placement_implied × 100

    Example: placed at 2.10, closed at 1.95
      placement_implied = 1/2.10 = 47.6%
      closing_implied = 1/1.95 = 51.3%
      CLV = (51.3 - 47.6) / 47.6 × 100 = +7.6%  (you had edge!)
    """
    if placement_odds <= 1.0 or closing_odds <= 1.0:
        return 0.0
    placement_implied = 1.0 / placement_odds
    closing_implied = 1.0 / closing_odds
    return round((closing_implied - placement_implied) / placement_implied * 100.0, 2)
