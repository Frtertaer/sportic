#!/usr/bin/env python3
"""Resolve forward-test bets: fetch finished-match results, grade win/loss using the
odds captured at bet time, and print a running ROI tally.

Reads bets from ``data/forward_test_log.jsonl`` (written by ``forward_test.py``),
fetches completed scores from the-odds-api, grades each bet, and records settled
results to ``data/forward_test_results.jsonl`` (idempotent per prediction_id).
Schedule alongside forward_test.py; run it after fixtures finish.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config

DB_PATH = ROOT / "sportsbot.db"
RESULTS_PATH = ROOT / "data" / "forward_test_results.jsonl"
SPORT = "soccer_fifa_world_cup"
FT_USER_ID = 990001  # must match scripts/forward_test.py


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def fetch_scores() -> list[dict]:
    key = getattr(config, "ODDS_API_KEY", "") or ""
    if not key:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/scores/?apiKey={key}&daysFrom=3"
    try:
        with urllib.request.urlopen(url, timeout=25) as resp:
            return json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"score fetch failed: {exc}")
        return []


def _scores_map(ev: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in ev.get("scores") or []:
        try:
            out[_norm(s.get("name"))] = int(s.get("score"))
        except (TypeError, ValueError):
            continue
    return out


def grade(rec: str, t1: str, t2: str, score: dict[str, int]) -> str | None:
    """Return 'win'/'loss'/'push' for the recommendation given final scores by team."""
    g1 = score.get(_norm(t1))
    g2 = score.get(_norm(t2))
    if g1 is None or g2 is None:
        # tolerate slight name differences
        for k, v in score.items():
            if _norm(t1) in k or k in _norm(t1):
                g1 = v
            if _norm(t2) in k or k in _norm(t2):
                g2 = v
    if g1 is None or g2 is None:
        return None
    low = rec.lower()
    total = g1 + g2
    m = re.search(r"(over|under)\s+(\d+(?:\.\d+)?)", low)
    if m:
        side, line = m.group(1), float(m.group(2))
        if total == line:
            return "push"
        if side == "over":
            return "win" if total > line else "loss"
        return "win" if total < line else "loss"
    if "btts" in low:
        both = g1 > 0 and g2 > 0
        yes = "no" not in low
        return "win" if (both == yes) else "loss"
    if "draw" in low and _norm(t1) not in _norm(rec) and _norm(t2) not in _norm(rec):
        return "win" if g1 == g2 else "loss"
    # team straight win (covers "-0.5 Asian Handicap")
    if _norm(t1) and _norm(t1) in _norm(rec):
        return "win" if g1 > g2 else "loss"
    if _norm(t2) and _norm(t2) in _norm(rec):
        return "win" if g2 > g1 else "loss"
    return None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _load_bets_from_db() -> list[dict]:
    """Canonical bet records: predictions with captured odds (the forward-test picks)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, team1, team2, top_rec, top_recommendation_odds, stake_units "
        "FROM predictions WHERE top_recommendation_odds IS NOT NULL AND user_id=? ORDER BY id",
        (FT_USER_ID,),
    ).fetchall()
    conn.close()
    return [
        {
            "prediction_id": r["id"], "team1": r["team1"], "team2": r["team2"],
            "recommendation": r["top_rec"], "bet_odds": r["top_recommendation_odds"],
            "stake_units": r["stake_units"],
        }
        for r in rows
    ]


def main() -> None:
    bets = _load_bets_from_db()
    already = {r["prediction_id"] for r in _read_jsonl(RESULTS_PATH)}
    pending = [b for b in bets if b["prediction_id"] not in already]

    print(f"Forward resolve — {len(bets)} bets w/ odds, {len(already)} settled, {len(pending)} pending")
    events = fetch_scores()
    completed = {}
    for ev in events:
        if ev.get("completed"):
            completed[frozenset((_norm(ev.get("home_team")), _norm(ev.get("away_team"))))] = ev

    newly = 0
    for b in pending:
        key = frozenset((_norm(b["team1"]), _norm(b["team2"])))
        ev = completed.get(key)
        if ev is None:
            for k, e in completed.items():
                ks = list(k)
                if any(_norm(b["team1"]) in x or x in _norm(b["team1"]) for x in ks) and \
                   any(_norm(b["team2"]) in x or x in _norm(b["team2"]) for x in ks):
                    ev = e
                    break
        if ev is None:
            continue  # not finished yet
        outcome = grade(b["recommendation"], b["team1"], b["team2"], _scores_map(ev))
        if outcome is None:
            continue
        stake = float(b.get("stake_units") or 1.0)
        odds = float(b["bet_odds"])
        pl = stake * (odds - 1.0) if outcome == "win" else (0.0 if outcome == "push" else -stake)
        result = {
            "prediction_id": b["prediction_id"],
            "team1": b["team1"], "team2": b["team2"],
            "recommendation": b["recommendation"], "bet_odds": odds,
            "stake_units": stake, "outcome": outcome, "pl_units": round(pl, 4),
            "final_score": _scores_map(ev), "settled_at": datetime.now(timezone.utc).isoformat(),
        }
        with RESULTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
        newly += 1
        print(f"  settled #{b['prediction_id']} {b['team1']} v {b['team2']} | "
              f"{b['recommendation']} @{odds} -> {outcome} (P/L {pl:+.2f}u)")

    # ── Running tally over all settled bets ──
    settled = _read_jsonl(RESULTS_PATH)
    if settled:
        n = len(settled)
        wins = sum(1 for r in settled if r["outcome"] == "win")
        staked = sum(float(r["stake_units"]) for r in settled)
        pl = sum(float(r["pl_units"]) for r in settled)
        flat_pl = sum((float(r["bet_odds"]) - 1.0) if r["outcome"] == "win"
                      else (0.0 if r["outcome"] == "push" else -1.0) for r in settled)
        print("-" * 64)
        print(f"SETTLED: {n} bets | win rate {wins/n*100:.0f}% | newly {newly}")
        print(f"  Kelly-stake ROI: {pl/staked*100:+.1f}%  ({pl:+.2f}u / {staked:.2f}u staked)")
        print(f"  Flat-stake ROI:  {flat_pl/n*100:+.1f}%  ({flat_pl:+.2f}u / {n}u staked)")
    else:
        print("No bets settled yet — matches not finished.")


if __name__ == "__main__":
    main()
