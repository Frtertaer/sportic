"""
Form/H2H scraper backed by Fotmob API (replacing Flashscore).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from models import FlashscoreData, RecentMatch, H2HRecord, MatchResult
from scrapers.base import BaseScraper
from scrapers.fotmob import resolve_fotmob_team

logger = logging.getLogger(__name__)
_SEARCH_ALIASES = {
    # Bundesliga
    "Bayern Munich":              ["Bayern München", "FC Bayern München", "FC Bayern"],
    "Borussia Dortmund":          ["BVB", "BVB 09"],
    "Borussia Mönchengladbach":   ["Gladbach", "Mönchengladbach"],
    "Bayer Leverkusen":           ["Leverkusen"],
    "RB Leipzig":                 ["RB Leipzig", "Rasenball Leipzig"],
    "TSG Hoffenheim":             ["Hoffenheim", "1899 Hoffenheim"],
    "Eintracht Frankfurt":        ["Frankfurt"],
    "VfB Stuttgart":              ["Stuttgart"],
    "Werder Bremen":              ["SV Werder Bremen"],
    "Hamburger SV":               ["HSV", "Hamburg"],
    "1. FC Köln":                 ["FC Köln", "Cologne"],
    "Union Berlin":               ["1. FC Union Berlin"],
    "Hertha BSC":                 ["Hertha Berlin"],
    # Premier League
    "Manchester City":            ["Man City", "MCFC"],
    "Manchester United":          ["Man Utd", "Man United", "MUFC"],
    "Tottenham Hotspur":          ["Spurs", "Tottenham", "THFC"],
    "Wolverhampton Wanderers":    ["Wolves"],
    "Brighton & Hove Albion":     ["Brighton"],
    "Nottingham Forest":          ["Nottm Forest"],
    "Newcastle United":           ["Newcastle", "NUFC"],
    "West Ham United":            ["West Ham"],
    "Leicester City":             ["Leicester"],
    "Sheffield United":           ["Sheff Utd"],
    "Aston Villa":                ["Villa", "AVFC"],
    # Serie A
    "Inter Milan":                ["FC Internazionale", "Internazionale", "Inter"],
    "AC Milan":                   ["Milan"],
    "Juventus":                   ["Juventus FC", "Juve"],
    "Atletico Madrid":            ["Atlético Madrid", "Atletico de Madrid"],
    # LaLiga
    "Athletic Club":              ["Athletic Bilbao", "Athletic de Bilbao"],
    "Real Sociedad":              ["Real Sociedad de Fútbol"],
    # Ligue 1
    "Paris Saint-Germain":        ["PSG", "Paris SG", "Paris Saint Germain"],
    "Olympique Marseille":        ["Marseille", "OM"],
    "Olympique Lyonnais":         ["Lyon", "OL"],
    "Stade Rennais":              ["Rennes"],
    # Portugal
    "SL Benfica":                 ["Benfica", "Sport Lisboa e Benfica"],
    "FC Porto":                   ["Porto"],
    "Sporting CP":                ["Sporting Lisboa", "Sporting Lisbon"],
    # Netherlands
    "AFC Ajax":                   ["Ajax", "Ajax Amsterdam"],
    "PSV Eindhoven":              ["PSV"],
    # Turkey
    "Galatasaray SK":             ["Galatasaray"],
    "Fenerbahçe SK":              ["Fenerbahce", "Fenerbahçe"],
    "Beşiktaş JK":                ["Besiktas", "Beşiktaş"],
    # Eastern Europe
    "Shakhtar Donetsk":           ["Shakhtar", "FC Shakhtar Donetsk"],
    "Dynamo Kyiv":                ["Dynamo Kiev", "FC Dynamo Kyiv"],
    "Red Star Belgrade":          ["Crvena Zvezda", "FK Crvena zvezda"],
    "Dinamo Zagreb":              ["GNK Dinamo Zagreb"],
    # Russia
    "Zenit St. Petersburg":       ["Zenit", "FC Zenit", "Zenit Petersburg"],
    "Spartak Moscow":             ["Spartak", "FC Spartak Moscow"],
    "CSKA Moscow":                ["CSKA", "PFC CSKA Moscow"],
    # Nordics
    "Malmö FF":                   ["Malmo FF", "Malmö"],
    "FK Bodø/Glimt":              ["Bodo/Glimt", "Bodø/Glimt"],
}

# Reverse map: alias → canonical + all siblings.
# e.g. "West Ham" → ["West Ham United"] so the search can find the team.
_REVERSE_SEARCH_ALIASES: dict[str, list[str]] = {}
for _canonical, _aliases in _SEARCH_ALIASES.items():
    for _alias in _aliases:
        _key = _alias.lower()
        if _key not in _REVERSE_SEARCH_ALIASES:
            _REVERSE_SEARCH_ALIASES[_key] = []
        if _canonical not in _REVERSE_SEARCH_ALIASES[_key]:
            _REVERSE_SEARCH_ALIASES[_key].append(_canonical)


def get_search_aliases(team_name: str) -> list[str]:
    """Get search aliases for a team name, supporting both canonical and reverse lookups."""
    direct = _SEARCH_ALIASES.get(team_name, [])
    reverse = _REVERSE_SEARCH_ALIASES.get(team_name.lower(), [])
    return direct + reverse


class FlashscoreScraper(BaseScraper):
    """Uses Fotmob API instead of Flashscore."""
    DOMAIN = "fotmob.com"

    # ── Form extraction ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_form(data: dict, team_id: int) -> list[RecentMatch]:
        """Extract last 10 matches from teamForm list in overview."""
        matches: list[RecentMatch] = []
        team_form = data.get("overview", {}).get("teamForm", [])
        for entry in team_form[:10]:
            try:
                tooltip = entry.get("tooltipText", {})
                home_id = entry.get("home", {}).get("id")
                is_home = home_id == team_id
                home_score = tooltip.get("homeScore", 0)
                away_score = tooltip.get("awayScore", 0)
                opponent = (
                    tooltip.get("awayTeam", "") if is_home
                    else tooltip.get("homeTeam", "")
                )
                rs = entry.get("resultString", "").upper()
                result = (
                    MatchResult.WIN if rs == "W"
                    else MatchResult.DRAW if rs == "D"
                    else MatchResult.LOSS
                )
                date_str = (entry.get("date", {}) or {}).get("utcTime", "")[:10]
                matches.append(RecentMatch(
                    date=date_str,
                    opponent=opponent,
                    result=result,
                    score=f"{home_score}-{away_score}",
                    home_away="H" if is_home else "A",
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return matches

    # ── H2H extraction ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_h2h(data1: dict, team2_id: int) -> list[H2HRecord]:
        """Find matches between team1 and team2 in team1's fixture list."""
        records: list[H2HRecord] = []
        fixtures = (
            data1.get("fixtures", {})
            .get("allFixtures", {})
            .get("fixtures", [])
        )
        for fx in fixtures:
            try:
                if not fx.get("status", {}).get("finished"):
                    continue
                home = fx.get("home", {})
                away = fx.get("away", {})
                opp = fx.get("opponent", {})
                if opp.get("id") != team2_id:
                    continue
                score = f"{home.get('score', '?')}-{away.get('score', '?')}"
                date_str = (fx.get("status", {}) or {}).get("utcTime", "")[:10]
                records.append(H2HRecord(
                    date=date_str,
                    home_team=home.get("name", "?"),
                    away_team=away.get("name", "?"),
                    score=score,
                ))
            except (KeyError, TypeError):
                continue
        return records[-10:]  # last 10

    # ── Goal averages ─────────────────────────────────────────────────────────

    @staticmethod
    def _goal_averages(matches: list[RecentMatch]) -> tuple[float, float]:
        if not matches:
            return 0.0, 0.0
        scored, conceded = [], []
        for m in matches:
            parts = m.score.split("-")
            if len(parts) != 2:
                continue
            try:
                h_g, a_g = int(parts[0]), int(parts[1])
                if m.home_away == "H":
                    scored.append(h_g)
                    conceded.append(a_g)
                else:
                    scored.append(a_g)
                    conceded.append(h_g)
            except ValueError:
                pass
        avg_s = round(sum(scored) / len(scored), 2) if scored else 0.0
        avg_c = round(sum(conceded) / len(conceded), 2) if conceded else 0.0
        return avg_s, avg_c

    # ── League name extraction ────────────────────────────────────────────────

    @staticmethod
    def _extract_league_name(data: dict) -> Optional[str]:
        """Extract tournament/league name from first teamForm entry."""
        team_form = data.get("overview", {}).get("teamForm", [])
        if not team_form:
            return None
        entry = team_form[0]
        return (
            entry.get("tournamentName")
            or (entry.get("tournament") or {}).get("name")
            or entry.get("leagueName")
            or entry.get("competition")
            or None
        )

    # ── Main entry point ──────────────────────────────────────────────────────

    async def scrape(self) -> FlashscoreData:
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
                return FlashscoreData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Fotmob: {', '.join(missing)}",
                )

            t1_id, t2_id = resolved_t1.team_id, resolved_t2.team_id
            data1, data2 = resolved_t1.data, resolved_t2.data
            t1_name = data1.get("details", {}).get("name", self.team1)
            t2_name = data2.get("details", {}).get("name", self.team2)
            league_name = self._extract_league_name(data1)

            t1_form = self._extract_form(data1, t1_id)
            t2_form = self._extract_form(data2, t2_id)
            h2h = self._extract_h2h(data1, t2_id)

            t1_scored, t1_conceded = self._goal_averages(t1_form)
            t2_scored, t2_conceded = self._goal_averages(t2_form)

            return FlashscoreData(
                team1_name=t1_name,
                team2_name=t2_name,
                team1_form=t1_form,
                team2_form=t2_form,
                h2h_matches=h2h,
                team1_goals_scored_avg=t1_scored,
                team1_goals_conceded_avg=t1_conceded,
                team2_goals_scored_avg=t2_scored,
                team2_goals_conceded_avg=t2_conceded,
                league_name=league_name,
                scrape_success=True,
            )

        except Exception as exc:
            logger.error("Fotmob form scraper critical failure: %s", exc, exc_info=True)
            return FlashscoreData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
