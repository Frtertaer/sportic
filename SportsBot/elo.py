"""
Elo rating system for football teams.

Based on FiveThirtyEight/ClubElo methodology with football-specific adjustments:
- K-factor = 20 (responsive to recent form)
- Goal difference multiplier: big wins count more
- Home advantage: +65 Elo points for home team
- Mean reversion: ratings pull 1/3 toward 1500 at season start

Data source: football-data.co.uk CSV (same as backtest.py)

Usage:
    elo = EloSystem()
    await elo.initialize()  # loads from DB or builds from CSV
    r1, r2 = elo.get_ratings("Arsenal", "Chelsea")
    win_prob = elo.expected_score(r1 + HOME_ELO_BONUS, r2)
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── Elo Parameters ────────────────────────────────────────────────────────────
DEFAULT_ELO = 1500.0
K_FACTOR = 20.0
HOME_ELO_BONUS = 65.0  # ~7% win probability boost
SEASON_REVERSION_FACTOR = 1 / 3  # pull 1/3 toward mean at season start
GOAL_DIFF_EXPONENT = 0.7  # diminishing returns on goal margin

# ── CSV Data Source ───────────────────────────────────────────────────────────
CSV_BASE_URL = "https://www.football-data.co.uk/mmz4281"
CSV_LEAGUES = {
    "E0": "Premier League",
    "D1": "Bundesliga",
    "SP1": "La Liga",
    "I1": "Serie A",
    "F1": "Ligue 1",
    "E1": "Championship",
    "N1": "Eredivisie",
    "P1": "Primeira Liga",
}
# 4 seasons of data for stable Elo initialization
CSV_SEASONS = ["2122", "2223", "2324", "2425"]

DB_PATH = Path(__file__).parent / "sportsbot.db"


def _goal_diff_multiplier(goal_diff: int) -> float:
    """
    Multiplier for K based on goal difference.
    1-0 win → 1.0, 2-0 → 1.32, 3-0 → 1.55, 5-0 → 1.90
    """
    if goal_diff <= 1:
        return 1.0
    return (math.log(abs(goal_diff) + 1) / math.log(2)) ** GOAL_DIFF_EXPONENT


def expected_score(rating_a: float, rating_b: float) -> float:
    """Expected score (win probability) for team A vs team B."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def _normalize_team_name(name: str) -> str:
    """Normalize team name for consistent lookup."""
    return " ".join(name.strip().lower().split())


# ── Team name aliases for CSV → Fotmob mapping ──────────────────────────────
_CSV_ALIASES: dict[str, list[str]] = {
    "man united": ["manchester united", "man utd"],
    "man city": ["manchester city"],
    "tottenham": ["tottenham hotspur", "spurs"],
    "wolves": ["wolverhampton wanderers", "wolverhampton"],
    "brighton": ["brighton and hove albion", "brighton & hove albion"],
    "nott'm forest": ["nottingham forest", "nottm forest"],
    "newcastle": ["newcastle united"],
    "west ham": ["west ham united"],
    "leicester": ["leicester city"],
    "sheffield united": ["sheff utd", "sheffield utd"],
    "ipswich": ["ipswich town"],
    "luton": ["luton town"],
    "bayern munich": ["bayern munchen", "fc bayern", "fc bayern munchen"],
    "leverkusen": ["bayer leverkusen"],
    "dortmund": ["borussia dortmund", "bvb"],
    "gladbach": ["borussia monchengladbach", "monchengladbach"],
    "rb leipzig": ["rasenball leipzig", "leipzig"],
    "hoffenheim": ["tsg hoffenheim", "1899 hoffenheim"],
    "fc koln": ["1. fc koln", "cologne"],
    "union berlin": ["1. fc union berlin"],
    "hertha": ["hertha berlin", "hertha bsc"],
    "werder bremen": ["sv werder bremen", "bremen"],
    "atletico madrid": ["atletico de madrid", "atletico"],
    "athletic bilbao": ["athletic club", "athletic de bilbao"],
    "real sociedad": ["real sociedad de futbol"],
    "betis": ["real betis"],
    "celta": ["celta vigo", "rc celta"],
    "vallecano": ["rayo vallecano"],
    "psg": ["paris saint-germain", "paris sg", "paris saint germain"],
    "marseille": ["olympique marseille", "om"],
    "lyon": ["olympique lyonnais", "ol"],
    "rennes": ["stade rennais"],
    "inter": ["inter milan", "fc internazionale", "internazionale"],
    "ac milan": ["milan"],
    "juventus": ["juventus fc", "juve"],
    "napoli": ["ssc napoli"],
    "lazio": ["ss lazio"],
    "roma": ["as roma"],
    "atalanta": ["atalanta bc"],
    "ajax": ["afc ajax", "ajax amsterdam"],
    "psv": ["psv eindhoven"],
    "feyenoord": ["feyenoord rotterdam"],
    "benfica": ["sl benfica", "sport lisboa e benfica"],
    "porto": ["fc porto"],
    "sporting cp": ["sporting lisboa", "sporting lisbon"],
}


