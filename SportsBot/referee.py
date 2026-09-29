"""
Referee profiling system.

Builds referee average stats from football-data.co.uk CSV files.
Used to adjust total-goals and card expectations per match.

Key insight: lenient referees (fewer fouls/cards) correlate with higher-scoring
games. Strict referees (more fouls/cards, tighter game) reduce tempo.
"""
from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data" / "football_data_co_uk"
DB_PATH = Path(__file__).parent / "sportsbot.db"

# Minimum matches to trust a referee profile
MIN_MATCHES = 15


@dataclass(slots=True)
class RefereeProfile:
    name: str
    matches: int
    avg_goals: float          # avg total goals per match
    avg_home_goals: float
    avg_away_goals: float
    avg_yellows: float        # total yellows per match
    avg_reds: float           # total reds per match
    avg_fouls: float          # total fouls per match
    over_2_5_pct: float       # % of matches with >2.5 goals
    btts_pct: float           # % of matches where both teams scored
    # Relative to league average
    goals_vs_avg: float       # +0.3 means this ref's matches have 0.3 more goals than avg
    yellows_vs_avg: float


def _load_csv_matches() -> list[dict]:
    """Load all CSV rows with referee + goals + cards."""
    rows = []
    if not DATA_DIR.exists():
        return rows

    for season_dir in sorted(DATA_DIR.iterdir()):
        if not season_dir.is_dir():
            continue
        for csv_file in sorted(season_dir.glob("*.csv")):
            try:
                with open(csv_file, encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        ref = (row.get("Referee") or "").strip()
                        fthg = row.get("FTHG")
                        ftag = row.get("FTAG")
                        if not ref or fthg is None or ftag is None:
                            continue
                        try:
                            rows.append({
                                "referee": ref,
                                "home_goals": int(fthg),
                                "away_goals": int(ftag),
                                "home_yellows": int(row.get("HY") or 0),
                                "away_yellows": int(row.get("AY") or 0),
                                "home_reds": int(row.get("HR") or 0),
                                "away_reds": int(row.get("AR") or 0),
                                "home_fouls": int(row.get("HF") or 0),
                                "away_fouls": int(row.get("AF") or 0),
                                "league": csv_file.stem,
                                "season": season_dir.name,
                            })
                        except (ValueError, TypeError):
                            continue
            except Exception as exc:
                logger.debug("Failed to read %s: %s", csv_file, exc)

    return rows


def build_referee_profiles(min_matches: int = MIN_MATCHES) -> dict[str, RefereeProfile]:
    """Build referee profiles from all CSV data."""
    rows = _load_csv_matches()
    if not rows:
        return {}

    # Global average for comparison
    total_goals_all = sum(r["home_goals"] + r["away_goals"] for r in rows)
    total_yellows_all = sum(r["home_yellows"] + r["away_yellows"] for r in rows)
    avg_goals_global = total_goals_all / len(rows) if rows else 2.7
    avg_yellows_global = total_yellows_all / len(rows) if rows else 3.5

    # Group by referee
    from collections import defaultdict
    by_ref: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_ref[row["referee"]].append(row)

    profiles: dict[str, RefereeProfile] = {}
    for ref_name, matches in by_ref.items():
        n = len(matches)
        if n < min_matches:
            continue

        total_goals = sum(m["home_goals"] + m["away_goals"] for m in matches)
        total_hg = sum(m["home_goals"] for m in matches)
        total_ag = sum(m["away_goals"] for m in matches)
        total_yellows = sum(m["home_yellows"] + m["away_yellows"] for m in matches)
        total_reds = sum(m["home_reds"] + m["away_reds"] for m in matches)
        total_fouls = sum(m["home_fouls"] + m["away_fouls"] for m in matches)
        over_2_5 = sum(1 for m in matches if m["home_goals"] + m["away_goals"] > 2)
        btts = sum(1 for m in matches if m["home_goals"] > 0 and m["away_goals"] > 0)

        avg_g = total_goals / n
        avg_y = total_yellows / n

        profiles[ref_name] = RefereeProfile(
            name=ref_name,
            matches=n,
            avg_goals=round(avg_g, 2),
            avg_home_goals=round(total_hg / n, 2),
            avg_away_goals=round(total_ag / n, 2),
            avg_yellows=round(avg_y, 2),
            avg_reds=round(total_reds / n, 3),
            avg_fouls=round(total_fouls / n, 1),
            over_2_5_pct=round(over_2_5 / n * 100, 1),
            btts_pct=round(btts / n * 100, 1),
            goals_vs_avg=round(avg_g - avg_goals_global, 2),
            yellows_vs_avg=round(avg_y - avg_yellows_global, 2),
        )

    return profiles


# ── SQLite cache ──────────────────────────────────────────────────────────────

def _ensure_table():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referee_profiles (
            name TEXT PRIMARY KEY,
            matches INTEGER,
            avg_goals REAL,
            avg_home_goals REAL,
            avg_away_goals REAL,
            avg_yellows REAL,
            avg_reds REAL,
            avg_fouls REAL,
            over_2_5_pct REAL,
            btts_pct REAL,
            goals_vs_avg REAL,
            yellows_vs_avg REAL
        )
    """)
    conn.commit()
    conn.close()


def save_profiles_to_db(profiles: dict[str, RefereeProfile]) -> None:
    _ensure_table()
    conn = sqlite3.connect(str(DB_PATH))
    for p in profiles.values():
        conn.execute("""
            INSERT OR REPLACE INTO referee_profiles
            (name, matches, avg_goals, avg_home_goals, avg_away_goals,
             avg_yellows, avg_reds, avg_fouls, over_2_5_pct, btts_pct,
             goals_vs_avg, yellows_vs_avg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (p.name, p.matches, p.avg_goals, p.avg_home_goals, p.avg_away_goals,
              p.avg_yellows, p.avg_reds, p.avg_fouls, p.over_2_5_pct, p.btts_pct,
              p.goals_vs_avg, p.yellows_vs_avg))
    conn.commit()
    conn.close()
    logger.info("Saved %d referee profiles to DB", len(profiles))


def get_referee_profile(name: str) -> Optional[RefereeProfile]:
    """Look up referee by name (exact or fuzzy)."""
    _ensure_table()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Exact match first
    row = conn.execute("SELECT * FROM referee_profiles WHERE name = ?", (name,)).fetchone()
    if row:
        conn.close()
        return _row_to_profile(row)

    # Fuzzy: try surname match
    parts = name.strip().split()
    if parts:
        surname = parts[-1]
        rows = conn.execute(
            "SELECT * FROM referee_profiles WHERE name LIKE ?",
            (f"%{surname}%",)
        ).fetchall()
        if len(rows) == 1:
            conn.close()
            return _row_to_profile(rows[0])

    conn.close()
    return None


def _row_to_profile(row) -> RefereeProfile:
    return RefereeProfile(
        name=row["name"],
        matches=row["matches"],
        avg_goals=row["avg_goals"],
        avg_home_goals=row["avg_home_goals"],
        avg_away_goals=row["avg_away_goals"],
        avg_yellows=row["avg_yellows"],
        avg_reds=row["avg_reds"],
        avg_fouls=row["avg_fouls"],
        over_2_5_pct=row["over_2_5_pct"],
        btts_pct=row["btts_pct"],
        goals_vs_avg=row["goals_vs_avg"],
        yellows_vs_avg=row["yellows_vs_avg"],
    )


if __name__ == "__main__":
    import io
    import sys
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    profiles = build_referee_profiles()
    print(f"Built {len(profiles)} referee profiles\n")

    # Sort by goals_vs_avg to show most permissive/strict
    sorted_refs = sorted(profiles.values(), key=lambda p: p.goals_vs_avg, reverse=True)

    print("TOP 10 MOST PERMISSIVE (high-scoring):")
    print(f"  {'Referee':25s} {'N':>4s} {'AvgG':>5s} {'O2.5%':>6s} {'BTTS%':>6s} {'vsAvg':>6s} {'Fouls':>6s}")
    for p in sorted_refs[:10]:
        print(f"  {p.name:25s} {p.matches:4d} {p.avg_goals:5.2f} {p.over_2_5_pct:5.1f}% {p.btts_pct:5.1f}% {p.goals_vs_avg:+5.2f} {p.avg_fouls:5.1f}")

    print(f"\nTOP 10 MOST STRICT (low-scoring):")
    for p in sorted_refs[-10:]:
        print(f"  {p.name:25s} {p.matches:4d} {p.avg_goals:5.2f} {p.over_2_5_pct:5.1f}% {p.btts_pct:5.1f}% {p.goals_vs_avg:+5.2f} {p.avg_fouls:5.1f}")

    save_profiles_to_db(profiles)
    print("\nProfiles saved to DB.")
