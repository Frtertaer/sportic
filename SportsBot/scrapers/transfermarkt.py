"""
Transfermarkt scraper — squad market values.

Strategy: Transfermarkt allows scraping with correct headers (no Cloudflare on EN pages).
  1. Search: GET /schnellsuche/ergebnis/schnellsuche?query={team}&Hacksuche=suchen
  2. Squad value: GET /{slug}/kader/verein/{id} → parse totals from table footer

Must set Accept-Language: en-US and Referer from their domain.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from models import TransfermarktData
from scrapers.base import BaseScraper, ScraperError, RateLimitError
from scrapers.fotmob import _names_match, _normalize_name

logger = logging.getLogger(__name__)

BASE_URL = "https://www.transfermarkt.com"
_TM_HEADERS = {
    "Referer": BASE_URL + "/",
    "Accept-Language": "en-US,en;q=0.9",
}


class TransfermarktScraper(BaseScraper):
    DOMAIN = "transfermarkt.com"

    @staticmethod
    def _candidate_score(expected_name: str, candidate_name: str) -> int:
        if not _names_match([expected_name], [candidate_name]):
            return 0
        expected_norm = _normalize_name(expected_name)
        candidate_norm = _normalize_name(candidate_name)
        if expected_norm == candidate_norm:
            return 3
        expected_tokens = set(expected_norm.split())
        candidate_tokens = set(candidate_norm.split())
        if expected_tokens and candidate_tokens and (
            expected_tokens <= candidate_tokens or candidate_tokens <= expected_tokens
        ):
            return 2
        return 1

    # ── Team search ───────────────────────────────────────────────────────────

    async def _search_team(self, name: str) -> Optional[dict]:
        """Returns {id, slug, name} or None."""
        url = f"{BASE_URL}/schnellsuche/ergebnis/schnellsuche"
        params = {"query": name, "Hacksuche": "suchen"}
        try:
            html = await self._fetch_with_retry(url, params=params, headers=_TM_HEADERS)
            soup = BeautifulSoup(html, "lxml")

            tables = soup.select("table.items")
            for table in tables:
                headers_text = " ".join(
                    th.get_text(strip=True).lower()
                    for th in table.select("th")
                )
                if "club" not in headers_text:
                    continue
                rows = table.select("tbody tr")
                best_match: Optional[dict] = None
                best_score = 0
                for row in rows:
                    link = row.select_one("td.hauptlink a")
                    if not link or not link.get("href"):
                        continue
                    href = link["href"]
                    id_match = re.search(r"/verein/(\d+)", href)
                    slug_match = re.match(r"/([^/]+)/", href)
                    if not (id_match and slug_match):
                        continue
                    candidate = {
                        "id": id_match.group(1),
                        "slug": slug_match.group(1),
                        "name": link.get_text(strip=True),
                    }
                    score = self._candidate_score(name, candidate["name"])
                    if score > best_score:
                        best_match = candidate
                        best_score = score
                        if score == 3:
                            break
                if best_match is not None:
                    return best_match
        except (ScraperError, RateLimitError) as exc:
            logger.warning("Transfermarkt search failed for '%s': %s", name, exc)
        return None

    # ── Squad value ───────────────────────────────────────────────────────────

    async def _fetch_squad_value(
        self, slug: str, team_id: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Returns (total_squad_value_EUR_millions, avg_player_value_EUR_millions)."""
        url = f"{BASE_URL}/{slug}/kader/verein/{team_id}"
        try:
            html = await self._fetch_with_retry(url, headers=_TM_HEADERS)
            soup = BeautifulSoup(html, "lxml")

            player_values: list[float] = []
            total_val: Optional[float] = None

            # Try footer total first
            tfoot = soup.select_one("table.items tfoot")
            if tfoot:
                # Footer cells contain the total (last td with rechts class or bold)
                bold = tfoot.select_one("td.rechts b") or tfoot.select_one("td.rechts")
                if bold:
                    total_val = self._parse_value(bold.get_text(strip=True))

            # Collect individual player values from tbody
            for row in soup.select("table.items tbody tr.odd, table.items tbody tr.even"):
                val_cell = row.select_one("td.rechts")
                if val_cell:
                    v = self._parse_value(val_cell.get_text(strip=True))
                    if v and v > 0:
                        player_values.append(v)

            # If footer parse failed, sum individual values
            if total_val is None and player_values:
                total_val = round(sum(player_values), 2)

            avg_val = (
                round(sum(player_values) / len(player_values), 2)
                if player_values
                else None
            )
            return total_val, avg_val

        except (ScraperError, RateLimitError) as exc:
            logger.warning(
                "Transfermarkt squad value failed for %s/%s: %s", slug, team_id, exc
            )
        return None, None

    @staticmethod
    def _parse_value(text: str) -> Optional[float]:
        """Parse '€150m', '€25.5m', '€800k', '1.50m' → float in millions EUR."""
        text = text.strip().upper().replace("€", "").replace(",", ".").replace(" ", "")
        try:
            if "M" in text:
                return float(text.replace("M", ""))
            elif "K" in text:
                return round(float(text.replace("K", "")) / 1000, 4)
            elif text:
                v = float(text)
                # Values > 1000 are likely already in thousands (raw EUR)
                return round(v / 1_000_000, 4) if v > 1000 else v
        except (ValueError, AttributeError):
            pass
        return None

    # ── Main entry point ──────────────────────────────────────────────────────

    async def _search_team_with_fallback(self, name: str) -> Optional[dict]:
        """Try multiple search variants until one succeeds."""
        from scrapers.fotmob import _build_derived_search_aliases

        # Primary: exact name
        result = await self._search_team(name)
        if result:
            return result

        # Fallback: try derived aliases (e.g. "Como 1907" → "Como")
        for alias in _build_derived_search_aliases(name):
            result = await self._search_team(alias)
            if result:
                return result

        # Fallback: append common prefixes for short names
        if len(name.split()) == 1 and len(name) <= 6:
            for prefix in ("FC", "AC", "SC", "SS"):
                result = await self._search_team(f"{prefix} {name}")
                if result:
                    return result

        return None

    async def scrape(self) -> TransfermarktData:
        try:
            t1_info, t2_info = await asyncio.gather(
                self._search_team_with_fallback(self.team1),
                self._search_team_with_fallback(self.team2),
            )

            if not t1_info or not t2_info:
                missing = [n for n, i in [(self.team1, t1_info), (self.team2, t2_info)] if not i]
                return TransfermarktData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Transfermarkt: {', '.join(missing)}",
                )

            (t1_total, t1_avg), (t2_total, t2_avg) = await asyncio.gather(
                self._fetch_squad_value(t1_info["slug"], t1_info["id"]),
                self._fetch_squad_value(t2_info["slug"], t2_info["id"]),
            )

            scrape_success = t1_total is not None and t2_total is not None
            error_message = None
            if not scrape_success:
                missing_values = []
                if t1_total is None:
                    missing_values.append(self.team1)
                if t2_total is None:
                    missing_values.append(self.team2)
                error_message = (
                    "Could not parse squad market values for: "
                    + ", ".join(missing_values)
                )

            return TransfermarktData(
                team1_name=t1_info["name"],
                team2_name=t2_info["name"],
                team1_market_value_eur=t1_total,
                team2_market_value_eur=t2_total,
                team1_avg_player_value=t1_avg,
                team2_avg_player_value=t2_avg,
                scrape_success=scrape_success,
                error_message=error_message,
            )

        except Exception as exc:
            logger.error("Transfermarkt critical failure: %s", exc, exc_info=True)
            return TransfermarktData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
