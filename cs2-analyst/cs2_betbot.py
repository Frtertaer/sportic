#!/usr/bin/env python3
"""CS2 forward-test Telegram bot: record the odds you took, on one line.

Long-polls Telegram. Commands (from the configured chat only):
  /coef <id> <odds> [stake]   record the odds you bet at → marks the pick ACTIVE
  /pending                    list picks awaiting odds / awaiting result
  /roi                        running forward-test ROI
  /help

Pairs with cs2_monitor.py (creates the picks) and cs2_resolve.py (settles them).
Run as an always-on systemd service.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import monitor  # noqa: E402  (CFG, DB, telegram_send)

DB = monitor.DB
TOKEN = monitor.CFG.get("TELEGRAM_TOKEN", "")
CHAT = str(monitor.CFG.get("TELEGRAM_CHAT_ID", ""))
API = f"https://api.telegram.org/bot{TOKEN}"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=60000")
    return c


def reply(text: str) -> None:
    body = urllib.parse.urlencode({"chat_id": CHAT, "text": text[:4000], "parse_mode": "HTML"}).encode()
    try:
        urllib.request.urlopen(f"{API}/sendMessage", data=body, timeout=20)
    except Exception as exc:  # noqa: BLE001
        print(f"reply failed: {exc}")


def _clean(s: str) -> str:
    return s.strip(" <>[](){}").replace(",", ".")


def cmd_coef(args: list[str]) -> str:
    args = [_clean(a) for a in args if _clean(a)]
    if len(args) < 2:
        return "Формат: <code>/coef 379499 1.85</code>  (id и коэф; ставка опц.: <code>/coef 379499 1.85 2</code>)"
    bid, odds = args[0], args[1]
    stake = args[2] if len(args) > 2 else "1"
    try:
        odds_f, stake_f = float(odds), float(stake)
    except ValueError:
        return "Коэф/ставка должны быть числами. Пример: <code>/coef 379499 1.85</code>"
    conn = _conn()
    row = conn.execute("SELECT team1,team2,side FROM cs2_bets WHERE id=?", (bid,)).fetchone()
    if not row:
        conn.close()
        return f"Пик {bid} не найден."
    conn.execute("UPDATE cs2_bets SET taken_odds=?, stake=?, status='active' WHERE id=?",
                 (odds_f, stake_f, bid))
    conn.commit()
    conn.close()
    return (f"✅ Записал: {row['team1']} v {row['team2']} — ставка на <b>{row['side']}</b> "
            f"@ {odds_f} ({stake_f}u). Рассчитаю по итогу матча.")


def cmd_pending() -> str:
    conn = _conn()
    rows = conn.execute(
        "SELECT id,team1,team2,side,model_fair_odds,status FROM cs2_bets "
        "WHERE status IN ('pending','active') ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    if not rows:
        return "Нет открытых пиков."
    out = ["<b>Открытые пики:</b>"]
    for r in rows:
        tag = "⏳ нет коэфа" if r["status"] == "pending" else "🟢 активна"
        out.append(f"<code>{r['id']}</code> {r['team1']} v {r['team2']} → {r['side']} "
                   f"(спр.{r['model_fair_odds']}) {tag}")
    return "\n".join(out)


def cmd_roi() -> str:
    conn = _conn()
    rows = conn.execute("SELECT taken_odds,stake,status FROM cs2_bets WHERE status IN ('won','lost')").fetchall()
    conn.close()
    if not rows:
        return "Ещё нет рассчитанных ставок."
    n = len(rows)
    wins = sum(1 for r in rows if r["status"] == "won")
    staked = sum(float(r["stake"] or 1) for r in rows)
    pl = sum((float(r["stake"] or 1) * (float(r["taken_odds"]) - 1)) if r["status"] == "won"
             else -float(r["stake"] or 1) for r in rows)
    return (f"📈 Ставок: {n} | винрейт {wins/n*100:.0f}%\n"
            f"ROI: {pl/staked*100:+.1f}% ({pl:+.2f}u / {staked:.1f}u)")


def handle(text: str) -> str | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    args = parts[1:]
    if cmd == "coef":
        return cmd_coef(args)
    if cmd == "pending":
        return cmd_pending()
    if cmd == "roi":
        return cmd_roi()
    if cmd in ("help", "start"):
        return ("CS2 форвард-тест. Скобки печатать НЕ нужно, просто число:\n"
                "<code>/coef 379499 1.85</code> — записать коэф (ставка опц.: <code>/coef 379499 1.85 2</code>)\n"
                "<code>/pending</code> — открытые пики\n"
                "<code>/roi</code> — текущий ROI")
    return None


def main() -> None:
    if not TOKEN or not CHAT:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set in cs2.env")
        return
    print("cs2 betbot polling…")
    offset = 0
    while True:
        try:
            url = f"{API}/getUpdates?timeout=30&offset={offset}"
            with urllib.request.urlopen(url, timeout=40) as r:
                data = json.load(r)
        except Exception as exc:  # noqa: BLE001
            print(f"poll error: {exc}")
            time.sleep(5)
            continue
        for u in data.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message") or {}
            if str((msg.get("chat") or {}).get("id")) != CHAT:
                continue
            out = handle(msg.get("text", ""))
            if out:
                reply(out)


if __name__ == "__main__":
    main()
