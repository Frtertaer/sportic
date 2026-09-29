#!/usr/bin/env python3
"""Backfill per-map ROUND scores from bo3.gg /games into cs2.db (map_rounds table).

Each game (= one map of a match) carries winner_clan_score + loser_clan_score +
rounds_count directly in the /games list, so a single paginated pass gives the
total-rounds distribution per map — the base for the negative-binomial round-totals
(O/U map rounds) model. Joined to matches via match_id for team-level pace later.

  python cs2_games_backfill.py [max_games]   default 4000
"""
from __future__ import annotations

import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "cs2.db"
API = "https://api.bo3.gg/api/v1"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

_MAP = {  # bo3 internal name -> display
    "de_dust2": "Dust2", "de_mirage": "Mirage", "de_inferno": "Inferno",
    "de_nuke": "Nuke", "de_ancient": "Ancient", "de_anubis": "Anubis",
    "de_overpass": "Overpass", "de_vertigo": "Vertigo", "de_train": "Train",
}


def _get(path: str, params: dict) -> dict:
    url = f"{API}{path}?" + urllib.parse.urlencode(params, safe="[]")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                import json
                return json.load(r)
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {}


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS map_rounds (
        game_id INTEGER PRIMARY KEY, match_id INTEGER, map TEXT,
        total_rounds INTEGER, winner_score INTEGER, loser_score INTEGER, begin_at TEXT
    )""")
    conn.commit()


def backfill(max_games: int = 4000) -> None:
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    init(conn)
    have = {r[0] for r in conn.execute("SELECT game_id FROM map_rounds")}
    stored, offset = 0, 0
    while stored < max_games:
        data = _get("/games", {"sort": "-begin_at", "page[offset]": offset, "page[limit]": 50})
        rows = data.get("results") or []
        if not rows:
            break
        offset += 50
        for g in rows:
            gid = g.get("id")
            if not gid or gid in have or g.get("state") != "done":
                continue
            mp = _MAP.get(g.get("map_name"), g.get("map_name"))
            ws, ls = g.get("winner_clan_score"), g.get("loser_clan_score")
            if ws is None or ls is None:
                continue
            conn.execute("INSERT OR REPLACE INTO map_rounds VALUES(?,?,?,?,?,?,?)",
                         (gid, g.get("match_id"), mp, (ws + ls), ws, ls, g.get("begin_at")))
            stored += 1
        conn.commit()
        if stored % 200 < 50:
            print(f"  stored ~{stored}/{max_games}")
        time.sleep(0.3)
    # summary: round-total distribution per map
    print(f"DONE. map_rounds rows: {conn.execute('SELECT COUNT(*) FROM map_rounds').fetchone()[0]}")
    print("avg total rounds + over-26.5 rate per map:")
    for r in conn.execute(
            "SELECT map, COUNT(*) n, ROUND(AVG(total_rounds),1) avg, "
            "ROUND(AVG(CASE WHEN total_rounds>26.5 THEN 1.0 ELSE 0 END)*100,0) over265 "
            "FROM map_rounds WHERE map IS NOT NULL GROUP BY map HAVING n>=20 ORDER BY n DESC"):
        print(f"   {r[0]:10s} n={r[1]:4d}  avg={r[2]:.1f}  over26.5={r[3]:.0f}%")
    conn.close()


if __name__ == "__main__":
    backfill(int(sys.argv[1]) if len(sys.argv) > 1 else 4000)
