#!/usr/bin/env python3
"""Settle CS2 round-total forward-test picks against the ACTUAL map round total from bo3,
and print a running ROI. Proves whether the "under-21.5 bias" edge is real (vs football's
fake edges). Idempotent per pick via data/totals_ft_results.jsonl.

  python cs2_totals_resolve.py [--notify]
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
import monitor as mon  # noqa: E402

LOG = ROOT / "data" / "totals_ft_log.jsonl"
RES = ROOT / "data" / "totals_ft_results.jsonl"
HDR = {"User-Agent": "Mozilla/5.0", "Origin": "https://bo3.gg", "Referer": "https://bo3.gg/"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _m(a: str, b: str) -> bool:
    return bool(a and b and (a in b or b in a or (len(a) >= 4 and a[:5] in b)))


def _read(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def find_match(t1: str, t2: str):
    n1, n2 = _norm(t1), _norm(t2)
    for off in (0, 60, 120):
        try:
            raw = bo3._get("/matches", {"filter[matches.status][eq]": "finished",
                "filter[matches.discipline_id][eq]": 1, "sort": "-start_date",
                "page[offset]": off, "page[limit]": 60}).get("results", [])
        except Exception:  # noqa: BLE001
            return None
        for m in raw:
            slug = _norm(m.get("slug") or "")
            if _m(n1, slug) and _m(n2, slug):
                return m.get("id")
        if not raw:
            break
    return None


def game_total(mid: int, map_no: int):
    url = "https://api.bo3.gg/api/v1/games?" + urllib.parse.urlencode(
        {"filter[games.match_id][eq]": mid, "scope": "show-match", "page[limit]": 10})
    try:
        gs = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDR),
                                               timeout=20).read()).get("results", [])
    except Exception:  # noqa: BLE001
        return None
    want = map_no or 1
    for g in gs:
        if g.get("number") == want and g.get("status") == "finished":
            ws, ls = g.get("winner_clan_score"), g.get("loser_clan_score")
            if ws is not None and ls is not None:
                return ws + ls
    return None


def run(notify: bool) -> None:
    picks = _read(LOG)
    done = {(_norm(r["t1"]), _norm(r["t2"]), r["line"], r["side"]) for r in _read(RES)}
    newly = 0
    for p in picks:
        key = (_norm(p["t1"]), _norm(p["t2"]), p["line"], p["side"])
        if key in done:
            continue
        mid = find_match(p["t1"], p["t2"])
        if not mid:
            continue
        tot = game_total(mid, p.get("map_no") or 1)
        if tot is None:
            continue
        over = tot > p["line"]
        won = (over and p["side"] == "ТБ") or (not over and p["side"] == "ТМ")
        pl = (p["odd"] - 1) if won else -1.0
        rec = {**p, "actual_total": tot, "outcome": "win" if won else "loss",
               "pl_units": round(pl, 3), "settled_at": datetime.now(timezone.utc).isoformat()}
        with RES.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done.add(key)
        newly += 1
        print(f"  settled {p['t1']} v {p['t2']} к{p.get('map_no')} {p['side']} {p['line']} "
              f"| total {tot} -> {'WIN' if won else 'loss'} ({pl:+.2f}u)")

    res = _read(RES)
    if res:
        n = len(res); wins = sum(1 for r in res if r["outcome"] == "win")
        pl = sum(float(r["pl_units"]) for r in res)
        line = (f"🎲 CS2 тоталы раундов — форвард-тест\nставок: {n} | винрейт {wins/n*100:.0f}% "
                f"| новых: {newly}\nROI (флэт): {pl/n*100:+.1f}% ({pl:+.2f}u / {n}u)")
        print("-" * 50 + "\n" + line)
        if notify and newly:
            mon.telegram_send(line.replace("\n", "\n"))
    print(f"done. newly={newly}")


def main() -> None:
    run("--notify" in sys.argv)


if __name__ == "__main__":
    main()