@dataclass
class EloRating:
    """A team's Elo rating with metadata."""
    team: str
    rating: float = DEFAULT_ELO
    matches_played: int = 0
    last_updated: str = ""
    league: str = ""
    # Home/away splits
    home_rating: float = DEFAULT_ELO
    away_rating: float = DEFAULT_ELO
    home_matches: int = 0
    away_matches: int = 0


@dataclass
class EloSystem:
    """Full Elo rating system with persistence."""
    ratings: dict[str, EloRating] = field(default_factory=dict)
    _alias_cache: dict[str, str] = field(default_factory=dict)
    initialized: bool = False

    def _resolve_name(self, name: str) -> str:
        """Resolve team name to canonical form using aliases."""
        normalized = _normalize_team_name(name)
        if normalized in self._alias_cache:
            return self._alias_cache[normalized]

        # Direct match
        if normalized in self.ratings:
            self._alias_cache[normalized] = normalized
            return normalized

        # Check aliases
        for canonical, aliases in _CSV_ALIASES.items():
            if normalized == canonical or normalized in [a.lower() for a in aliases]:
                if canonical in self.ratings:
                    self._alias_cache[normalized] = canonical
                    return canonical
                # Check if any alias is in ratings
                for alias in aliases:
                    norm_alias = _normalize_team_name(alias)
                    if norm_alias in self.ratings:
                        self._alias_cache[normalized] = norm_alias
                        return norm_alias

        # Fuzzy match as last resort
        best_match = None
        best_score = 0.0
        for key in self.ratings:
            score = SequenceMatcher(None, normalized, key).ratio()
            if score > best_score and score > 0.65:
                best_score = score
                best_match = key
        if best_match:
            self._alias_cache[normalized] = best_match
            return best_match

        # Not found — will create new entry
        self._alias_cache[normalized] = normalized
        return normalized

    def get_rating(self, team: str) -> EloRating:
        """Get team's Elo rating, creating default if not found."""
        key = self._resolve_name(team)
        if key not in self.ratings:
            self.ratings[key] = EloRating(team=key)
        return self.ratings[key]

    def get_ratings(self, team1: str, team2: str) -> tuple[float, float]:
        """Get both teams' overall ratings."""
        return self.get_rating(team1).rating, self.get_rating(team2).rating

    def get_home_away_ratings(self, home_team: str, away_team: str) -> tuple[float, float]:
        """Get home team's home rating and away team's away rating."""
        h = self.get_rating(home_team)
        a = self.get_rating(away_team)
        # Use split ratings if enough data, else fall back to overall
        hr = h.home_rating if h.home_matches >= 5 else h.rating
        ar = a.away_rating if a.away_matches >= 5 else a.rating
        return hr, ar

    def win_probability(
        self, team1: str, team2: str, *, is_home: bool = True, neutral: bool = False,
    ) -> tuple[float, float, float]:
        """
        Return (p_win1, p_draw, p_win2) using Elo ratings.

        Uses home/away split ratings when available.
        """
        if neutral:
            r1, r2 = self.get_ratings(team1, team2)
        elif is_home:
            r1, r2 = self.get_home_away_ratings(team1, team2)
            r1 += HOME_ELO_BONUS
        else:
            r2, r1 = self.get_home_away_ratings(team2, team1)
            r2 += HOME_ELO_BONUS

        e1 = expected_score(r1, r2)
        e2 = 1.0 - e1

        # Convert to 1X2 using draw model
        # Draw probability depends on how close the match is
        draw_base = 0.25
        closeness = 1.0 - abs(e1 - e2)  # 0=one-sided, 1=perfectly even
        p_draw = draw_base * (0.6 + 0.4 * closeness)
        p_draw = max(0.10, min(0.35, p_draw))

        # Distribute remaining probability
        remaining = 1.0 - p_draw
        p_w1 = remaining * e1
        p_w2 = remaining * e2

        return round(p_w1, 4), round(p_draw, 4), round(p_w2, 4)

    def elo_xg_adjustment(
        self, team1: str, team2: str, *, is_home: bool = True, neutral: bool = False,
    ) -> float:
        """
        Return xG adjustment factor based on Elo difference.

        Positive = team1 should score more than average.
        Range: roughly -0.5 to +0.5 goals.
        """
        if neutral:
            r1, r2 = self.get_ratings(team1, team2)
        elif is_home:
            r1, r2 = self.get_home_away_ratings(team1, team2)
            r1 += HOME_ELO_BONUS
        else:
            r2, r1 = self.get_home_away_ratings(team2, team1)
            r2 += HOME_ELO_BONUS

        diff = r1 - r2
        # 100 Elo diff ≈ 0.15 xG adjustment
        return round(diff * 0.0015, 3)

    def update(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        date: str = "",
        league: str = "",
    ) -> tuple[float, float]:
        """
        Update Elo ratings after a match result.
        Returns (new_home_rating, new_away_rating).
        """
        h = self.get_rating(home_team)
        a = self.get_rating(away_team)

        # Overall ratings
        r_h = h.rating + HOME_ELO_BONUS
        r_a = a.rating

        e_h = expected_score(r_h, r_a)
        e_a = 1.0 - e_h

        # Actual score: W=1, D=0.5, L=0
        if home_goals > away_goals:
            s_h, s_a = 1.0, 0.0
        elif home_goals < away_goals:
            s_h, s_a = 0.0, 1.0
        else:
            s_h, s_a = 0.5, 0.5

        # Goal difference multiplier
        gd = abs(home_goals - away_goals)
        gd_mult = _goal_diff_multiplier(gd)

        # Update overall
        delta_h = K_FACTOR * gd_mult * (s_h - e_h)
        delta_a = K_FACTOR * gd_mult * (s_a - e_a)
        h.rating = round(h.rating + delta_h, 1)
        a.rating = round(a.rating + delta_a, 1)

        # Update home/away splits (with separate tracking)
        h.home_rating = round(h.home_rating + delta_h * 1.1, 1)  # slightly more weight
        a.away_rating = round(a.away_rating + delta_a * 1.1, 1)
        h.home_matches += 1
        a.away_matches += 1

        h.matches_played += 1
        a.matches_played += 1
        h.last_updated = date
        a.last_updated = date
        if league:
            h.league = league
            a.league = league

        return h.rating, a.rating

    def apply_season_reversion(self) -> None:
        """Pull all ratings 1/3 toward mean at season start."""
        for r in self.ratings.values():
            r.rating = round(r.rating + (DEFAULT_ELO - r.rating) * SEASON_REVERSION_FACTOR, 1)
            r.home_rating = round(r.home_rating + (DEFAULT_ELO - r.home_rating) * SEASON_REVERSION_FACTOR, 1)
            r.away_rating = round(r.away_rating + (DEFAULT_ELO - r.away_rating) * SEASON_REVERSION_FACTOR, 1)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _save_to_db_sync(self) -> None:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS elo_ratings (
                team TEXT PRIMARY KEY,
                rating REAL NOT NULL,
                home_rating REAL NOT NULL DEFAULT 1500,
                away_rating REAL NOT NULL DEFAULT 1500,
                matches_played INTEGER NOT NULL DEFAULT 0,
                home_matches INTEGER NOT NULL DEFAULT 0,
                away_matches INTEGER NOT NULL DEFAULT 0,
                last_updated TEXT,
                league TEXT
            )
        """)
        for r in self.ratings.values():
            conn.execute("""
                INSERT OR REPLACE INTO elo_ratings
                (team, rating, home_rating, away_rating, matches_played,
                 home_matches, away_matches, last_updated, league)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (r.team, r.rating, r.home_rating, r.away_rating,
                  r.matches_played, r.home_matches, r.away_matches,
                  r.last_updated, r.league))
        conn.commit()
        conn.close()

    def _load_from_db_sync(self) -> bool:
        if not DB_PATH.exists():
            return False
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM elo_ratings").fetchall()
        except sqlite3.OperationalError:
            conn.close()
            return False
        conn.close()

        if not rows:
            return False

        for row in rows:
            r = EloRating(
                team=row["team"],
                rating=row["rating"],
                home_rating=row["home_rating"],
                away_rating=row["away_rating"],
                matches_played=row["matches_played"],
                home_matches=row["home_matches"],
                away_matches=row["away_matches"],
                last_updated=row["last_updated"] or "",
                league=row["league"] or "",
            )
            self.ratings[r.team] = r

        self.initialized = True
        logger.info("Loaded %d Elo ratings from DB", len(self.ratings))
        return True

    async def save(self) -> None:
        await asyncio.to_thread(self._save_to_db_sync)
        logger.info("Saved %d Elo ratings to DB", len(self.ratings))

    async def load(self) -> bool:
        return await asyncio.to_thread(self._load_from_db_sync)

    # ── CSV Initialization ───────────────────────────────────────────────────

    async def initialize(self, force_rebuild: bool = False) -> None:
        """Load from DB or build from CSV historical data."""
        if not force_rebuild:
            loaded = await self.load()
            if loaded and len(self.ratings) > 50:
                return

        logger.info("Building Elo ratings from football-data.co.uk CSV...")
        matches = await _fetch_all_csv_matches()
        if not matches:
            logger.warning("No CSV data fetched, using empty Elo ratings")
            self.initialized = True
            return

        # Sort by date
        matches.sort(key=lambda m: m["date"])

        current_season = ""
        for m in matches:
            season = m.get("season", "")
            if season != current_season and current_season:
                self.apply_season_reversion()
                logger.info(
                    "Season reversion applied at %s->%s (%d teams)",
                    current_season, season, len(self.ratings),
                )
            current_season = season

            self.update(
                home_team=m["home"],
                away_team=m["away"],
                home_goals=m["hg"],
                away_goals=m["ag"],
                date=m["date"],
                league=m.get("league", ""),
            )

        await self.save()
        self.initialized = True
        logger.info(
            "Elo system initialized: %d teams, %d matches processed",
            len(self.ratings), len(matches),
        )

    def top_teams(self, n: int = 20) -> list[tuple[str, float]]:
        """Return top N teams by Elo rating."""
        sorted_teams = sorted(
            self.ratings.values(), key=lambda r: r.rating, reverse=True,
        )
        return [(r.team, r.rating) for r in sorted_teams[:n]]


