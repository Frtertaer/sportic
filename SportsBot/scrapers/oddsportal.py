"""
Oddsportal scraper replaced with Betexplorer (betexplorer.com).

Strategy:
  1. Fetch 25 league pages in PARALLEL - JSON-LD SportsEvent list
  2. Find the match URL for team1 vs team2 via fuzzy name matching
  3. Extract match_id from the URL (last path segment, e.g. CE2gREmB)
  4. Call /match-odds/{match_id}/0/{bet_type}/bestOdds/?lang=en
  5. 1x2 / O/U 2.5 / BTTS / Asian Handicap parsed from data-odd attrs
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

import config
from models import OddsData
from scrapers.base import BaseScraper, ScraperError, RateLimitError
from scrapers.fotmob import _names_match

logger = logging.getLogger(__name__)
_LEAGUE_EVENTS_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}
_MATCH_URL_CACHE: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_MATCH_ODDS_HTML_CACHE: dict[tuple[str, str], tuple[float, str]] = {}

BASE_URL = "https://www.betexplorer.com"

_BE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": BASE_URL + "/",
    "Accept-Language": "en-US,en;q=0.9",
}

_ODDS_HEADERS = {
    **_BE_HEADERS,
    "Accept": "application/json, */*",
}

# 25 football league pages on Betexplorer (searched in parallel)
_LEAGUE_PAGES = [
    # Top 5 European
    "/soccer/england/premier-league/",
    "/soccer/spain/laliga/",
    "/soccer/germany/bundesliga/",
    "/soccer/italy/serie-a/",
    "/soccer/france/ligue-1/",
    # Major European
    "/soccer/netherlands/eredivisie/",
    "/soccer/portugal/liga-portugal/",
    "/soccer/russia/premier-league/",
    "/soccer/ukraine/premier-league/",
    "/soccer/turkey/super-lig/",
    "/soccer/scotland/premiership/",
    "/soccer/belgium/jupiler-pro-league/",
    # Second divisions
    "/soccer/england/championship/",
    "/soccer/germany/2-bundesliga/",
    "/soccer/italy/serie-b/",
    "/soccer/france/ligue-2/",
    # European cups
    "/soccer/europe/champions-league/",
    "/soccer/europe/europa-league/",
    "/soccer/europe/conference-league/",
    # Americas
    "/soccer/usa/mls/",
    "/soccer/brazil/serie-a/",
    "/soccer/argentina/primera-division/",
    # Asia
    "/soccer/japan/j1-league/",
    "/soccer/south-korea/k-league-1/",
    # Nordic
    "/soccer/sweden/allsvenskan/",
]


def clear_betexplorer_caches() -> None:
    _LEAGUE_EVENTS_CACHE.clear()
    _MATCH_URL_CACHE.clear()
    _MATCH_ODDS_HTML_CACHE.clear()


class OddsportalScraper(BaseScraper):
    """Uses Betexplorer instead of Oddsportal (fully JS-rendered)."""
    DOMAIN = "betexplorer.com"

    # -- Match URL discovery ------------------------------------------

    def _slugify(self, name: str) -> str:
        """Convert team name to URL-like slug for fuzzy matching."""
        import unicodedata
        name = unicodedata.normalize("NFKD", name)
        name = name.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9]", "", name.lower())

    @staticmethod
    def _cache_get(cache: dict, key):
        entry = cache.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            cache.pop(key, None)
            return None
        return value

    @staticmethod
    def _cache_set(cache: dict, key, value) -> None:
        # Proactive eviction: purge expired entries every 50 writes to prevent unbounded growth
        if len(cache) > 50:
            now = time.monotonic()
            expired = [k for k, (exp, _) in cache.items() if now >= exp]
            for k in expired:
                cache.pop(k, None)
        cache[key] = (
            time.monotonic() + config.BETEXPLORER_INDEX_CACHE_TTL_SECONDS,
            value,
        )

    def _match_cache_key(self) -> tuple[str, str]:
        pair = sorted([self._slugify(self.team1), self._slugify(self.team2)])
        return date.today().isoformat(), f"{pair[0]}|{pair[1]}"

    @staticmethod
    def _extract_team_name(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get("name") or value.get("teamName") or "")
        return str(value or "")

    @staticmethod
    def _split_event_name(name: str) -> Optional[tuple[str, str]]:
        lowered = name.lower()
        separators = (" vs ", " v ", " - ", " – ", " — ")
        for separator in separators:
            if separator not in lowered:
                continue
            pattern = re.compile(re.escape(separator), flags=re.IGNORECASE)
            parts = [part.strip() for part in pattern.split(name, maxsplit=1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                return parts[0], parts[1]
        return None

    def _event_matches(self, event: dict[str, str]) -> bool:
        home = event.get("home", "")
        away = event.get("away", "")
        if not home or not away:
            split = self._split_event_name(event.get("name", ""))
            if split:
                home, away = split
        if home and away:
            direct = _names_match([self.team1], [home]) and _names_match([self.team2], [away])
            reverse = _names_match([self.team1], [away]) and _names_match([self.team2], [home])
            return direct or reverse
        event_name_slug = self._slugify(event.get("name", ""))
        return self._slugify(self.team1) in event_name_slug and self._slugify(self.team2) in event_name_slug

    def _parse_league_events(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        events: list[dict[str, str]] = []
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(sc.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") != "SportsEvent":
                    continue
                url = item.get("url", "")
                if not url:
                    continue
                events.append(
                    {
                        "name": str(item.get("name", "")),
                        "home": self._extract_team_name(item.get("homeTeam")),
                        "away": self._extract_team_name(item.get("awayTeam")),
                        "url": str(url),
                        "status": str(item.get("eventStatus", "")),
                    }
                )
        return events

    async def _load_league_events(self, league_path: str) -> list[dict[str, str]]:
        cache_key = (date.today().isoformat(), league_path)
        cached = self._cache_get(_LEAGUE_EVENTS_CACHE, cache_key)
        if cached is not None:
            return cached
        html = await self._fetch_with_retry(
            BASE_URL + league_path,
            headers=_BE_HEADERS,
        )
        events = self._parse_league_events(html)
        self._cache_set(_LEAGUE_EVENTS_CACHE, cache_key, events)
        return events

    async def _check_league_for_match(self, league_path: str) -> Optional[dict[str, str]]:
        try:
            for event in await self._load_league_events(league_path):
                if self._event_matches(event):
                    url = event["url"]
                    status = event.get("status", "")
                    logger.info("Found match on Betexplorer (%s): %s [status=%s]", league_path, url, status)
                    return {"url": url, "status": status}
        except (ScraperError, RateLimitError) as exc:
            logger.debug("Betexplorer league page failed %s: %s", league_path, exc)
        return None

    async def _search_fallback(self) -> Optional[dict[str, str]]:
        """Fallback: search Betexplorer directly when league pages miss."""
        query = f"{self.team1} {self.team2}"
        url = f"{BASE_URL}/results/soccer/"
        try:
            html = await self._fetch_with_retry(
                url,
                params={"q": query},
                headers=_BE_HEADERS,
            )
        except (ScraperError, RateLimitError) as exc:
            logger.debug("Betexplorer search fallback failed: %s", exc)
            return None

        # Try JSON-LD first (some result pages have it)
        events = self._parse_league_events(html)
        for event in events:
            if self._event_matches(event):
                logger.info("Betexplorer search fallback found match: %s", event["url"])
                return {"url": event["url"], "status": event.get("status", "")}

        # Try parsing result links from HTML
        soup = BeautifulSoup(html, "lxml")
        for link in soup.select("a[href*='/soccer/']"):
            href = link.get("href", "")
            text = link.get_text(strip=True)
            if not href or not text:
                continue
            slug1 = self._slugify(self.team1)
            slug2 = self._slugify(self.team2)
            text_slug = self._slugify(text)
            if slug1 in text_slug and slug2 in text_slug:
                full_url = href if href.startswith("http") else BASE_URL + href
                logger.info("Betexplorer search fallback found link: %s", full_url)
                return {"url": full_url, "status": ""}
        return None

    async def _find_match_url(self) -> Optional[dict[str, str]]:
        """
        Search all league pages in PARALLEL for JSON-LD SportsEvent entries.
        Falls back to Betexplorer search if no league page matches.
        Returns {"url": ..., "status": ...} or None.
        """
        match_cache_key = self._match_cache_key()
        cached = self._cache_get(_MATCH_URL_CACHE, match_cache_key)
        if cached is not None:
            return cached

        tasks = [
            asyncio.create_task(self._check_league_for_match(league_path))
            for league_path in _LEAGUE_PAGES
        ]
        result = None
        try:
            for task in asyncio.as_completed(tasks):
                result = await task
                if result:
                    self._cache_set(_MATCH_URL_CACHE, match_cache_key, result)
                    return result
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        # Fallback: direct search on Betexplorer
        if result is None:
            result = await self._search_fallback()
            if result:
                self._cache_set(_MATCH_URL_CACHE, match_cache_key, result)
        return result

    # -- Best-odds JSON endpoint --------------------------------------

    async def _fetch_best_odds_html(
        self, match_id: str, bet_type: str, referer: str
    ) -> str:
        """Call /match-odds/{match_id}/0/{bet_type}/bestOdds/?lang=en"""
        cache_key = (match_id, bet_type)
        cached = self._cache_get(_MATCH_ODDS_HTML_CACHE, cache_key)
        if cached is not None:
            return cached
        url = f"{BASE_URL}/match-odds/{match_id}/0/{bet_type}/bestOdds/?lang=en"
        headers = {**_ODDS_HEADERS, "Referer": referer}
        raw = await self._fetch_with_retry(url, headers=headers, json_mode=True)
        try:
            data = json.loads(raw)
            html = data.get("odds", "")
            self._cache_set(_MATCH_ODDS_HTML_CACHE, cache_key, html)
            return html
        except (json.JSONDecodeError, AttributeError):
            return ""

    # -- Odds extraction from HTML ------------------------------------

    @staticmethod
    def _extract_best_odds(html: str, bet_type: str) -> dict:
        """Parse data-odd attributes from the /match-odds/ HTML fragment."""
        if not html:
            return {}
        soup = BeautifulSoup(html, "lxml")
        result: dict = {}

        if bet_type == "1x2":
            home_odds, draw_odds, away_odds = [], [], []
            for row in soup.select("tr"):
                cells = row.select("[data-odd]")
                if len(cells) >= 3:
                    try:
                        h = float(cells[0]["data-odd"])
                        d = float(cells[1]["data-odd"])
                        a = float(cells[2]["data-odd"])
                        if 1.01 <= h <= 100 and 1.01 <= d <= 100 and 1.01 <= a <= 100:
                            home_odds.append(h)
                            draw_odds.append(d)
                            away_odds.append(a)
                    except (ValueError, KeyError):
                        pass
            if home_odds:
                result["home"] = round(max(home_odds), 2)
                result["draw"] = round(max(draw_odds), 2)
                result["away"] = round(max(away_odds), 2)

        elif bet_type == "ou":
            totals = OddsportalScraper._extract_total_line_odds(html, 2.5)
            if totals:
                result["over"] = totals["over"]
                result["under"] = totals["under"]

        elif bet_type == "bts":
            yes_odds, no_odds = [], []
            for row in soup.select("tr"):
                cells = row.select("[data-odd]")
                if len(cells) >= 2:
                    try:
                        y = float(cells[0]["data-odd"])
                        n = float(cells[1]["data-odd"])
                        if 1.01 <= y <= 100 and 1.01 <= n <= 100:
                            yes_odds.append(y)
                            no_odds.append(n)
                    except (ValueError, KeyError):
                        pass
            if yes_odds:
                result["btts_yes"] = round(max(yes_odds), 2)
                result["btts_no"] = round(max(no_odds), 2)

        return result

    @staticmethod
    def _extract_total_line_odds(html: str, line: float) -> dict:
        """Extract best O/U odds for a specific totals line (e.g. 1.5, 2.5, 3.5)."""
        if not html:
            return {}
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one(f'table[data-handicap="{line:.2f}"]')
        if table is None:
            return {}

        over_odds: list[float] = []
        under_odds: list[float] = []
        for row in table.select("tr"):
            cells = row.select("[data-odd]")
            if len(cells) < 2:
                continue
            try:
                over = float(cells[0]["data-odd"])
                under = float(cells[1]["data-odd"])
            except (ValueError, KeyError):
                continue
            if 1.01 <= over <= 100 and 1.01 <= under <= 100:
                over_odds.append(over)
                under_odds.append(under)

        if not over_odds:
            return {}
        return {
            "over": round(max(over_odds), 2),
            "under": round(max(under_odds), 2),
        }

    @staticmethod
    def _extract_best_ah_odds(html: str) -> dict:
        """
        Parse Asian Handicap odds HTML fragment from Betexplorer.
        Each table[data-handicap] element represents one AH line.
        Pick the line with the most bookmaker rows (most liquid market).
        Returns {line: float, home: float, away: float} or {}.
        """
        if not html:
            return {}
        soup = BeautifulSoup(html, "lxml")
        best: dict = {}
        best_row_count = 0
        for table in soup.select("table[data-handicap]"):
            try:
                line = float(table["data-handicap"])
            except (ValueError, KeyError):
                continue
            home_list, away_list = [], []
            for row in table.select("tr"):
                cells = row.select("[data-odd]")
                if len(cells) >= 2:
                    try:
                        h = float(cells[0]["data-odd"])
                        a = float(cells[1]["data-odd"])
                        if 1.01 <= h <= 20 and 1.01 <= a <= 20:
                            home_list.append(h)
                            away_list.append(a)
                    except (ValueError, KeyError):
                        pass
            if home_list and len(home_list) > best_row_count:
                best_row_count = len(home_list)
                best = {
                    "line": line,
                    "home": round(max(home_list), 2),
                    "away": round(max(away_list), 2),
                }
        return best

    # -- Implied probabilities ----------------------------------------

    @staticmethod
    def _fill_implied_probs(result: OddsData) -> None:
        try:
            if not (result.team1_win_odds and result.draw_odds and result.team2_win_odds):
                return
            raw = [
                1 / result.team1_win_odds,
                1 / result.draw_odds,
                1 / result.team2_win_odds,
            ]
            overround = sum(raw)
            result.implied_prob_w1 = round(raw[0] / overround, 4)
            result.implied_prob_draw = round(raw[1] / overround, 4)
            result.implied_prob_w2 = round(raw[2] / overround, 4)
        except (ZeroDivisionError, TypeError):
            pass

    @staticmethod
    def _fill_derived_markets(result: OddsData) -> None:
        try:
            if None in (result.implied_prob_w1, result.implied_prob_draw, result.implied_prob_w2):
                return
            p1 = float(result.implied_prob_w1)
            pd = float(result.implied_prob_draw)
            p2 = float(result.implied_prob_w2)
            no_draw_total = p1 + p2
            if no_draw_total > 0:
                result.dnb_team1_odds = round(1.0 / (p1 / no_draw_total), 4)
                result.dnb_team2_odds = round(1.0 / (p2 / no_draw_total), 4)
            if (p1 + pd) > 0:
                result.double_chance_1x_odds = round(1.0 / (p1 + pd), 4)
            if (p1 + p2) > 0:
                result.double_chance_12_odds = round(1.0 / (p1 + p2), 4)
            if (pd + p2) > 0:
                result.double_chance_x2_odds = round(1.0 / (pd + p2), 4)
        except (ZeroDivisionError, TypeError, ValueError):
            return

    # -- Main entry point ---------------------------------------------

    async def scrape(self) -> OddsData:
        try:
            match_info = await self._find_match_url()
            if not match_info:
                return OddsData(
                    scrape_success=False,
                    error_message=(
                        f"Match not found on Betexplorer: {self.team1} vs {self.team2}"
                    ),
                )
            match_url = match_info["url"]
            event_status = match_info.get("status", "")

            # Skip matches that can't have odds
            _NO_ODDS_STATUSES = {
                "https://schema.org/EventPostponed",
                "https://schema.org/EventCancelled",
                "EventPostponed",
                "EventCancelled",
            }
            if event_status in _NO_ODDS_STATUSES:
                return OddsData(
                    scrape_success=False,
                    error_message=(
                        f"Match {self.team1} vs {self.team2} is postponed/cancelled — no odds available"
                    ),
                )

            match_id_m = re.search(r"/([A-Za-z0-9]+)/?$", match_url.rstrip("/"))
            if not match_id_m:
                return OddsData(
                    scrape_success=False,
                    error_message="Could not extract match ID from URL",
                )
            match_id = match_id_m.group(1)
            referer = match_url

            # Fetch 1X2, O/U 2.5, BTTS and AH odds concurrently
            html_1x2, html_ou, html_bts, html_ah = await asyncio.gather(
                self._fetch_best_odds_html(match_id, "1x2", referer),
                self._fetch_best_odds_html(match_id, "ou", referer),
                self._fetch_best_odds_html(match_id, "bts", referer),
                self._fetch_best_odds_html(match_id, "ah", referer),
                return_exceptions=True,
            )

            result = OddsData()

            if not isinstance(html_1x2, Exception):
                odds_1x2 = self._extract_best_odds(html_1x2, "1x2")
                result.team1_win_odds = odds_1x2.get("home")
                result.draw_odds = odds_1x2.get("draw")
                result.team2_win_odds = odds_1x2.get("away")
                self._fill_implied_probs(result)
                self._fill_derived_markets(result)

            if not isinstance(html_ou, Exception):
                odds_1_5 = self._extract_total_line_odds(html_ou, 1.5)
                odds_2_5 = self._extract_total_line_odds(html_ou, 2.5)
                odds_3_5 = self._extract_total_line_odds(html_ou, 3.5)
                result.over_1_5_odds = odds_1_5.get("over")
                result.under_1_5_odds = odds_1_5.get("under")
                result.over_2_5_odds = odds_2_5.get("over")
                result.under_2_5_odds = odds_2_5.get("under")
                result.over_3_5_odds = odds_3_5.get("over")
                result.under_3_5_odds = odds_3_5.get("under")

            if not isinstance(html_bts, Exception):
                odds_bts = self._extract_best_odds(html_bts, "bts")
                result.btts_yes_odds = odds_bts.get("btts_yes")
                result.btts_no_odds = odds_bts.get("btts_no")

            if not isinstance(html_ah, Exception):
                ah = self._extract_best_ah_odds(html_ah)
                if ah:
                    result.asian_handicap_line = ah["line"]
                    result.asian_handicap_home_odds = ah["home"]
                    result.asian_handicap_away_odds = ah["away"]
                    sign = "+" if ah["line"] > 0 else ""
                    result.asian_handicap = (
                        f"{self.team1} {sign}{ah['line']} "
                        f"({ah['home']:.2f} / {ah['away']:.2f})"
                    )

            result.scrape_success = bool(result.team1_win_odds)
            if not result.scrape_success:
                logger.warning(
                    "No 1X2 odds parsed for %s vs %s (match_id=%s, status=%s, html_len=%d)",
                    self.team1, self.team2, match_id, event_status,
                    len(html_1x2) if isinstance(html_1x2, str) else -1,
                )
                result.error_message = "Could not parse 1X2 odds from Betexplorer"
            return result

        except Exception as exc:
            logger.error("Betexplorer odds scraper critical failure: %s", exc, exc_info=True)
            return OddsData(scrape_success=False, error_message=str(exc))
        finally:
            await self._close()
