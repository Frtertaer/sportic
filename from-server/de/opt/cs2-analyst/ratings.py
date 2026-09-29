#!/usr/bin/env python3
"""Per-map Glicko-2 ratings for CS2 teams, from the bo3.gg backfill (cs2.db).

Each active-duty map is a separate sub-game (the veto IS the model), so we keep an
INDEPENDENT Glicko-2 rating per (team, map). Map results are processed chronologically;
each result updates BOTH teams using their pre-game values. Ratings are written to the
`map_ratings` table for the win-probability + veto-simulation layers to consume.

  python ratings.py            recompute all per-map ratings + print top teams
  python ratings.py prob <t1> <t2> <map>   win prob of t1 over t2 on a map (team ids)
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "cs2.db"
SCALE = 173.7178
TAU = 0.5            # volatility constraint (lower → steadier)
DEFAULT_VOL = 0.06
BASE_R, BASE_RD = 1500.0, 350.0


class R:
    __slots__ = ("r", "rd", "vol", "games")

    def __init__(self, r=BASE_R, rd=BASE_RD, vol=DEFAULT_VOL):
        self.r, self.rd, self.vol, self.games = r, rd, vol, 0


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _apply(team: R, opp_r: float, opp_rd: float, score: float) -> None:
    """Glicko-2 single-game update of `team` vs an opponent's PRE-game (r, rd)."""
    mu = (team.r - 1500.0) / SCALE
    phi = team.rd / SCALE
    mu_j = (opp_r - 1500.0) / SCALE
    phi_j = opp_rd / SCALE
    g = _g(phi_j)
    E = _E(mu, mu_j, phi_j)
    v = 1.0 / (g * g * E * (1.0 - E))
    delta = v * g * (score - E)

    a0 = math.log(team.vol ** 2)

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a0) / (TAU * TAU)

    A = a0
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a0 - k * TAU) < 0:
            k += 1
        B = a0 - k * TAU
    fA, fB = f(A), f(B)
    for _ in range(100):
        if abs(B - A) < 1e-6:
            break
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2.0
        B, fB = C, fC
    new_vol = math.exp(A / 2.0)
    phi_star = math.sqrt(phi * phi + new_vol * new_vol)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * g * (score - E)
    team.r = SCALE * mu_new + 1500.0
    team.rd = SCALE * phi_new
    team.vol = new_vol
    team.games += 1


def update_pair(a: R, b: R, s_a: float) -> None:
    a_pre = (a.r, a.rd)
    b_pre = (b.r, b.rd)
    _apply(a, b_pre[0], b_pre[1], s_a)
    _apply(b, a_pre[0], a_pre[1], 1.0 - s_a)


def win_prob(a: R, b: R) -> float:
    """Glicko-2 expected score of a vs b, inflating uncertainty via both RDs."""
    mu = (a.r - 1500.0) / SCALE
    mu_j = (b.r - 1500.0) / SCALE
    phi_combined = math.sqrt((a.rd / SCALE) ** 2 + (b.rd / SCALE) ** 2)
    return _E(mu, mu_j, phi_combined)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB), timeout=90)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=90000")
    except sqlite3.OperationalError:
        pass
    return conn


def compute() -> dict[tuple[str, int], R]:
    conn = _connect()
    ratings: dict[tuple[str, int], R] = {}

    def get(mp: str, tid: int) -> R:
        key = (mp, tid)
        if key not in ratings:
            ratings[key] = R()
        return ratings[key]

    rows = conn.execute(
        "SELECT mr.map, mr.winner_id, m.team1_id, m.team2_id "
        "FROM map_results mr JOIN matches m ON m.slug = mr.slug "
        "WHERE mr.map IS NOT NULL AND mr.winner_id IS NOT NULL "
        "ORDER BY m.start_date, mr.ord"
    ).fetchall()
    for mr in rows:
        mp, win, t1, t2 = mr["map"], mr["winner_id"], mr["team1_id"], mr["team2_id"]
        if not (mp and win and t1 and t2):
            continue
        lose = t1 if win == t2 else t2
        update_pair(get(mp, win), get(mp, lose), 1.0)

    # persist
    conn.execute("CREATE TABLE IF NOT EXISTS map_ratings "
                 "(map TEXT, team_id INTEGER, rating REAL, rd REAL, vol REAL, games INTEGER, "
                 "PRIMARY KEY(map, team_id))")
    conn.execute("DELETE FROM map_ratings")
    conn.executemany(
        "INSERT INTO map_ratings VALUES(?,?,?,?,?,?)",
        [(mp, tid, r.r, r.rd, r.vol, r.games) for (mp, tid), r in ratings.items()],
    )
    conn.commit()
    conn.close()
    return ratings


def _team_names() -> dict[int, str]:
    conn = _connect()
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM teams")}
    conn.close()
    return names


def print_top(ratings: dict[tuple[str, int], R], min_games: int = 4, top: int = 8) -> None:
    names = _team_names()
    maps: dict[str, list] = {}
    for (mp, tid), r in ratings.items():
        if r.games >= min_games:
            maps.setdefault(mp, []).append((r.r, r.rd, r.games, tid))
    for mp in sorted(maps):
        rows = sorted(maps[mp], key=lambda x: -x[0])[:top]
        print(f"\n=== {mp} (top {len(rows)}, ≥{min_games} games) ===")
        for rr, rd, g, tid in rows:
            print(f"  {names.get(tid, tid):22s} {rr:6.0f}  ±{rd:3.0f}  ({g}g)")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "prob":
        t1, t2, mp = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
        ratings = compute()
        a = ratings.get((mp, t1), R())
        b = ratings.get((mp, t2), R())
        print(f"P({t1} beats {t2} on {mp}) = {win_prob(a, b):.3f}  "
              f"[{a.r:.0f}±{a.rd:.0f} vs {b.r:.0f}±{b.rd:.0f}]")
        return
    ratings = compute()
    n_teams = len({tid for _, tid in ratings})
    print(f"Computed per-map Glicko-2: {len(ratings)} (team,map) ratings, {n_teams} teams.")
    print_top(ratings)


if __name__ == "__main__":
    main()
