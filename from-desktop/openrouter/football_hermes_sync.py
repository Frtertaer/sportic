#!/usr/bin/env python3
"""Port the football bot's self-learning into the shared Hermes learn-svc (:8909).
READ-ONLY on sportsbot.db (never touches the live bot): pulls active lessons the football
self-learning has distilled and registers each as a Hermes skill, so CS2 + football feed
ONE procedural memory that the Hermes curator can consolidate. Runs on a timer -> forward
lessons sync automatically. State kept in a side file (no DB writes)."""
from __future__ import annotations
import sqlite3, json, re, urllib.request
from pathlib import Path
from datetime import datetime, timezone

DB = "/opt/sportsbot/sportsbot.db"
LEARN = "http://127.0.0.1:8909"
STATE = Path("/opt/sportsbot/data/football_hermes_synced.json")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:50]


def _post(path: str, payload: dict) -> dict | None:
    try:
        req = urllib.request.Request(LEARN + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    except Exception as e:  # noqa: BLE001
        print("  post", path, "failed:", type(e).__name__)
        return None


def run() -> None:
    try:
        synced = set(json.loads(STATE.read_text(encoding="utf-8"))) if STATE.exists() else set()
    except Exception:  # noqa: BLE001
        synced = set()
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = c.execute("SELECT id, text, category, confidence, times_confirmed, source_match "
                         "FROM lessons WHERE active=1").fetchall()
        c.close()
    except Exception as e:  # noqa: BLE001
        print("football DB read failed:", e); return
    n_new = 0
    for lid, text, category, conf, confirmed, src in rows:
        key = f"football-{lid}"
        if key in synced:
            continue
        name = f"football-{_slug(category or 'lesson')}-{lid}"
        payload = {
            "name": name,
            "description": (text or "")[:300],                    # Hermes matches on description
            "body": f"[football / {category}] {text}\n(confidence {conf}, confirmed {confirmed}x"
                    + (f", match {src}" if src else "") + ")",
            "tags": ["football", "betting", "lesson", _slug(category or "")],
        }
        r = _post("/skill", payload)
        if r is not None:
            synced.add(key); n_new += 1
            print(f"  synced lesson {lid} [{category}] -> {name}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(sorted(synced)), encoding="utf-8")
    # a short reflection so the curator sees football is active
    if n_new:
        _post("/reflect", {"task": "Football self-learning sync", "model": "claude-code",
                           "outcome": f"registered {n_new} football lesson(s) into shared memory",
                           "success": True, "tags": ["football", "betting"]})
    print(f"football->Hermes: {n_new} new lesson(s) synced, {len(rows)} active total")


if __name__ == "__main__":
    run()