# ── CSV Fetcher ──────────────────────────────────────────────────────────────

async def _fetch_csv(session: aiohttp.ClientSession, season: str, league: str) -> list[dict]:
    """Fetch one CSV file from football-data.co.uk."""
    url = f"{CSV_BASE_URL}/{season}/{league}.csv"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            text = await resp.text(encoding="utf-8", errors="replace")
            text = text.lstrip("\ufeff")  # BOM
    except Exception as exc:
        logger.debug("CSV fetch failed %s/%s: %s", season, league, exc)
        return []

    matches = []
    reader = csv.DictReader(io.StringIO(text))
    league_name = CSV_LEAGUES.get(league, league)

    for row in reader:
        try:
            home = _normalize_team_name(row.get("HomeTeam") or row.get("HT", ""))
            away = _normalize_team_name(row.get("AwayTeam") or row.get("AT", ""))
            hg = int(row.get("FTHG") or row.get("HG", 0))
            ag = int(row.get("FTAG") or row.get("AG", 0))
            date_str = row.get("Date", "")

            if not home or not away:
                continue

            # Parse date (DD/MM/YYYY or DD/MM/YY)
            for fmt in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    dt = datetime.strptime(date_str, fmt)
                    date_str = dt.strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

            matches.append({
                "home": home,
                "away": away,
                "hg": hg,
                "ag": ag,
                "date": date_str,
                "season": season,
                "league": league_name,
            })
        except (ValueError, KeyError):
            continue

    return matches


