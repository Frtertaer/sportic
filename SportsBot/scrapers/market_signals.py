"""Consensus market-structure signals derived from Betexplorer bookmaker rows."""
from __future__ import annotations

import asyncio
import logging
import re

from bs4 import BeautifulSoup

from models import MarketSelectionSignal, MarketSignalsData
from scrapers.oddsportal import OddsportalScraper

logger = logging.getLogger(__name__)


class MarketSignalsScraper(OddsportalScraper):
    """Parse bookmaker consensus, dispersion, and price movement cues."""

    @staticmethod
    def _movement_score(cell) -> int:
        classes: list[str] = []
        for icon in cell.select(".archiveOddsMovement__icon_arrowMovement"):
            raw_classes = icon.get("class", [])
            if isinstance(raw_classes, list):
                classes.extend(str(value) for value in raw_classes)
            elif raw_classes:
                classes.append(str(raw_classes))
        if "icon__decreasing" in classes:
            return 1
        if "icon__increasing" in classes:
            return -1
        return 0

    @classmethod
    def _aggregate_market_rows(
        cls,
        rows: list[list],
        labels: list[str],
    ) -> dict[str, MarketSelectionSignal]:
        odds_map: dict[str, list[float]] = {label: [] for label in labels}
        movement_map: dict[str, int] = {label: 0 for label in labels}

        for row in rows:
            for label, cell in zip(labels, row):
                try:
                    odd = float(cell.get("data-odd"))
                except (TypeError, ValueError):
                    continue
                if not 1.01 <= odd <= 100.0:
                    continue
                odds_map[label].append(odd)
                movement_map[label] += cls._movement_score(cell)

        signals: dict[str, MarketSelectionSignal] = {}
        for label in labels:
            odds = odds_map[label]
            if not odds:
                continue
            signals[label] = MarketSelectionSignal(
                avg_odds=round(sum(odds) / len(odds), 3),
                min_odds=round(min(odds), 3),
                max_odds=round(max(odds), 3),
                bookmaker_count=len(odds),
                movement_score=movement_map[label],
            )

        if len(signals) >= 2:
            implied_inputs = {
                label: signal
                for label, signal in signals.items()
                if signal.avg_odds and signal.avg_odds > 0
            }
            raw = {label: 1 / signal.avg_odds for label, signal in implied_inputs.items()}
            total = sum(raw.values())
            if total > 0:
                for label, value in raw.items():
                    signals[label].implied_prob = round(value / total, 4)

        return signals

    @staticmethod
    def _parse_1x2_rows(html: str) -> list[list]:
        soup = BeautifulSoup(html, "lxml")
        rows: list[list] = []
        for row in soup.select("tr"):
            cells = row.select("td[data-odd]")
            if len(cells) >= 3:
                rows.append(cells[:3])
        return rows

    @staticmethod
    def _parse_table_rows(table) -> list[list]:
        rows: list[list] = []
        if table is None:
            return rows
        for row in table.select("tbody tr"):
            cells = row.select("td[data-odd]")
            if len(cells) >= 2:
                rows.append(cells[:2])
        return rows

    @classmethod
    def _pick_liquid_table(cls, html: str):
        soup = BeautifulSoup(html, "lxml")
        tables = []
        for table in soup.select("table[data-handicap]"):
            try:
                line = float(table.get("data-handicap"))
            except (TypeError, ValueError):
                continue
            row_count = len(cls._parse_table_rows(table))
            if row_count:
                tables.append((row_count, abs(line), line, table))
        if not tables:
            return None, None
        tables.sort(key=lambda item: (-item[0], item[1]))
        _, _, line, table = tables[0]
        return line, table

    @staticmethod
    def _build_warnings(
        one_x_two: dict[str, MarketSelectionSignal],
        favourite_label: str | None,
        favourite_gap_pct: float | None,
        asian_handicap: dict[str, MarketSelectionSignal],
    ) -> list[str]:
        warnings: list[str] = []
        if favourite_gap_pct is not None and favourite_gap_pct < 0.08:
            warnings.append("1X2 market is tightly priced")
        if favourite_label and favourite_label in one_x_two:
            favourite = one_x_two[favourite_label]
            if favourite.min_odds is not None and favourite.max_odds is not None:
                if favourite.max_odds - favourite.min_odds >= 0.12:
                    warnings.append("Favourite price dispersion is high across bookmakers")
        if asian_handicap:
            home = asian_handicap.get("team1")
            away = asian_handicap.get("team2")
            if home and away and abs(home.movement_score - away.movement_score) <= 1:
                warnings.append("Asian handicap movement is mixed")
        return warnings

    async def scrape(self) -> MarketSignalsData:
        try:
            match_info = await self._find_match_url()
            if not match_info:
                return MarketSignalsData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Match not found on Betexplorer: {self.team1} vs {self.team2}",
                )
            match_url = match_info["url"]
            event_status = match_info.get("status", "")

            _NO_ODDS_STATUSES = {
                "https://schema.org/EventPostponed",
                "https://schema.org/EventCancelled",
                "EventPostponed",
                "EventCancelled",
            }
            if event_status in _NO_ODDS_STATUSES:
                return MarketSignalsData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Match {self.team1} vs {self.team2} is postponed/cancelled — no odds available",
                )

            match_id_m = re.search(r"/([A-Za-z0-9]+)/?$", match_url.rstrip("/"))
            if not match_id_m:
                return MarketSignalsData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message="Could not extract match ID from URL",
                )
            match_id = match_id_m.group(1)

            html_1x2, html_ou, html_bts, html_ah = await asyncio.gather(
                self._fetch_best_odds_html(match_id, "1x2", match_url),
                self._fetch_best_odds_html(match_id, "ou", match_url),
                self._fetch_best_odds_html(match_id, "bts", match_url),
                self._fetch_best_odds_html(match_id, "ah", match_url),
                return_exceptions=True,
            )

            one_x_two: dict[str, MarketSelectionSignal] = {}
            over_under_2_5: dict[str, MarketSelectionSignal] = {}
            btts: dict[str, MarketSelectionSignal] = {}
            asian_handicap: dict[str, MarketSelectionSignal] = {}
            asian_handicap_line = None

            if not isinstance(html_1x2, Exception):
                one_x_two = self._aggregate_market_rows(
                    self._parse_1x2_rows(html_1x2),
                    ["team1", "draw", "team2"],
                )

            if not isinstance(html_ou, Exception):
                soup_ou = BeautifulSoup(html_ou, "lxml")
                over_under_2_5 = self._aggregate_market_rows(
                    self._parse_table_rows(soup_ou.select_one('table[data-handicap="2.50"]')),
                    ["over", "under"],
                )

            if not isinstance(html_bts, Exception):
                soup_bts = BeautifulSoup(html_bts, "lxml")
                btts = self._aggregate_market_rows(
                    self._parse_table_rows(soup_bts.select_one("table[data-handicap]")),
                    ["yes", "no"],
                )

            if not isinstance(html_ah, Exception):
                asian_handicap_line, table = self._pick_liquid_table(html_ah)
                asian_handicap = self._aggregate_market_rows(
                    self._parse_table_rows(table),
                    ["team1", "team2"],
                )

            favourite_label = None
            favourite_gap_pct = None
            ranked_1x2 = sorted(
                (
                    (label, signal.implied_prob)
                    for label, signal in one_x_two.items()
                    if signal.implied_prob is not None
                ),
                key=lambda item: item[1],
                reverse=True,
            )
            if ranked_1x2:
                favourite_label = ranked_1x2[0][0]
                if len(ranked_1x2) >= 2:
                    favourite_gap_pct = round(ranked_1x2[0][1] - ranked_1x2[1][1], 4)

            scrape_success = bool(one_x_two)
            error_message = None if scrape_success else "Could not parse market consensus from Betexplorer"

            return MarketSignalsData(
                team1_name=self.team1,
                team2_name=self.team2,
                one_x_two=one_x_two,
                over_under_2_5=over_under_2_5,
                btts=btts,
                asian_handicap_line=asian_handicap_line,
                asian_handicap=asian_handicap,
                favourite_label=favourite_label,
                favourite_gap_pct=favourite_gap_pct,
                market_warnings=self._build_warnings(
                    one_x_two,
                    favourite_label,
                    favourite_gap_pct,
                    asian_handicap,
                ),
                scrape_success=scrape_success,
                error_message=error_message,
            )
        except Exception as exc:
            logger.error("Market signals scraper critical failure: %s", exc, exc_info=True)
            return MarketSignalsData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
