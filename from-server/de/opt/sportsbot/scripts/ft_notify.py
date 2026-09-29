#!/usr/bin/env python3
"""Push newly-logged forward-test football picks to Telegram.

forward_test.py only WRITES quality (+EV) picks to data/forward_test_log.jsonl (for ROI
tracking) — it never DMs them, so the user never saw any "offers". This reads the log,
finds picks not yet notified whose match is still upcoming, and DMs them via the SportsBot
bot. Dedups by prediction_id in data/ft_notified.json. Wired as ExecStartPost on
sportsbot-ft-predict.service so every daily run that logs a pick also delivers it.

  python scripts/ft_notify.py [--all] [--dry]
    --all  also send picks for matches already kicked off (default: upcoming only)
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "forward_test_log.jsonl"
STATE = ROOT / "data" / "ft_notified.json"
CHAT_ID = 488469657


def _token() -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        return None
    for ln in env.read_text(encoding="utf-8").splitlines():
        if ln.startswith("TELEGRAM_TOKEN="):
            return ln.split("=", 1)[1].strip().strip("'\"")
    return None


def _send(token: str, text: str) -> bool:
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                   "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        r = urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                                   data=data, timeout=15)
        return bool(json.load(r).get("ok"))
    except Exception as e:  # noqa: BLE001
        print(f"  send failed: {str(e)[:160]}")
        return False


def _fmt(p: dict) -> str:
    edge = (p.get("edge") or 0) * 100
    mp = (p.get("model_prob") or 0) * 100
    conf = p.get("confidence_pct")
    return (
        f"⚽ <b>Футбол value-пик</b> — {p.get('team1')} vs {p.get('team2')}\n"
        f"🏆 {p.get('competition','?')} | ⏰ {p.get('kickoff_date','?')}\n"
        f"🎯 <b>{p.get('recommendation')}</b> @ {p.get('bet_odds')}\n"
        f"🧮 модель {mp:.0f}% | край <b>+{edge:.0f}%</b>"
        + (f" | увер. {conf}%" if conf else "")
        + (f" | риск {p.get('risk')}" if p.get('risk') else "")
        + (f" | ставка {p.get('stake_units')}u" if p.get('stake_units') else "")
        + f"\n<i>(forward-test, данные {p.get('completeness_pct','?')}%)</i>"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    token = _token()
    if not token:
        print("no TELEGRAM_TOKEN"); return
    if not LOG.exists():
        print("no forward_test_log.jsonl"); return

    notified = set()
    if STATE.exists():
        try:
            notified = set(json.loads(STATE.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            notified = set()

    today = date.today().isoformat()
    sent = 0
    for ln in LOG.read_text(encoding="utf-8").splitlines():
        try:
            p = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        pid = p.get("prediction_id")
        if pid is None or p.get("skipped") or not p.get("recommendation"):
            continue
        if pid in notified:
            continue
        upcoming = (p.get("kickoff_date") or "9999") >= today
        if upcoming or args.all:
            if args.dry:
                print("WOULD SEND:\n" + _fmt(p))
                sent += 1
            elif _send(token, _fmt(p)):
                sent += 1
                print(f"  sent pid={pid} {p.get('team1')} v {p.get('team2')}")
        notified.add(pid)

    if not args.dry:
        STATE.write_text(json.dumps(sorted(notified)), encoding="utf-8")
    print(f"done. sent={sent} | total notified={len(notified)}")


if __name__ == "__main__":
    main()
