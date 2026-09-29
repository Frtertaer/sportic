#!/usr/bin/env python3
"""Side-aware (CT/T) per-map win probability, blended on top of per-map Glicko.

Glicko per-map rating is opponent-AVERAGED; side_split adds the OPPONENT-SPECIFIC
side matchup: team A strong on CT-Nuke vs team B weak on T-Nuke wins Nuke by more
than the aggregate rating implies. We combine each team's side win-rate (shrunk to
the map's global CT/T base) via log5 into a per-round share, then map that to a map
win prob. The biggest payoff is live (half-time comebacks) + round-handicap markets.

  python cs2_side_split.py <t1> <t2> <map>   side-derived map win prob for t1
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "cs2.db"
SHRINK = 30          # rounds of pseudo-data pulling a team's side wr to the map base
ROUND_TO_MAP_K = 17  # round-share → map-win steepness (0.55 share ≈ 0.70 map)
MIN_PLAYED = 20      # need at least this many side rounds to trust a team's split


class Side:
    def __init__(self, conn: sqlite3.Connection):
        self.tw: dict[tuple[int, str, str], tuple[int, int]] = {}
        self.base: dict[tuple[str, str], float] = {}
        gw, gp = {}, {}
        for r in conn.execute("SELECT team_id,map,side,wins,played FROM side_stats"):
            self.tw[(r["team_id"], r["map"], r["side"])] = (r["wins"], r["played"])
            k = (r["map"], r["side"])
            gw[k] = gw.get(k, 0) + r["wins"]; gp[k] = gp.get(k, 0) + r["played"]
        for k in gp:
            self.base[k] = gw[k] / gp[k] if gp[k] else 0.5

    def wr(self, team: int, mp: str, side: str) -> tuple[float, int]:
        w, p = self.tw.get((team, mp, side), (0, 0))
        base = self.base.get((mp, side), 0.5)
        return (w + SHRINK * base) / (p + SHRINK), p


def _log5(a: float, b: float) -> float:
    """P(A wins) combining A's win-rate a and opponent's win-rate b (both vs average)."""
    denom = a + b - 2 * a * b
    return (a - a * b) / denom if denom > 1e-9 else 0.5


_CACHE: Side | None = None


def _side() -> Side:
    global _CACHE
    if _CACHE is None:
        conn = sqlite3.connect(str(DB), timeout=60)
        conn.row_factory = sqlite3.Row
        _CACHE = Side(conn)
        conn.close()
    return _CACHE


def side_map_prob(t1: int, t2: int, mp: str) -> float | None:
    """Side-derived P(t1 wins map mp), or None if data too thin."""
    s = _side()
    t1_ct, n1c = s.wr(t1, mp, "CT")
    t1_t, n1t = s.wr(t1, mp, "T")
    t2_ct, n2c = s.wr(t2, mp, "CT")
    t2_t, n2t = s.wr(t2, mp, "T")
    if min(n1c + n1t, n2c + n2t) < MIN_PLAYED:
        return None
    # t1 as CT vs t2 as T, and t1 as T vs t2 as CT → average round share
    share = 0.5 * (_log5(t1_ct, t2_t) + _log5(t1_t, t2_ct))
    return 1.0 / (1.0 + math.exp(-ROUND_TO_MAP_K * (share - 0.5)))


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__); return
    t1, t2, mp = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    p = side_map_prob(t1, t2, mp)
    print(f"side-derived P({t1} wins {mp} vs {t2}) = {p}")


if __name__ == "__main__":
    main()
