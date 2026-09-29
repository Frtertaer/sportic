"""Advanced team-stat scraper backed by Fotmob team stats."""
from __future__ import annotations

import asyncio
import logging

from models import AdvancedStatsData, TeamAdvancedMetrics
from scrapers.base import BaseScraper
from scrapers.flashscore import get_search_aliases
from scrapers.fotmob import resolve_fotmob_team

logger = logging.getLogger(__name__)

_STAT_FIELD_MAP = {
    "rating_team": "rating",
    "expected_goals_team": "xg",
    "expected_goals_conceded_team": "xga",
    "_xg_diff_team": "xg_diff",
    "possession_percentage_team": "possession_pct",
    "ontarget_scoring_att_team": "shots_on_target_per_match",
    "big_chance_team": "big_chances",
    "touches_in_opp_box_team": "touches_in_opp_box",
    "corner_taken_team": "corners",
    "clean_sheet_team": "clean_sheets",
}


class AdvancedStatsScraper(BaseScraper):
    """Extract season-level advanced team metrics for both clubs."""

    DOMAIN = "fotmob.com"

    @staticmethod
    def _extract_metrics(data: dict) -> TeamAdvancedMetrics:
        values: dict[str, float | int] = {}
        for item in (data.get("stats") or {}).get("teams", []):
            participant = item.get("participant") or {}
            stat_info = participant.get("stat") or {}
            stat_name = stat_info.get("name") or item.get("stat")
            field_name = _STAT_FIELD_MAP.get(str(stat_name or ""))
            if not field_name:
                continue
            value = participant.get("value")
            rank = participant.get("rank")
            if isinstance(value, (int, float)):
                values[field_name] = value
            if isinstance(rank, int):
                values[f"{field_name}_rank"] = rank
        return TeamAdvancedMetrics(**values)

    @staticmethod
    def _has_useful_metrics(metrics: TeamAdvancedMetrics) -> bool:
        return any(
            getattr(metrics, field) is not None
            for field in (
                "xg",
                "xga",
                "xg_diff",
                "rating",
                "possession_pct",
                "shots_on_target_per_match",
            )
        )

    async def scrape(self) -> AdvancedStatsData:
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
                return AdvancedStatsData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Fotmob: {', '.join(missing)}",
                )

            data1, data2 = resolved_t1.data, resolved_t2.data
            team1_name = data1.get("details", {}).get("name", self.team1)
            team2_name = data2.get("details", {}).get("name", self.team2)
            team1_metrics = self._extract_metrics(data1)
            team2_metrics = self._extract_metrics(data2)

            scrape_success = self._has_useful_metrics(team1_metrics) and self._has_useful_metrics(team2_metrics)
            error_message = None
            if not scrape_success:
                error_message = "Could not parse advanced team stats from Fotmob"

            return AdvancedStatsData(
                team1_name=team1_name,
                team2_name=team2_name,
                team1_metrics=team1_metrics,
                team2_metrics=team2_metrics,
                scrape_success=scrape_success,
                error_message=error_message,
            )
        except Exception as exc:
            logger.error("Advanced stats scraper critical failure: %s", exc, exc_info=True)
            return AdvancedStatsData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
