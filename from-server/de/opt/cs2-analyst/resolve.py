#!/usr/bin/env python3
"""Settle CS2 forward-test bets from bo3.gg results → running ROI.

Reads ACTIVE bets (you recorded the odds you took via /coef) from cs2_bets, fetches
each match result from bo3.gg (free), grades win/loss, computes P/L on the taken odds,
and prints + DMs a running ROI tally. Match-winner (ML) graded now; map-handicap /
total-maps fall back to manual.

  python cs2_resolve.py [--notify]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3  # noqa: E402
import monitor  # noqa: E402  (telegram_send + DB path)

DB = ROOT / "cs2.db"


def _norm(s: str) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(s or "").lower())


def grade(bet: sqlite3.Row, m: dict) -> tuple[str | None, str]:
    """Return (outcome, final_score) — outcome win/lost/None(not finished/manual)."""
    win_id = m.get("winner_team_id")
    score = f"{m.get('maps_score')}"
    if not win_id or not m.get("team1_id"):
        return None, score
    winner = m["team1"] if win_id == m["team1_id"] else m["team2"]
    market = (bet["market"] or "ml").lower()
    side = bet["side"]
    if any(k in market for k in ("ml", "winner", "moneyline")) or market in ("", "none"):
        won = _norm(side) and (_norm(side) in _norm(winner) or _norm(winner) in _norm(side))
        return ("won" if won else "lost"), score
    return None, score  # map-handicap / total-maps → manual for now


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    active = conn.execute(
        "SELECT * FROM cs2_bets WHERE status='active' AND taken_odds IS NOT NULL").fetchall()
    print(f"active bets to settle: {len(active)}")
    newly = 0
    for b in active:
        try:
            m = bo3.get_match(b["slug"])
        except Exception as exc:  # noqa: BLE001
            import re as _re
            md = _re.search(r"(\d{2})-(\d{2})-(\d{4})", b["slug"] or "")
            if md:
                try:
                    d = datetime(int(md.group(3)), int(md.group(2)), int(md.group(1)), tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - d).days > 7:
                        conn.execute("UPDATE cs2_bets SET status='void', result='expired: unresolvable >7d' WHERE id=?", (b["id"],))
                        conn.commit()
                        print(f"  {b['slug']}: EXPIRED (>7d, bo3 404) -> void")
                        continue
                except Exception:  # noqa: BLE001
                    pass
            print(f"  {b['slug']}: fetch failed {exc}")
            continue
        outcome, score = grade(b, m)
        if outcome is None:
            print(f"  {b['team1']} v {b['team2']}: not finished / manual")
            continue
        stake = float(b["stake"] or 1.0)
        odds = float(b["taken_odds"])
        pl = stake * (odds - 1.0) if outcome == "won" else -stake
        conn.execute("UPDATE cs2_bets SET status=?, result=? WHERE id=?",
                     (outcome, f"{score}|pl={pl:+.2f}", b["id"]))
        conn.commit()
        newly += 1
        print(f"  settled {b['team1']} v {b['team2']} | {b['side']} @{odds} -> {outcome} ({pl:+.2f}u)")

    # tally over all settled
    rows = conn.execute("SELECT taken_odds,stake,status FROM cs2_bets WHERE status IN ('won','lost')").fetchall()
    conn.close()
    if rows:
        n = len(rows)
        wins = sum(1 for r in rows if r["status"] == "won")
        staked = sum(float(r["stake"] or 1.0) for r in rows)
        pl = sum((float(r["stake"] or 1.0) * (float(r["taken_odds"]) - 1.0)) if r["status"] == "won"
                 else -float(r["stake"] or 1.0) for r in rows)
        flat = sum((float(r["taken_odds"]) - 1.0) if r["status"] == "won" else -1.0 for r in rows)
        tally = (f"📈 <b>CS2 форвард-тест</b>\nставок: {n} | винрейт {wins/n*100:.0f}% | новых: {newly}\n"
                 f"ROI (по ставке): {pl/staked*100:+.1f}% ({pl:+.2f}u / {staked:.1f}u)\n"
                 f"ROI (флэт 1u): {flat/n*100:+.1f}%")
        print(tally.replace("<b>", "").replace("</b>", ""))
        if args.notify and newly:
            monitor.telegram_send(tally)
    else:
        print("no settled bets yet")


if __name__ == "__main__":
    main()
