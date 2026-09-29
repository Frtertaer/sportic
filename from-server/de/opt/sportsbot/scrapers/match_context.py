"""Supplementary scraper for upcoming fixture context and venue metadata."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from models import MatchContextData
from scrapers.base import BaseScraper
from scrapers.flashscore import get_search_aliases
from scrapers.fotmob import _names_match, resolve_fotmob_team

logger = logging.getLogger(__name__)

_KNOCKOUT_KEYWORDS = (
    "cup",
    "playoff",
    "play-off",
    "knockout",
    "quarter",
    "semi",
    "final",
    "round of 16",
    "round of 32",
    "last 16",
    "last 32",
)
_SECOND_LEG_KEYWORDS = (
    "2nd leg",
    "second leg",
    "leg 2",
)


class MatchContextScraper(BaseScraper):
    """Extract venue and competition context for the specific upcoming fixture."""

    DOMAIN = "fotmob.com"

    @staticmethod
    def _fixture_kickoff_key(fixture: dict) -> tuple[int, str]:
        utc_time = ((fixture.get("status") or {}).get("utcTime")) or ""
        if not utc_time:
            return (1, "")
        try:
            dt = datetime.fromisoformat(utc_time.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (0, dt.astimezone(timezone.utc).isoformat())
        except ValueError:
            return (1, utc_time)

    @staticmethod
    def _extract_competition_name(fixture: dict, team_data: dict) -> Optional[str]:
        competition = (
            (fixture.get("tournament") or {}).get("name")
            or fixture.get("leagueName")
            or fixture.get("competition")
        )
        if competition:
            return competition

        overview = team_data.get("overview", {})
        for entry in overview.get("teamForm", []):
            competition = (
                (entry.get("tournament") or {}).get("name")
                or entry.get("leagueName")
                or entry.get("competition")
            )
            if competition:
                return competition
        return None

    @staticmethod
    def _extract_stage(fixture: dict) -> Optional[str]:
        status = fixture.get("status") or {}
        reason = status.get("reason") or {}
        return (
            fixture.get("roundName")
            or fixture.get("round")
            or fixture.get("statusText")
            or reason.get("long")
            or reason.get("longKey")
            or reason.get("short")
            or reason.get("shortKey")
        )

    @staticmethod
    def _extract_referee(fixture: dict) -> Optional[str]:
        """Extract referee name from Fotmob fixture data."""
        # Fotmob may include referee in various places
        for key in ("referee", "refereeId"):
            val = fixture.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                name = val.get("name") or val.get("text")
                if name:
                    return str(name).strip()
        # Sometimes in content.matchFacts
        content = fixture.get("content") or {}
        match_facts = content.get("matchFacts") or {}
        info_box = match_facts.get("infoBox") or {}
        ref_info = info_box.get("Referee") or info_box.get("referee")
        if isinstance(ref_info, dict):
            name = ref_info.get("text") or ref_info.get("name")
            if name:
                return str(name).strip()
        if isinstance(ref_info, str) and ref_info.strip():
            return ref_info.strip()
        return None

    @staticmethod
    def _extract_neutral_flag(fixture: dict) -> Optional[bool]:
        for key in ("isNeutralGround", "neutralGround", "neutralVenue", "isNeutralVenue"):
            value = fixture.get(key)
            if isinstance(value, bool):
                return value
        venue = fixture.get("venue") or {}
        for key in ("neutral", "isNeutralGround", "isNeutralVenue"):
            value = venue.get(key)
            if isinstance(value, bool):
                return value
        return None

    @classmethod
    def _fixture_matches(
        cls,
        fixture: dict,
        *,
        team1_id: int,
        team2_id: int,
        team1_names: list[str],
        team2_names: list[str],
    ) -> bool:
        home = fixture.get("home") or {}
        away = fixture.get("away") or {}
        opponent = fixture.get("opponent") or {}

        home_id = home.get("id")
        away_id = away.get("id")
        opponent_id = opponent.get("id")
        if home_id == team1_id and away_id == team2_id:
            return True
        if home_id == team2_id and away_id == team1_id:
            return True
        if opponent_id == team2_id:
            return True

        return (
            _names_match(team1_names, [home.get("name", ""), away.get("name", "")])
            and _names_match(team2_names, [home.get("name", ""), away.get("name", ""), opponent.get("name", "")])
        )

    @classmethod
    def _select_fixture(
        cls,
        team_data: dict,
        *,
        team1_id: int,
        team2_id: int,
        team1_names: list[str],
        team2_names: list[str],
    ) -> Optional[dict]:
        fixtures = (
            team_data.get("fixtures", {})
            .get("allFixtures", {})
            .get("fixtures", [])
        )
        candidates = [
            fixture for fixture in fixtures
            if not (fixture.get("status") or {}).get("finished")
            and cls._fixture_matches(
                fixture,
                team1_id=team1_id,
                team2_id=team2_id,
                team1_names=team1_names,
                team2_names=team2_names,
            )
        ]
        if not candidates:
            return None
        candidates.sort(key=cls._fixture_kickoff_key)
        return candidates[0]

    async def scrape(self) -> MatchContextData:
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
                return MatchContextData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Fotmob: {', '.join(missing)}",
                )

            data1, data2 = resolved_t1.data, resolved_t2.data
            team1_name = data1.get("details", {}).get("name", self.team1)
            team2_name = data2.get("details", {}).get("name", self.team2)
            fixture = self._select_fixture(
                data1,
                team1_id=resolved_t1.team_id,
                team2_id=resolved_t2.team_id,
                team1_names=[self.team1, team1_name],
                team2_names=[self.team2, team2_name],
            )
            if not fixture:
                return MatchContextData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    scrape_success=False,
                    error_message="Upcoming fixture not found on Fotmob",
                )

            home = fixture.get("home") or {}
            away = fixture.get("away") or {}
            venue = fixture.get("venue") or {}
            stage = self._extract_stage(fixture)
            competition_name = self._extract_competition_name(fixture, data1)
            stage_blob = " ".join(part for part in (competition_name, stage) if part).lower()

            # Try to extract referee from fixture data
            referee_name = self._extract_referee(fixture)

            return MatchContextData(
                team1_name=team1_name,
                team2_name=team2_name,
                competition_name=competition_name,
                kickoff_utc=((fixture.get("status") or {}).get("utcTime")),
                venue_name=venue.get("name") or venue.get("longName"),
                referee_name=referee_name,
                is_neutral_venue=self._extract_neutral_flag(fixture),
                is_team1_home=(home.get("id") == resolved_t1.team_id) if home.get("id") is not None else None,
                stage=stage,
                is_knockout=any(keyword in stage_blob for keyword in _KNOCKOUT_KEYWORDS) or None,
                is_second_leg=any(keyword in stage_blob for keyword in _SECOND_LEG_KEYWORDS) or None,
                scrape_success=True,
            )
        except Exception as exc:
            logger.error("Match context scraper critical failure: %s", exc, exc_info=True)
            return MatchContextData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
