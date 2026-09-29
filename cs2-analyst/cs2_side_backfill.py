#!/usr/bin/env python3
"""Backfill per-team CT/T side round-win rates per map from bo3.gg /games detail.

Each game detail carries `game_rounds` with `winner_clan_side` (CT/T) +
winner/loser clan names. We map clan names → team_ids via the game's
winner/loser_team_clan and tally, per (team, map, side): rounds won / played.
Feeds the side-split adjustment to the per-map win probability (a team strong on
CT-Nuke vs an opponent weak on T-Nuke gets a bigger map edge than Glicko alone).

  python cs2_side_backfill.py [max_games]   default 1500 (iterates map_rounds game_ids)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "cs2.db"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
_MAP = {"de_dust2": "Dust2", "de_mirage": "Mirage", "de_inferno": "Inferno",
        "de_nuke": "Nuke", "de_ancient": "Ancient", "de_anubis": "Anubis",
        "de_overpass": "Overpass", "de_vertigo": "Vertigo", "de_train": "Train"}


def _get(gid: int) -> dict | None:
    url = f"https://api.bo3.gg/api/v1/games/{gid}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                d = json.load(r)
                return d.get("results", d)
        except Exception:
            time.sleep(1.2 * (attempt + 1))
    return None


def init(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS side_stats (
        team_id INTEGER, map TEXT, side TEXT, wins INTEGER DEFAULT 0,
        played INTEGER DEFAULT 0, PRIMARY KEY (team_id, map, side));
    CREATE TABLE IF NOT EXISTS side_done (game_id INTEGER PRIMARY KEY);
    """)
    conn.commit()


def _side(s: str | None) -> str | None:
    if not s:
        return None
    s = s.lower()
    if "ct" in s or "counter" in s:
        return "CT"
    if "t" in s or "terror" in s:
        return "T"
    return None


def process(conn, gid: int) -> bool:
    g = _get(gid)
    if not g:
        return False
    mp = _MAP.get(g.get("map_name"), g.get("map_name"))
    w = g.get("winner_team_clan") or {}
    l = g.get("loser_team_clan") or {}
    name2id = {}
    if w.get("clan_name"):
        name2id[w["clan_name"]] = w.get("team_id")
    if l.get("clan_name"):
        name2id[l["clan_name"]] = l.get("team_id")
    acc = defaultdict(lambda: [0, 0])   # (team_id, side) -> [wins, played]
    for rnd in g.get("game_rounds") or []:
        ws = _side(rnd.get("winner_clan_side"))
        ls = _side(rnd.get("loser_clan_side"))
        wn, ln = rnd.get("winner_clan_name"), rnd.get("loser_clan_name")
        wid, lid = name2id.get(wn), name2id.get(ln)
        if wid and ws:
            acc[(wid, ws)][0] += 1; acc[(wid, ws)][1] += 1
        if lid and ls:
            acc[(lid, ls)][1] += 1
    if not acc or not mp:
        conn.execute("INSERT OR IGNORE INTO side_done VALUES(?)", (gid,))
        return True
    for (tid, side), (wins, played) in acc.items():
        conn.execute(
            "INSERT INTO side_stats(team_id,map,side,wins,played) VALUES(?,?,?,?,?) "
            "ON CONFLICT(team_id,map,side) DO UPDATE SET wins=wins+?, played=played+?",
            (tid, mp, side, wins, played, wins, played))
    conn.execute("INSERT OR IGNORE INTO side_done VALUES(?)", (gid,))
    return True


def backfill(max_games: int = 1500) -> None:
    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    init(conn)
    done = {r[0] for r in conn.execute("SELECT game_id FROM side_done")}
    gids = [r[0] for r in conn.execute(
        "SELECT game_id FROM map_rounds ORDER BY begin_at DESC") if r[0] not in done]
    n = 0
    for gid in gids[:max_games]:
        if process(conn, gid):
            n += 1
            if n % 50 == 0:
                conn.commit(); print(f"  processed {n}/{min(max_games,len(gids))}")
        time.sleep(0.35)
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM side_stats").fetchone()[0]
    print(f"DONE. side_stats rows: {rows}, games processed total: "
          f"{conn.execute('SELECT COUNT(*) FROM side_done').fetchone()[0]}")
    print("global CT vs T win% per map:")
    for r in conn.execute(
            "SELECT map, side, SUM(wins)*100.0/SUM(played) wr, SUM(played) n "
            "FROM side_stats WHERE map IS NOT NULL GROUP BY map, side HAVING n>200 ORDER BY map, side"):
        print(f"   {r[0]:10s} {r[1]:2s}  {r[2]:.0f}%  (n={r[3]})")
    conn.close()


if __name__ == "__main__":
    backfill(int(sys.argv[1]) if len(sys.argv) > 1 else 1500)
