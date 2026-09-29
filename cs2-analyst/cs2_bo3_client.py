#!/usr/bin/env python3
"""bo3.gg data client for the CS2 analyst.

bo3.gg exposes a public, auth-less JSON API (api.bo3.gg/api/v1) with ~71k historical
+ upcoming CS2 matches, teams (with ranks), tournaments (with tier), per-map results
(maps_score) AND the veto (match_maps: choice_type pick/ban/decider + team + order).
This is the free data spine for the model (the-odds-api does NOT cover CS2, so odds
come from a separate source).

CLI:
  python cs2_bo3_client.py upcoming [N]   list upcoming matches
  python cs2_bo3_client.py match <slug>   dump a match incl parsed veto + per-map
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.bo3.gg/api/v1"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
DISCIPLINE_CS2 = 1  # discipline_id for Counter-Strike on bo3.gg


def _get(path: str, params: dict | None = None, retries: int = 3) -> dict:
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe="[]")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"bo3 GET {path} failed: {last}")


def list_matches(status: str = "upcoming", limit: int = 20, offset: int = 0) -> list[dict]:
    """status: upcoming | finished | live. bo3 paginates by page[offset]/page[limit]."""
    data = _get("/matches", {
        "filter[matches.status][eq]": status,
        "filter[matches.discipline_id][eq]": DISCIPLINE_CS2,
        "sort": "start_date" if status == "upcoming" else "-start_date",
        "page[offset]": offset, "page[limit]": limit,
    })
    return [_brief(m) for m in (data.get("results") or [])]


def _brief(m: dict) -> dict:
    return {
        "slug": m.get("slug"),
        "team1": (m.get("team1") or {}).get("name") or m.get("team1_id"),
        "team2": (m.get("team2") or {}).get("name") or m.get("team2_id"),
        "team1_rank": (m.get("team1") or {}).get("rank"),
        "team2_rank": (m.get("team2") or {}).get("rank"),
        "bo_type": m.get("bo_type"),
        "tier": m.get("tier"),
        "start_date": m.get("start_date"),
        "status": m.get("status"),
        "tournament": (m.get("tournament") or {}).get("name") or m.get("tournament_id"),
    }


def get_match(slug: str) -> dict:
    """Full match detail with parsed veto + per-map scores."""
    m = _get(f"/matches/{slug}")
    m = m.get("results", m) if isinstance(m.get("results", None), dict) else m
    return {
        **_brief(m),
        "team1_id": m.get("team1_id"),
        "team2_id": m.get("team2_id"),
        "winner_team_id": m.get("winner_team_id"),
        "maps_score": m.get("maps_score"),
        "tier_rank": m.get("tier_rank"),
        "veto": _parse_veto(m),
        "maps": _parse_maps(m),
    }


def _map_name(mm: dict) -> str | None:
    mp = mm.get("maps")
    if isinstance(mp, dict):
        return mp.get("name") or mp.get("map_name") or mp.get("slug")
    return None


def _parse_veto(m: dict) -> list[dict]:
    out = []
    for mm in sorted(m.get("match_maps") or [], key=lambda x: x.get("order") or 0):
        out.append({
            "order": mm.get("order"),
            "choice": mm.get("choice_type"),     # pick / ban / decider
            "by_team_id": mm.get("team_id"),
            "map": _map_name(mm),
            "map_id": mm.get("map_id"),
        })
    return out


def _parse_maps(m: dict) -> list[dict]:
    out = []
    for mm in sorted(m.get("match_maps") or [], key=lambda x: x.get("order") or 0):
        if mm.get("choice_type") == "ban":
            continue
        out.append({
            "order": mm.get("order"),
            "map": _map_name(mm),
            "team1_winrate": mm.get("team1_winrate"),
            "team2_winrate": mm.get("team2_winrate"),
        })
    return out


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "upcoming"
    if cmd == "upcoming":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        rows = list_matches("upcoming", limit=n)
        print(f"{len(rows)} upcoming CS2 matches:")
        for r in rows:
            print(f"  [{r['start_date']}] T{r['tier']} Bo{r['bo_type']} | "
                  f"{r['team1']} (#{r['team1_rank']}) vs {r['team2']} (#{r['team2_rank']}) | {r['tournament']}")
    elif cmd == "match":
        m = get_match(sys.argv[2])
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
