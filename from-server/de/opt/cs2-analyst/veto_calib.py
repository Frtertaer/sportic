#!/usr/bin/env python3
"""Validate/calibrate the veto model against REAL vetos (cs2.db `vetos` table).

The veto sim assumes a team BANS its least-desirable maps and PICKS its most-desirable
(desirability = per-map Glicko win prob vs the opponent). This checks that on real
veto sequences: for each ban/pick, we rank the still-available maps by the acting
team's desirability and record where the chosen map falls (0 = least desirable …
1 = most). If the model's direction is right, BANS should average LOW, PICKS HIGH.
A near-0/near-1 split = sharp/rational vetos (→ lower softmax temp); near 0.5 = noise.

  python cs2_veto_calib.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from ratings import R, win_prob  # noqa: E402

DB = ROOT / "cs2.db"
BAN, PICK, DECIDER = 2, 1, 3


def main() -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    ratings: dict[tuple[str, int], R] = {}
    for r in conn.execute("SELECT map, team_id, rating, rd FROM map_ratings"):
        ratings[(r["map"], r["team_id"])] = R(r["rating"], r["rd"])
    teams = {r["slug"]: (r["team1_id"], r["team2_id"])
             for r in conn.execute("SELECT slug, team1_id, team2_id FROM matches")}
    vetos = defaultdict(list)
    for r in conn.execute("SELECT slug, ord, choice, by_team_id, map FROM vetos ORDER BY slug, ord"):
        vetos[r["slug"]].append(r)
    conn.close()

    def desir(team: int, opp: int, mp: str) -> float:
        a = ratings.get((mp, team), R())
        b = ratings.get((mp, opp), R())
        return win_prob(a, b)

    ban_pcts, pick_pcts = [], []
    matches_used = 0
    for slug, steps in vetos.items():
        tt = teams.get(slug)
        if not tt or None in tt:
            continue
        t1, t2 = tt
        pool = [s["map"] for s in steps if s["map"]]
        if len(pool) < 5 or not all((m, t1) in ratings or (m, t2) in ratings for m in pool):
            continue
        remaining = list(pool)
        used = False
        for s in steps:
            mp, by, ch = s["map"], s["by_team_id"], s["choice"]
            if not mp or mp not in remaining:
                continue
            if ch == DECIDER or by not in (t1, t2):
                if mp in remaining:
                    remaining.remove(mp)
                continue
            opp = t2 if by == t1 else t1
            scores = {m: desir(by, opp, m) for m in remaining}
            chosen = scores[mp]
            n = len(remaining)
            rank = sum(1 for v in scores.values() if v < chosen)  # 0=least desirable
            pct = rank / (n - 1) if n > 1 else 0.5
            if ch == BAN:
                ban_pcts.append(pct); used = True
            elif ch == PICK:
                pick_pcts.append(pct); used = True
            remaining.remove(mp)
        if used:
            matches_used += 1

    def avg(x):
        return sum(x) / len(x) if x else float("nan")

    print(f"matches used: {matches_used}")
    print(f"BANS  n={len(ban_pcts):5d}  avg desirability-percentile = {avg(ban_pcts):.3f}  "
          f"(expect LOW if teams ban weak maps)")
    print(f"PICKS n={len(pick_pcts):5d}  avg desirability-percentile = {avg(pick_pcts):.3f}  "
          f"(expect HIGH if teams pick strong maps)")
    # share of bans on the single worst available map / picks on the single best
    if ban_pcts:
        print(f"  bans on the WORST available map: {sum(1 for p in ban_pcts if p==0)/len(ban_pcts)*100:.0f}%")
    if pick_pcts:
        print(f"  picks on the BEST available map:  {sum(1 for p in pick_pcts if p==1)/len(pick_pcts)*100:.0f}%")
    print("\nInterpretation: bans≈0.2-0.35 & picks≈0.65-0.8 → model direction right, temp ~ok; "
          "closer to 0/1 → make VETO_TEMP smaller (sharper); ~0.5 → ratings too noisy for veto sim.")


if __name__ == "__main__":
    main()
