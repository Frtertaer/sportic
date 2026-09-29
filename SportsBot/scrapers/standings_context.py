"""League-table context scraper backed by Fotmob overview table data."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from models import StandingsContextData, TableEntrySummary
from scrapers.base import BaseScraper
from scrapers.flashscore import get_search_aliases
from scrapers.fotmob import resolve_fotmob_team

logger = logging.getLogger(__name__)


class StandingsContextScraper(BaseScraper):
    """Extract overall and venue-split table context for both teams."""

    DOMAIN = "fotmob.com"

    @staticmethod
    def _find_matching_table(tables: object, team1_id: int, team2_id: int) -> Optional[dict]:
        if not isinstance(tables, list):
            return None
        for table_block in tables:
            data = table_block.get("data") if isinstance(table_block, dict) else None
            if not isinstance(data, dict):
                continue
            rows = ((data.get("table") or {}).get("all")) or []
            ids = {
                row.get("id")
                for row in rows
                if isinstance(row, dict)
            }
            if team1_id in ids and team2_id in ids:
                return data
        return None

    @staticmethod
    def _lookup_row(rows: object, team_id: int) -> Optional[dict]:
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and row.get("id") == team_id:
                return row
        return None

    @staticmethod
    def _qualification_label(row: dict, legend: object) -> tuple[Optional[str], Optional[str]]:
        if not isinstance(row, dict):
            return None, None
        color = row.get("qualColor")
        idx = row.get("idx")
        if not isinstance(legend, list):
            return None, color
        zero_based_idx = idx - 1 if isinstance(idx, int) else None
        for item in legend:
            if not isinstance(item, dict):
                continue
            indices = item.get("indices")
            if zero_based_idx is not None and isinstance(indices, list) and zero_based_idx in indices:
                return item.get("title"), color
            if color and item.get("color") == color:
                return item.get("title"), color
        return None, color

    @classmethod
    def _build_summary(cls, row: Optional[dict], legend: object) -> TableEntrySummary:
        if not isinstance(row, dict):
            return TableEntrySummary()
        qualification_label, qualification_color = cls._qualification_label(row, legend)
        return TableEntrySummary(
            position=row.get("idx"),
            played=row.get("played"),
            points=row.get("pts"),
            goal_difference=row.get("goalConDiff"),
            scores_str=row.get("scoresStr"),
            qualification_label=qualification_label,
            qualification_color=qualification_color,
        )

    async def scrape(self) -> StandingsContextData:
        try:
            (resolved_t1, searched_t1), (resolved_t2, searched_t2) = await asyncio.gather(
                resolve_fotmob_team(
                    self,
                    self.team1,
                    search_aliases=get_search_aliases(self.team1),
                    log=logger,
                ),
                resolve_fotmob_team(
                    self,
                    self.team2,
                    search_aliases=get_search_aliases(self.team2),
                    log=logger,
                ),
            )

            if not resolved_t1 or not resolved_t2:
                missing = []
                if not resolved_t1:
                    missing.append(f"{self.team1} (searched: {', '.join(searched_t1)})")
                if not resolved_t2:
                    missing.append(f"{self.team2} (searched: {', '.join(searched_t2)})")
                return StandingsContextData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Fotmob: {', '.join(missing)}",
                )

            data1, data2 = resolved_t1.data, resolved_t2.data
            team1_name = data1.get("details", {}).get("name", self.team1)
            team2_name = data2.get("details", {}).get("name", self.team2)

            table_data = self._find_matching_table(
                (data1.get("overview") or {}).get("table"),
                resolved_t1.team_id,
                resolved_t2.team_id,
            )
            if not table_data:
                return StandingsContextData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    scrape_success=False,
                    error_message="Could not find shared standings table on Fotmob",
                )

            legend = table_data.get("legend")
            table = table_data.get("table") or {}
            team1_overall_row = self._lookup_row(table.get("all"), resolved_t1.team_id)
            team2_overall_row = self._lookup_row(table.get("all"), resolved_t2.team_id)
            team1_home_row = self._lookup_row(table.get("home"), resolved_t1.team_id)
            team1_away_row = self._lookup_row(table.get("away"), resolved_t1.team_id)
            team2_home_row = self._lookup_row(table.get("home"), resolved_t2.team_id)
            team2_away_row = self._lookup_row(table.get("away"), resolved_t2.team_id)

            scrape_success = bool(team1_overall_row and team2_overall_row)
            error_message = None
            if not scrape_success:
                error_message = "Could not parse standings rows for both teams"

            team1_overall = self._build_summary(team1_overall_row, legend)
            team2_overall = self._build_summary(team2_overall_row, legend)

            point_gap = None
            position_gap = None
            team1_above_team2 = None
            if team1_overall.points is not None and team2_overall.points is not None:
                point_gap = team1_overall.points - team2_overall.points
            if team1_overall.position is not None and team2_overall.position is not None:
                position_gap = team2_overall.position - team1_overall.position
                team1_above_team2 = team1_overall.position < team2_overall.position

            return StandingsContextData(
                team1_name=team1_name,
                team2_name=team2_name,
                league_name=table_data.get("leagueName"),
                team1_overall=team1_overall,
                team2_overall=team2_overall,
                team1_home=self._build_summary(team1_home_row, legend),
                team1_away=self._build_summary(team1_away_row, legend),
                team2_home=self._build_summary(team2_home_row, legend),
                team2_away=self._build_summary(team2_away_row, legend),
                point_gap=point_gap,
                position_gap=position_gap,
                team1_above_team2=team1_above_team2,
                scrape_success=scrape_success,
                error_message=error_message,
            )
        except Exception as exc:
            logger.error("Standings context scraper critical failure: %s", exc, exc_info=True)
            return StandingsContextData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
