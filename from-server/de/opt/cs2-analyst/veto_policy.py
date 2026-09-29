#!/usr/bin/env python3
"""Data-driven veto policy from the real `vetos` table.

Calibration showed per-map Glicko ratings do NOT predict real vetos (~random).
Teams instead have strong, opponent-independent map tendencies (habitual bans /
comfort picks). This learns each team's empirical ban & pick propensity per map,
shrunk toward the global map tendency for low-sample teams, and exposes them to the
veto sim so simulated map pools match reality.

  python cs2_veto_policy.py [team_id]   show global + (optional) a team's tendencies
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent / "cs2.db"
BAN, PICK = 2, 1
SHRINK = 6.0  # pseudo-count pulling a team toward the global map tendency


class Policy:
    def __init__(self, conn: sqlite3.Connection):
        self.tb = defaultdict(lambda: defaultdict(int))   # team -> map -> ban count
        self.tp = defaultdict(lambda: defaultdict(int))   # team -> map -> pick count
        self.tbn = defaultdict(int)                       # team -> total bans
        self.tpn = defaultdict(int)
        gb, gp = defaultdict(int), defaultdict(int)
        nb = npk = 0
        for r in conn.execute(
                "SELECT by_team_id t, map m, choice c FROM vetos "
                "WHERE map IS NOT NULL AND by_team_id IS NOT NULL"):
            if r["c"] == BAN:
                self.tb[r["t"]][r["m"]] += 1; self.tbn[r["t"]] += 1; gb[r["m"]] += 1; nb += 1
            elif r["c"] == PICK:
                self.tp[r["t"]][r["m"]] += 1; self.tpn[r["t"]] += 1; gp[r["m"]] += 1; npk += 1
        self.gban = {m: c / nb for m, c in gb.items()} if nb else {}
        self.gpick = {m: c / npk for m, c in gp.items()} if npk else {}

    def ban_prop(self, team: int, mp: str) -> float:
        e, n = self.tb[team].get(mp, 0), self.tbn[team]
        g = self.gban.get(mp, 1e-3)
        return (e + SHRINK * g) / (n + SHRINK)

    def pick_prop(self, team: int, mp: str) -> float:
        e, n = self.tp[team].get(mp, 0), self.tpn[team]
        g = self.gpick.get(mp, 1e-3)
        return (e + SHRINK * g) / (n + SHRINK)


_CACHE: Policy | None = None


def get_policy() -> Policy:
    global _CACHE
    if _CACHE is None:
        conn = sqlite3.connect(str(DB), timeout=60)
        conn.row_factory = sqlite3.Row
        _CACHE = Policy(conn)
        conn.close()
    return _CACHE


def main() -> None:
    p = get_policy()
    print("GLOBAL ban tendency (share):",
          {m: round(v, 2) for m, v in sorted(p.gban.items(), key=lambda x: -x[1])})
    print("GLOBAL pick tendency (share):",
          {m: round(v, 2) for m, v in sorted(p.gpick.items(), key=lambda x: -x[1])})
    if len(sys.argv) > 1:
        t = int(sys.argv[1])
        pool = ["Dust2", "Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Overpass"]
        print(f"\nteam {t}: total bans={p.tbn[t]} picks={p.tpn[t]}")
        print("  ban prop:", {m: round(p.ban_prop(t, m), 2) for m in sorted(pool, key=lambda m: -p.ban_prop(t, m))})
        print("  pick prop:", {m: round(p.pick_prop(t, m), 2) for m in sorted(pool, key=lambda m: -p.pick_prop(t, m))})


if __name__ == "__main__":
    main()
