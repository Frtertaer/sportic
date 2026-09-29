"""Fixture congestion and rest-day signals around the target upcoming match."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from models import ScheduleLoadData, TeamScheduleLoadSummary
from scrapers.base import BaseScraper
from scrapers.flashscore import get_search_aliases
from scrapers.fotmob import resolve_fotmob_team
from scrapers.match_context import MatchContextScraper

logger = logging.getLogger(__name__)

_SHORT_REST_HOURS = 72.0
_NEXT_SHORT_TURNAROUND_HOURS = 96.0
_REST_EDGE_WARNING_HOURS = 36.0


class ScheduleLoadScraper(BaseScraper):
    """Measure rest, congestion, and next-fixture pressure for both teams."""

    DOMAIN = "fotmob.com"

    @staticmethod
    def _parse_fixture_dt(fixture: dict) -> Optional[datetime]:
        utc_time = ((fixture.get("status") or {}).get("utcTime")) or ""
        if not utc_time:
            return None
        try:
            dt = datetime.fromisoformat(utc_time.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _is_finished(fixture: dict) -> bool:
        return bool((fixture.get("status") or {}).get("finished"))

    @staticmethod
    def _opponent_name(fixture: dict, team_id: int) -> Optional[str]:
        home = fixture.get("home") or {}
        away = fixture.get("away") or {}
        opponent = fixture.get("opponent") or {}
        if home.get("id") == team_id:
            return away.get("name") or opponent.get("name")
        if away.get("id") == team_id:
            return home.get("name") or opponent.get("name")
        return opponent.get("name") or away.get("name") or home.get("name")

    @classmethod
    def _summarize_team_load(
        cls,
        team_data: dict,
        *,
        team_id: int,
        target_dt: datetime,
    ) -> TeamScheduleLoadSummary:
        fixtures = (
            team_data.get("fixtures", {})
            .get("allFixtures", {})
            .get("fixtures", [])
        )
        previous_fixtures: list[tuple[datetime, dict]] = []
        next_fixtures: list[tuple[datetime, dict]] = []

        for fixture in fixtures:
            fixture_dt = cls._parse_fixture_dt(fixture)
            if fixture_dt is None:
                continue
            if cls._is_finished(fixture) and fixture_dt < target_dt:
                previous_fixtures.append((fixture_dt, fixture))
            elif not cls._is_finished(fixture) and fixture_dt > target_dt:
                next_fixtures.append((fixture_dt, fixture))

        previous_fixtures.sort(key=lambda item: item[0])
        next_fixtures.sort(key=lambda item: item[0])

        last_match = previous_fixtures[-1] if previous_fixtures else None
        next_match = next_fixtures[0] if next_fixtures else None
        last_7d_cutoff = target_dt - timedelta(days=7)
        next_7d_cutoff = target_dt + timedelta(days=7)

        matches_last_7d = sum(
            1
            for fixture_dt, _fixture in previous_fixtures
            if fixture_dt >= last_7d_cutoff
        )
        matches_next_7d = sum(
            1
            for fixture_dt, _fixture in next_fixtures
            if fixture_dt <= next_7d_cutoff
        )

        rest_hours = None
        last_match_utc = None
        last_opponent = None
        if last_match is not None:
            last_dt, last_fixture = last_match
            rest_hours = round((target_dt - last_dt).total_seconds() / 3600, 1)
            last_match_utc = last_dt.isoformat()
            last_opponent = cls._opponent_name(last_fixture, team_id)

        next_match_utc = None
        next_opponent = None
        next_gap_hours = None
        if next_match is not None:
            next_dt, next_fixture = next_match
            next_gap_hours = round((next_dt - target_dt).total_seconds() / 3600, 1)
            next_match_utc = next_dt.isoformat()
            next_opponent = cls._opponent_name(next_fixture, team_id)

        return TeamScheduleLoadSummary(
            rest_hours=rest_hours,
            matches_last_7d=matches_last_7d,
            matches_next_7d=matches_next_7d,
            last_match_utc=last_match_utc,
            last_opponent=last_opponent,
            next_match_utc=next_match_utc,
            next_opponent=next_opponent,
            short_rest=(rest_hours < _SHORT_REST_HOURS) if rest_hours is not None else None,
            next_short_turnaround=(
                next_gap_hours < _NEXT_SHORT_TURNAROUND_HOURS
            ) if next_gap_hours is not None else None,
        )

    @staticmethod
    def _build_warnings(
        team1_name: str,
        team2_name: str,
        team1_summary: TeamScheduleLoadSummary,
        team2_summary: TeamScheduleLoadSummary,
        rest_gap_hours: Optional[float],
    ) -> list[str]:
        warnings: list[str] = []
        for team_name, summary in ((team1_name, team1_summary), (team2_name, team2_summary)):
            if summary.short_rest and summary.rest_hours is not None:
                warnings.append(
                    f"{team_name} on short rest ({summary.rest_hours:.0f}h since previous match)"
                )
            if summary.matches_last_7d >= 3:
                warnings.append(
                    f"{team_name} has played {summary.matches_last_7d} matches in the last 7 days"
                )
            if summary.next_short_turnaround and summary.next_match_utc:
                warnings.append(
                    f"{team_name} has another fixture within 4 days after this match"
                )

        if rest_gap_hours is not None and abs(rest_gap_hours) >= _REST_EDGE_WARNING_HOURS:
            if rest_gap_hours > 0:
                warnings.append(
                    f"{team1_name} has about {rest_gap_hours:.0f}h more rest than {team2_name}"
                )
            else:
                warnings.append(
                    f"{team2_name} has about {abs(rest_gap_hours):.0f}h more rest than {team1_name}"
                )
        return warnings

    async def scrape(self) -> ScheduleLoadData:
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
                return ScheduleLoadData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Fotmob: {', '.join(missing)}",
                )

            data1, data2 = resolved_t1.data, resolved_t2.data
            team1_name = data1.get("details", {}).get("name", self.team1)
            team2_name = data2.get("details", {}).get("name", self.team2)

            fixture = MatchContextScraper._select_fixture(
                data1,
                team1_id=resolved_t1.team_id,
                team2_id=resolved_t2.team_id,
                team1_names=[self.team1, team1_name],
                team2_names=[self.team2, team2_name],
            )
            if not fixture:
                return ScheduleLoadData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    scrape_success=False,
                    error_message="Upcoming fixture not found on Fotmob",
                )

            target_dt = self._parse_fixture_dt(fixture)
            if target_dt is None:
                return ScheduleLoadData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    scrape_success=False,
                    error_message="Upcoming fixture kickoff is missing on Fotmob",
                )

            team1_summary, team2_summary = await asyncio.gather(
                asyncio.to_thread(
                    self._summarize_team_load,
                    data1,
                    team_id=resolved_t1.team_id,
                    target_dt=target_dt,
                ),
                asyncio.to_thread(
                    self._summarize_team_load,
                    data2,
                    team_id=resolved_t2.team_id,
                    target_dt=target_dt,
                ),
            )

            rest_gap_hours = None
            if team1_summary.rest_hours is not None and team2_summary.rest_hours is not None:
                rest_gap_hours = round(team1_summary.rest_hours - team2_summary.rest_hours, 1)

            return ScheduleLoadData(
                team1_name=team1_name,
                team2_name=team2_name,
                team1=team1_summary,
                team2=team2_summary,
                rest_gap_hours=rest_gap_hours,
                load_warnings=self._build_warnings(
                    team1_name,
                    team2_name,
                    team1_summary,
                    team2_summary,
                    rest_gap_hours,
                ),
                scrape_success=True,
            )
        except Exception as exc:
            logger.error("Schedule/load scraper critical failure: %s", exc, exc_info=True)
            return ScheduleLoadData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