async def _fetch_all_csv_matches() -> list[dict]:
    """Fetch all historical matches from football-data.co.uk."""
    all_matches = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        for season in CSV_SEASONS:
            for league in CSV_LEAGUES:
                tasks.append(_fetch_csv(session, season, league))

        results = await asyncio.gather(*tasks)
        for result in results:
            all_matches.extend(result)

    logger.info("Fetched %d total matches from CSV", len(all_matches))
    return all_matches


# ── Singleton ────────────────────────────────────────────────────────────────
_GLOBAL_ELO: Optional[EloSystem] = None
_GLOBAL_ELO_LOCK: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _GLOBAL_ELO_LOCK
    if _GLOBAL_ELO_LOCK is None:
        _GLOBAL_ELO_LOCK = asyncio.Lock()
    return _GLOBAL_ELO_LOCK


async def get_elo_system() -> EloSystem:
    """Get or create the global Elo system (lazy singleton)."""
    global _GLOBAL_ELO
    async with _get_lock():
        if _GLOBAL_ELO is None or not _GLOBAL_ELO.initialized:
            _GLOBAL_ELO = EloSystem()
            await _GLOBAL_ELO.initialize()
        return _GLOBAL_ELO


async def update_elo_after_result(
    home_team: str, away_team: str, home_goals: int, away_goals: int,
) -> tuple[float, float]:
    """Update Elo after a match result and persist."""
    elo = await get_elo_system()
    new_h, new_a = elo.update(home_team, away_team, home_goals, away_goals)
    await elo.save()
    return new_h, new_a
