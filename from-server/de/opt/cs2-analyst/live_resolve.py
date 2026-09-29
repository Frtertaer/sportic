#!/usr/bin/env python3
"""Grade the model's LIVE predictions (series / map / round-total) vs bo3 results, so the
dashboard can show a real calibration track record ("model said 84% — did it win 84%?").
Idempotent per pick via data/live_picks_results.jsonl.

  python cs2_live_resolve.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3  # noqa: E402

PICKS = ROOT / "data" / "live_picks.jsonl"
RES = ROOT / "data" / "live_picks_results.jsonl"
HDR = {"User-Agent": "Mozilla/5.0", "Origin": "https://bo3.gg", "Referer": "https://bo3.gg/"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _norm_map(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").replace("de_", "").lower())


def _read(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                out.append(json.loads(ln))
    return out


def _games(mid: int) -> list[dict]:
    url = "https://api.bo3.gg/api/v1/games?" + urllib.parse.urlencode(
        {"filter[games.match_id][eq]": mid, "scope": "show-match", "page[limit]": 12})
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDR),
                                                 timeout=20).read()).get("results", [])
    except Exception:  # noqa: BLE001
        return []


def _match(slug: str):
    try:
        raw = bo3._get(f"/matches/{slug}", {"scope": "show-match"})
    except Exception:  # noqa: BLE001
        return None
    m = raw.get("results", raw)
    if isinstance(m, list):
        m = m[0] if m else {}
    return m


def grade(pick: dict):
    m = _match(pick["slug"])
    if not m or m.get("status") != "finished":
        return None
    t1, t2 = m.get("team1"), m.get("team2")
    s1, s2 = int(m.get("team1_score") or 0), int(m.get("team2_score") or 0)
    if pick["market"] == "series":
        winner = t1 if s1 > s2 else t2
        won = _norm(pick["side"]) in _norm(winner) or _norm(winner) in _norm(pick["side"])
        return ("win" if won else "loss"), {"winner": winner, "score": f"{s1}-{s2}"}
    # map / total need the specific game
    gs = _games(m.get("id"))
    g = next((x for x in gs if _norm_map(x.get("map_name")) == _norm_map(pick.get("map"))
              and x.get("status") == "finished"), None)
    if not g:
        return None
    if pick["market"] == "map":
        won = (_norm(pick["side"]) in _norm(g.get("winner_clan_name"))
               or _norm(g.get("winner_clan_name")) in _norm(pick["side"]))
        return ("win" if won else "loss"), {"map_winner": g.get("winner_clan_name")}
    if pick["market"] == "total":
        ws, ls = g.get("winner_clan_score"), g.get("loser_clan_score")
        if ws is None or ls is None:
            return None
        total = ws + ls
        over = total > pick["line"]
        won = (over and pick["side"] == "ТБ") or (not over and pick["side"] == "ТМ")
        return ("win" if won else "loss"), {"total": total}
    return None


def run() -> None:
    picks = _read(PICKS)
    done = {r["key"] for r in _read(RES)}
    newly = 0
    for p in picks:
        if p["key"] in done:
            continue
        g = grade(p)
        if not g:
            continue
        outcome, extra = g
        rec = {**p, "outcome": outcome, **extra, "settled_at": datetime.now(timezone.utc).isoformat()}
        with RES.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done.add(p["key"]); newly += 1
        print(f"  {p['market']:6} {p['match'][:28]:28} {p['side'][:14]:14} p={p['model_p']:.2f} -> {outcome}")
    # quick calibration print
    res = _read(RES)
    for mk in ("series", "map", "total"):
        rr = [r for r in res if r["market"] == mk]
        if rr:
            n = len(rr); w = sum(1 for r in rr if r["outcome"] == "win")
            avgp = sum(r["model_p"] for r in rr) / n
            print(f"  [{mk}] n={n} winrate={w/n*100:.0f}% avg_model_p={avgp*100:.0f}% "
                  f"({'calibrated' if abs(w/n-avgp)<0.08 else 'OFF'})")
    print(f"done. newly={newly}")


if __name__ == "__main__":
    run()
