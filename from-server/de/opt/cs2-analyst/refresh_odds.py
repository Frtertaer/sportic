#!/usr/bin/env python3
"""Re-check PENDING cs2 bets against winline as their match approaches.

The monitor logs a value pick as `pending` (manual /coef) when winline doesn't list
the match yet — winline only opens lines ~2 days ahead, so matches further out fall
through. This runs on the winline timer (right after the scrape): for each still-pending
bet, look up winline again; once the line appears, fill the odds + edge and flip the
status (active if value, novalue if the book kills the edge) + DM the user. Closes the
timing gap so the user isn't stuck manually entering odds (or worse, manually betting a
no-value price the model would have rejected).

  python cs2_refresh_odds.py [--dry]
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone

import monitor  # reuse _conn, MIN_EDGE, telegram_send
import winline_odds as wo


def _norm(s: str) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(s or "").lower())


def run(dry: bool) -> None:
    conn = monitor._conn()
    rows = conn.execute(
        "SELECT id, slug, team1, team2, side, model_p, model_fair_odds "
        "FROM cs2_bets WHERE status='pending'").fetchall()
    print(f"pending bets to re-check: {len(rows)}")
    updated = 0
    for bid, slug, t1, t2, side, p, fair in rows:
        if not p:
            continue
        wl = wo.lookup(t1, t2) or wo.lookup_slug(slug)
        if not wl:
            print(f"  {t1} v {t2}: still not on winline")
            continue
        ns, n1 = _norm(side), _norm(t1)
        odd = wl["odd1"] if (ns and (ns in n1 or n1 in ns)) else wl["odd2"]
        edge = round(p * odd - 1.0, 3)
        if edge >= monitor.MIN_EDGE:
            status = "active"
            msg = (f"🏦 <b>winline теперь даёт линию</b> — {t1} vs {t2}\n"
                   f"👉 {side} @ {odd} | модель {p*100:.0f}% (справедл. {fair}) | "
                   f"край <b>{edge*100:+.0f}%</b> ✅ VALUE — записано авто (id {bid})")
        else:
            status = "novalue"
            msg = (f"ℹ️ winline выставил линию — {t1} vs {t2}\n"
                   f"{side} @ {odd} → край {edge*100:+.0f}% &lt; {int(monitor.MIN_EDGE*100)}% = "
                   f"НЕ value, пропускаем (id {bid})")
        print(f"  {t1} v {t2}: {side} @ {odd} edge {edge:+.0%} -> {status}")
        if not dry:
            conn.execute("UPDATE cs2_bets SET status=?, taken_odds=? WHERE id=?",
                         (status, odd, bid))
            conn.commit()
            monitor.telegram_send(msg)
        updated += 1
    print(f"done. updated={updated}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    run(ap.parse_args().dry)


if __name__ == "__main__":
    main()
