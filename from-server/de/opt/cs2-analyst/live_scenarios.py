#!/usr/bin/env python3
"""CS2 live scenario engine: once the VETO is locked (real maps known), compute the
EXACT series-outcome distribution on those maps (no veto simulation) — and update it
as maps finish (the "price after Map 1" edge). Picks the most likely / best-value
outcome to bet in-play.

Flow the user wants: after teams ban/pick → for the ACTUAL maps, enumerate every
series path → show P(2-0), P(2-1) each way + per-map winners + total-maps, find the
top outcome, and (with live book odds) the value bet.

  python cs2_live_scenarios.py <slug>          scenarios from current live state
  python cs2_live_scenarios.py <slug> --fresh   assume 0-0 (just-locked veto)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3  # noqa: E402
import veto_sim as vs  # noqa: E402


def real_maps(match: dict) -> list[tuple[str, int]]:
    """Picked maps + decider in veto order → [(map, picked_by 1/2/0)]. [] if veto not locked."""
    t1, t2 = match.get("team1_id"), match.get("team2_id")
    out = []
    for v in match.get("veto") or []:
        ch = v.get("choice")
        if ch == 2:                      # ban
            continue
        by = v.get("by_team_id")
        picker = 1 if by == t1 else (2 if by == t2 else 0)
        if v.get("map"):
            out.append((v["map"], picker))
    return out


def _played_score(match: dict, n_maps: int) -> tuple[int, int, int]:
    """(t1 maps won, t2 maps won, maps completed) from maps_score (team1 perspective)."""
    score = match.get("maps_score") or []
    s1 = sum(1 for s in score if s is True)
    s2 = sum(1 for s in score if s is False)
    done = s1 + s2
    return s1, s2, done


def enumerate_outcomes(probs: list[float], s1: int, s2: int, need: int,
                       name1: str, name2: str) -> dict[str, float]:
    """Exact distribution of final series scores from current (s1,s2) over remaining maps."""
    res: dict[str, float] = defaultdict(float)

    def rec(i: int, a: int, b: int, pr: float) -> None:
        if a == need:
            res[f"{name1} {need}-{b}"] += pr
            return
        if b == need:
            res[f"{name2} {need}-{a}"] += pr
            return
        if i >= len(probs):
            return
        p = probs[i]
        rec(i + 1, a + 1, b, pr * p)
        rec(i + 1, a, b + 1, pr * (1 - p))

    rec(0, s1, s2, 1.0)
    return dict(res)


def scenarios(slug: str, fresh: bool = False) -> dict | None:
    match = bo3.get_match(slug)
    maps = real_maps(match)
    if not maps:
        return None  # veto not locked yet
    t1, t2 = match["team1_id"], match["team2_id"]
    bo = match.get("bo_type") or 3
    need = 2 if bo == 3 else (3 if bo == 5 else 1)

    conn = vs._conn()
    pool = vs.active_pool(conn)
    rt1 = vs.team_ratings(conn, t1, pool)
    rt2 = vs.team_ratings(conn, t2, pool)
    name1, name2 = vs.team_name(conn, t1), vs.team_name(conn, t2)
    conn.close()

    s1, s2, done = (0, 0, 0) if fresh else _played_score(match, len(maps))
    remaining = maps[done:]
    # per-map P(t1 wins) for the remaining maps (ratings + side-split + pick bonus)
    probs = [vs._map_winprob(rt1, rt2, mp, by, t1, t2) for mp, by in remaining]

    out = enumerate_outcomes(probs, s1, s2, need, name1, name2)
    total = sum(out.values()) or 1.0
    out = {k: v / total for k, v in out.items()}

    # per-remaining-map winner probs + total maps
    map_lines = [{"map": mp, "picked_by": ("decider" if by == 0 else (name1 if by == 1 else name2)),
                  f"p_{name1}": round(p, 3)} for (mp, by), p in zip(remaining, probs)]
    p_series_t1 = sum(v for k, v in out.items() if k.startswith(name1))
    return {
        "match": f"{name1} vs {name2}", "bo": bo, "current": f"{s1}-{s2}",
        "maps_order": [{"map": mp, "picked_by": ("decider" if by == 0 else (name1 if by == 1 else name2))}
                       for mp, by in maps],
        "remaining_maps": map_lines,
        "p_series": {name1: round(p_series_t1, 3), name2: round(1 - p_series_t1, 3)},
        "outcomes": dict(sorted(out.items(), key=lambda x: -x[1])),
        "top": max(out.items(), key=lambda x: x[1]) if out else None,
    }


def _fair(p: float) -> float | None:
    return round(1 / p, 2) if p and p > 0.01 else None


def live_series(slug: str) -> dict | None:
    """Live series-winner probability CONDITIONED on the current score.

    bo3 hides the veto (match_maps) for in-play matches, so we can't know the exact
    remaining maps. We approximate: p_map = the team's average per-map win prob (with
    side-split) over the active pool, then condition the series on the current score.
    Gives an updated series prob to compare vs the live book line (the between-maps edge).
    """
    raw = bo3._get(f"/matches/{slug}")
    m = raw.get("results", raw)
    t1, t2 = m.get("team1_id"), m.get("team2_id")
    if not t1 or not t2:
        return None
    bo = m.get("bo_type") or 3
    need = 2 if bo == 3 else (3 if bo == 5 else 1)
    s1, s2 = int(m.get("team1_score") or 0), int(m.get("team2_score") or 0)

    conn = vs._conn()
    pool = vs.active_pool(conn)
    rt1 = vs.team_ratings(conn, t1, pool)
    rt2 = vs.team_ratings(conn, t2, pool)
    name1, name2 = vs.team_name(conn, t1), vs.team_name(conn, t2)
    conn.close()

    ps = [vs._map_winprob(rt1, rt2, mp, 0, t1, t2) for mp in pool]
    p = sum(ps) / len(ps) if ps else 0.5

    memo: dict[tuple[int, int], float] = {}

    def f(a: int, b: int) -> float:
        if a >= need:
            return 1.0
        if b >= need:
            return 0.0
        if (a, b) in memo:
            return memo[(a, b)]
        r = p * f(a + 1, b) + (1 - p) * f(a, b + 1)
        memo[(a, b)] = r
        return r

    ps1 = f(s1, s2)
    return {
        "match": f"{name1} vs {name2}", "bo": bo, "current": f"{s1}-{s2}",
        "team1": name1, "team2": name2,
        "p_series": {name1: round(ps1, 3), name2: round(1 - ps1, 3)},
        "p_map": round(p, 3),
        "rated1": sum(1 for r in rt1.values() if r.games >= 3),
        "rated2": sum(1 for r in rt2.values() if r.games >= 3),
    }


def _norm_map(name: str | None, pool: list[str]) -> str | None:
    """bo3 game map_name 'de_anubis' -> our rating key 'Anubis'."""
    if not name:
        return None
    base = str(name).replace("de_", "").lower()
    for p in pool:
        if p.lower() == base:
            return p
    return None


def live_games(slug: str) -> dict | None:
    """BEST live model: bo3 hides the veto live, but the /games endpoint (scope=show-match)
    exposes each map's NAME + status, incl. the CURRENT map being played. So we compute the
    EXACT win prob of the current map (per-map Glicko + side-split) instead of a pool average,
    then the series prob conditioned on the live score. At the decider, P(map)=P(series)."""
    raw = bo3._get(f"/matches/{slug}", {"scope": "show-match"})
    m = raw.get("results", raw)
    if isinstance(m, list):
        m = m[0] if m else {}
    mid, t1, t2 = m.get("id"), m.get("team1_id"), m.get("team2_id")
    if not mid or not t1 or not t2:
        return None
    bo = m.get("bo_type") or 3
    need = 2 if bo == 3 else (3 if bo == 5 else 1)
    s1, s2 = int(m.get("team1_score") or 0), int(m.get("team2_score") or 0)

    try:
        games = bo3._get("/games", {"filter[games.match_id][eq]": mid,
                                    "scope": "show-match", "page[limit]": 10}).get("results", [])
    except Exception:  # noqa: BLE001
        games = []
    cur = next((g for g in games if g.get("status") == "current"), None)
    played = [g for g in games if g.get("status") == "finished"]

    conn = vs._conn()
    pool = vs.active_pool(conn)
    rt1 = vs.team_ratings(conn, t1, pool)
    rt2 = vs.team_ratings(conn, t2, pool)
    name1, name2 = vs.team_name(conn, t1), vs.team_name(conn, t2)
    conn.close()

    ap = [vs._map_winprob(rt1, rt2, mp, 0, t1, t2) for mp in pool]
    p_avg = sum(ap) / len(ap) if ap else 0.5
    cur_map = _norm_map(cur.get("map_name"), pool) if cur else None
    p_cur = vs._map_winprob(rt1, rt2, cur_map, 0, t1, t2) if cur_map else p_avg

    memo: dict = {}

    def f(a: int, b: int, first: bool) -> float:
        if a >= need:
            return 1.0
        if b >= need:
            return 0.0
        if (a, b, first) in memo:
            return memo[(a, b, first)]
        p = p_cur if first else p_avg
        r = p * f(a + 1, b, False) + (1 - p) * f(a, b + 1, False)
        memo[(a, b, first)] = r
        return r

    ps1 = f(s1, s2, True)
    played_info = [{"map": _norm_map(g.get("map_name"), pool),
                    "winner": g.get("winner_clan_name"),
                    "score": f"{g.get('winner_clan_score')}-{g.get('loser_clan_score')}"}
                   for g in sorted(played, key=lambda x: x.get("number") or 0)]
    return {
        "match": f"{name1} vs {name2}", "bo": bo, "current": f"{s1}-{s2}",
        "team1": name1, "team2": name2,
        "cur_map": cur_map,
        "p_cur_map": {name1: round(p_cur, 3), name2: round(1 - p_cur, 3)},
        "p_series": {name1: round(ps1, 3), name2: round(1 - ps1, 3)},
        "played": played_info,
        "is_decider": (need - s1 == 1 and need - s2 == 1),
        "rated1": sum(1 for r in rt1.values() if r.games >= 3),
        "rated2": sum(1 for r in rt2.values() if r.games >= 3),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__); return
    slug = sys.argv[1]
    fresh = "--fresh" in sys.argv
    s = scenarios(slug, fresh=fresh)
    if not s:
        print("veto not locked yet (no maps picked) — scenarios unavailable")
        return
    print(f"\n{s['match']}  (Bo{s['bo']})  текущий счёт {s['current']}")
    print("карты (вето):", " → ".join(f"{m['map']}[{m['picked_by']}]" for m in s["maps_order"]))
    print(f"серия: {' | '.join(f'{k} {v*100:.0f}%' for k,v in s['p_series'].items())}")
    print("вероятности по оставшимся картам:")
    for m in s["remaining_maps"]:
        kk = [k for k in m if k.startswith("p_")][0]
        print(f"   {m['map']:9s} [{m['picked_by']}]  P({kk[2:]})={m[kk]:.2f}")
    print("ИСХОДЫ серии (вероятность → справедливый коэф):")
    for k, v in s["outcomes"].items():
        print(f"   {k:24s} {v*100:5.1f}%   (~{_fair(v)})")
    print(f"САМЫЙ ВЕРОЯТНЫЙ: {s['top'][0]} ({s['top'][1]*100:.0f}%)")


if __name__ == "__main__":
    main()
