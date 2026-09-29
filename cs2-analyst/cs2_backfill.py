#!/usr/bin/env python3
"""Backfill CS2 historical per-map results from bo3.gg into SQLite.

Builds the training base for per-map Glicko-2 ratings + the veto model. Pulls
finished CS2 matches (most-recent first), and for each stores the match meta, the
veto sequence, and per-map winners (derived from maps_score, which is team1's
W/L per played map in veto order). Resumable (skips matches already stored) and
rate-limited so bo3.gg stays happy.

  python cs2_backfill.py [max_matches]    default 300
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3

DB = ROOT / "cs2.db"


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY, name TEXT, rank INTEGER
    );
    CREATE TABLE IF NOT EXISTS matches (
        slug TEXT PRIMARY KEY, team1_id INTEGER, team2_id INTEGER,
        winner_id INTEGER, bo_type INTEGER, tier TEXT, tier_rank INTEGER,
        start_date TEXT, tournament TEXT, t1_score INTEGER, t2_score INTEGER
    );
    CREATE TABLE IF NOT EXISTS map_results (
        slug TEXT, ord INTEGER, map TEXT, winner_id INTEGER,
        PRIMARY KEY (slug, ord)
    );
    CREATE TABLE IF NOT EXISTS vetos (
        slug TEXT, ord INTEGER, choice INTEGER, by_team_id INTEGER, map TEXT,
        PRIMARY KEY (slug, ord)
    );
    """)
    conn.commit()


def _upsert_team(conn, tid, name, rank):
    if tid:
        conn.execute("INSERT INTO teams(id,name,rank) VALUES(?,?,?) "
                     "ON CONFLICT(id) DO UPDATE SET name=COALESCE(excluded.name,name), "
                     "rank=COALESCE(excluded.rank,rank)", (tid, name, rank))


def store_match(conn: sqlite3.Connection, slug: str) -> bool:
    m = bo3.get_match(slug)
    t1, t2, win = m.get("team1_id"), m.get("team2_id"), m.get("winner_team_id")
    if not t1 or not t2:
        return False
    _upsert_team(conn, t1, m.get("team1"), m.get("team1_rank"))
    _upsert_team(conn, t2, m.get("team2"), m.get("team2_rank"))

    score = m.get("maps_score") or []           # team1 W/L per played map (in order)
    played = m.get("maps") or []                 # played maps (picks + decider), ordered
    t1w = sum(1 for s in score if s is True)
    t2w = sum(1 for s in score if s is False)
    conn.execute(
        "INSERT OR REPLACE INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (slug, t1, t2, win, m.get("bo_type"), m.get("tier"), m.get("tier_rank"),
         m.get("start_date"), m.get("tournament"), t1w, t2w),
    )
    for i, mp in enumerate(played):
        if i < len(score) and score[i] is not None:
            winner = t1 if score[i] else t2
            conn.execute("INSERT OR REPLACE INTO map_results VALUES(?,?,?,?)",
                         (slug, mp.get("order"), mp.get("map"), winner))
    for v in m.get("veto") or []:
        conn.execute("INSERT OR REPLACE INTO vetos VALUES(?,?,?,?,?)",
                     (slug, v.get("order"), v.get("choice"), v.get("by_team_id"), v.get("map")))
    return True


def backfill(max_matches: int = 300) -> None:
    conn = sqlite3.connect(str(DB))
    init_db(conn)
    have = {r[0] for r in conn.execute("SELECT slug FROM matches")}
    stored = 0
    offset = 0
    PAGE = 50
    empty_pages = 0
    while stored < max_matches:
        try:
            briefs = bo3.list_matches("finished", limit=PAGE, offset=offset)
        except Exception as exc:  # noqa: BLE001 — transient bo3 error, skip this page
            print(f"  list page @offset {offset} failed: {exc}")
            briefs = []
        offset += PAGE
        if not briefs:
            empty_pages += 1
            if empty_pages >= 5:
                break
            time.sleep(2.0)
            continue
        empty_pages = 0
        for b in briefs:
            slug = b["slug"]
            if not slug or slug in have:
                continue
            have.add(slug)
            try:
                if store_match(conn, slug):
                    stored += 1
                    if stored % 25 == 0:
                        conn.commit()
                        print(f"  stored {stored}/{max_matches} (last: {slug})")
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {slug}: {exc}")
            time.sleep(0.4)  # be gentle with bo3.gg
            if stored >= max_matches:
                break
    conn.commit()
    # quick summary
    nm = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    nmr = conn.execute("SELECT COUNT(*) FROM map_results").fetchone()[0]
    nt = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    top = conn.execute("SELECT map, COUNT(*) c FROM map_results GROUP BY map ORDER BY c DESC LIMIT 8").fetchall()
    print(f"DONE. matches={nm} map_results={nmr} teams={nt}")
    print("map distribution:", dict(top))
    conn.close()


if __name__ == "__main__":
    backfill(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
