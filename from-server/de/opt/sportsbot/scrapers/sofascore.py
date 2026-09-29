"""
Squad/injury scraper backed by Fotmob API (replacing Sofascore).
"""
from __future__ import annotations

import asyncio
import logging

from models import SofascoreData, Player, PlayerStatus
from scrapers.base import BaseScraper
from scrapers.flashscore import get_search_aliases
from scrapers.fotmob import resolve_fotmob_team

logger = logging.getLogger(__name__)

# Position group titles from Fotmob squad API
_POSITION_MAP = {
    "coach": "Coach",
    "keepers": "GK",
    "defenders": "DEF",
    "midfielders": "MID",
    "forwards": "FWD",
    "attackers": "FWD",
}


class SofascoreScraper(BaseScraper):
    """Uses Fotmob API instead of Sofascore."""
    DOMAIN = "fotmob.com"

    # ── Squad parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_squad(data: dict) -> list[Player]:
        """
        Extract players from squad.squad groups.
        Each group has a 'title' (keepers, defenders, etc.) and 'members'.
        member.injury: null = available; dict with 'injuryType' = injured/suspended.
        """
        players: list[Player] = []
        groups = data.get("squad", {}).get("squad", [])
        for group in groups:
            title = group.get("title", "").lower()
            position = _POSITION_MAP.get(title, title.upper()[:3])
            if title == "coach":
                continue  # skip coaching staff
            for member in group.get("members", []):
                injury_info = member.get("injury")
                if injury_info and isinstance(injury_info, dict):
                    inj_type = (injury_info.get("injuryType") or "").lower()
                    detail = injury_info.get("injuryDescription") or injury_info.get("injuryType", "Injury")
                    if "suspend" in inj_type:
                        status = PlayerStatus.SUSPENDED
                        detail = "Suspension"
                    elif inj_type:
                        status = PlayerStatus.INJURED
                    else:
                        status = PlayerStatus.DOUBTFUL
                        detail = "Doubtful"
                else:
                    status = PlayerStatus.AVAILABLE
                    detail = None

                players.append(Player(
                    name=member.get("name", "Unknown"),
                    position=position,
                    status=status,
                    injury_detail=detail,
                ))
        return players

    # ── Predicted lineup (last match form as proxy) ───────────────────────────

    @staticmethod
    def _extract_last_lineup(data: dict) -> list[str]:
        """
        Use the teamForm (last 5 matches) to get the most recent match players.
        Fotmob doesn't expose lineup directly in team API, so we return
        top-rated available players as a proxy lineup.
        """
        players: list[tuple[str, float]] = []
        groups = data.get("squad", {}).get("squad", [])
        for group in groups:
            title = group.get("title", "").lower()
            if title == "coach":
                continue
            for member in group.get("members", []):
                if not member.get("injury"):  # available players only
                    rating = member.get("rating", 0) or 0
                    players.append((member.get("name", ""), float(rating)))
        # Sort by rating descending, take top 11
        players.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in players[:11]]

    # ── Main entry point ──────────────────────────────────────────────────────

    async def scrape(self) -> SofascoreData:
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
                return SofascoreData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Fotmob: {', '.join(missing)}",
                )

            data1, data2 = resolved_t1.data, resolved_t2.data
            t1_name = data1.get("details", {}).get("name", self.team1)
            t2_name = data2.get("details", {}).get("name", self.team2)

            t1_squad = self._parse_squad(data1)
            t2_squad = self._parse_squad(data2)

            t1_lineup = self._extract_last_lineup(data1)
            t2_lineup = self._extract_last_lineup(data2)

            t1_absences = [p for p in t1_squad if p.status != PlayerStatus.AVAILABLE]
            t2_absences = [p for p in t2_squad if p.status != PlayerStatus.AVAILABLE]

            return SofascoreData(
                team1_name=t1_name,
                team2_name=t2_name,
                team1_squad=t1_squad,
                team2_squad=t2_squad,
                team1_predicted_lineup=t1_lineup,
                team2_predicted_lineup=t2_lineup,
                team1_key_absences=t1_absences,
                team2_key_absences=t2_absences,
                scrape_success=True,
            )

        except Exception as exc:
            logger.error("Fotmob squad scraper critical failure: %s", exc, exc_info=True)
            return SofascoreData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
