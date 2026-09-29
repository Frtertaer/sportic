#!/usr/bin/env python3
"""CS2 value-pick monitor: scan rated upcoming matches → 4-AI council → Telegram alert.

For each upcoming match where both teams are rated, runs the full analyst (per-map
Glicko + veto sim + grok/gemini live-search + 4-AI council). When the council's
DECISION is BET (not PASS), it logs the pick to cs2_bets (SQLite) and DMs the user
the matchup + model fair odds + the council synthesis + how to record the odds taken.
Results/ROI settle later from bo3.gg (cs2_resolve.py). Semi-manual odds by design —
RU-book auto-odds is a future enhancement.

  python cs2_monitor.py [--days N] [--max M] [--dry]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import analyst  # noqa: E402  (imports veto_sim, bo3_client, ratings)
import bo3_client as bo3  # noqa: E402
import winline_odds  # noqa: E402  (RU-book auto-odds: reads data/winline_odds.json)

DB = ROOT / "cs2.db"
MIN_EDGE = 0.03  # min model-vs-winline edge to confirm a value bet


def load_cfg() -> dict:
    cfg = {}
    p = ROOT / "cs2.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = load_cfg()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("""CREATE TABLE IF NOT EXISTS cs2_bets (
        id TEXT PRIMARY KEY, slug TEXT, team1 TEXT, team2 TEXT, bo INTEGER,
        side TEXT, market TEXT, conf TEXT, model_p REAL, model_fair_odds REAL,
        synth TEXT, created_at TEXT, taken_odds REAL, stake REAL,
        status TEXT DEFAULT 'pending', result TEXT
    )""")
    return c


def short_id(slug: str) -> str:
    return hashlib.sha1(slug.encode()).hexdigest()[:6]


def rated_set(conn) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT team_id FROM map_ratings GROUP BY team_id HAVING SUM(games) >= 8")}


def candidates(conn, days: int) -> list[dict]:
    rated = rated_set(conn)
    now = datetime.now(timezone.utc)
    raw = bo3._get("/matches", {
        "filter[matches.status][eq]": "upcoming",
        "filter[matches.discipline_id][eq]": 1, "sort": "start_date",
        "page[offset]": 0, "page[limit]": 80,
    }).get("results", [])
    out = []
    for m in raw:
        t1, t2 = m.get("team1_id"), m.get("team2_id")
        if t1 not in rated or t2 not in rated:
            continue
        try:
            ko = datetime.fromisoformat((m.get("start_date") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if (ko - now).total_seconds() > days * 86400 or ko < now:
            continue
        out.append({"slug": m.get("slug"), "t1": t1, "t2": t2,
                    "bo": m.get("bo_type") or 3, "start": m.get("start_date")})
    return out


def telegram_send(text: str) -> bool:
    token, chat = CFG.get("TELEGRAM_TOKEN"), CFG.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram not configured")
        return False
    body = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000],
                                   "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=body, timeout=20) as r:
            return json.load(r).get("ok", False)
    except Exception as exc:  # noqa: BLE001
        print(f"telegram failed: {exc}")
        return False


def _side_fair(pred: dict, side: str) -> tuple[float | None, float | None]:
    """Match the decision side to the model's ML prob + fair odds."""
    if not side:
        return None, None
    import re
    n = re.sub(r"[^a-zа-я0-9]", "", side.lower())
    n1 = re.sub(r"[^a-zа-я0-9]", "", str(pred["team1"]).lower())
    n2 = re.sub(r"[^a-zа-я0-9]", "", str(pred["team2"]).lower())
    if n and (n in n1 or n1 in n):
        return pred["p1_win"], pred["fair_odds_t1"]
    if n and (n in n2 or n2 in n):
        return pred["p2_win"], pred["fair_odds_t2"]
    return None, None


def run(days: int, max_m: int, dry: bool) -> None:
    conn = _conn()
    have = {r[0] for r in conn.execute("SELECT id FROM cs2_bets")}
    cands = candidates(conn, days)
    print(f"{len(cands)} rated upcoming matches in next {days}d")
    alerts = 0
    for c in cands:
        bid = short_id(c["slug"])
        if bid in have:
            continue
        try:
            res = analyst.analyze(c["t1"], c["t2"], c["bo"])
        except Exception as exc:  # noqa: BLE001
            print(f"  analyze {c['slug']} failed: {exc}")
            continue
        dec = res["decision"]
        pred = res["pred"]
        verdict = "PASS" if dec["action"] != "BET" else f"BET {dec['side']} ({dec.get('market')})"
        print(f"  {pred['team1']} vs {pred['team2']}: {verdict}")
        if dec["action"] != "BET":
            continue
        p, fair = _side_fair(pred, dec["side"])
        # ── auto-odds from winline (RU book) → real edge vs the book ──
        wl = winline_odds.lookup(pred["team1"], pred["team2"]) or winline_odds.lookup_slug(c["slug"])
        real_odd = edge = None
        if wl and p:
            ns = re.sub(r"[^a-zа-я0-9]", "", dec["side"].lower())
            n1 = re.sub(r"[^a-zа-я0-9]", "", str(pred["team1"]).lower())
            real_odd = wl["odd1"] if (ns and (ns in n1 or n1 in ns)) else wl["odd2"]
            edge = round(p * real_odd - 1.0, 3)
        # winline says NO value → skip (the real line removes the council's edge)
        if real_odd is not None and edge is not None and edge < MIN_EDGE:
            print(f"  {pred['team1']} v {pred['team2']}: BET {dec['side']} but winline "
                  f"{real_odd} → edge {edge:+.0%} < {MIN_EDGE:+.0%}, skip")
            conn.execute("INSERT OR REPLACE INTO cs2_bets "
                         "(id,slug,team1,team2,bo,side,market,conf,model_p,model_fair_odds,"
                         "synth,created_at,taken_odds,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'novalue')",
                         (bid, c["slug"], pred["team1"], pred["team2"], c["bo"], dec["side"],
                          dec.get("market"), dec.get("conf"), p, fair, res["synth"],
                          datetime.now(timezone.utc).isoformat(), real_odd))
            conn.commit()
            continue

        rt_line = ""
        if pred.get("round_totals"):
            tl = []
            for r in pred["round_totals"]:
                side, odd = (("ТБ", r["fair_over"]) if r["p_over_23_5"] >= 0.5
                             else ("ТМ", r["fair_under"]))
                tl.append(f"  • {r['map']}: модель ждёт ~{r['exp_rounds']:.0f} раундов "
                          f"(наш {side} 23.5 @ {odd})")
            rt_line = ("🎲 <b>Тоталы раундов</b>:\n" + "\n".join(tl)
                       + "\n  <i>value: бери ТМ/ТБ, если коэф бука на той же линии ВЫШЕ нашего "
                       "@; либо если линия бука сильно расходится с нашим числом раундов.</i>\n")
        synth_tail = res["synth"].split("DECISION:")[0].strip()[-800:]
        if real_odd is not None:
            odds_line = (f"🏦 <b>winline {dec['side']}: {real_odd}</b> | справедл. {fair} | "
                         f"край <b>{edge*100:+.0f}%</b> ✅ VALUE\n"
                         f"👉 ставь {dec['side']} @ {real_odd} на winline (записано авто)\n")
            status, taken = "active", real_odd
        else:
            odds_line = (f"💡 Справедл. коэф {dec['side']}: <b>{fair if fair else '?'}</b> "
                         f"→ бери у бука ВЫШЕ = value (на winline нет — введи вручную)\n"
                         f"👉 поставишь — <code>/coef {bid} 1.85</code>\n")
            status, taken = "pending", None
        msg = (
            f"🎯 <b>CS2 value-пик</b> — {pred['team1']} vs {pred['team2']} (Bo{pred['bo']})\n"
            f"⏰ {c['start']}\n"
            f"📊 Совет: <b>BET {dec['side']}</b> ({dec.get('market') or 'ml'}), увер. {dec.get('conf') or '?'}\n"
            f"🧮 Модель: {pred['team1']} {pred['p1_win']*100:.0f}% / {pred['team2']} {pred['p2_win']*100:.0f}%\n"
            f"{odds_line}"
            f"{rt_line}\n"
            f"<b>Синтез коллегии:</b>\n{synth_tail}"
        )
        if dry:
            print("=" * 50 + "\n" + msg + f"\n[status={status} odds={taken}]")
        else:
            if telegram_send(msg):
                conn.execute(
                    "INSERT OR REPLACE INTO cs2_bets "
                    "(id,slug,team1,team2,bo,side,market,conf,model_p,model_fair_odds,"
                    "synth,created_at,taken_odds,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (bid, c["slug"], pred["team1"], pred["team2"], c["bo"], dec["side"],
                     dec.get("market"), dec.get("conf"), p, fair, res["synth"],
                     datetime.now(timezone.utc).isoformat(), taken, status))
                conn.commit()
                alerts += 1
                print(f"    alerted + logged {bid} (status={status}, odds={taken})")
        if alerts >= max_m:
            break
    print(f"done. alerts={alerts}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    run(args.days, args.max, args.dry)


if __name__ == "__main__":
    main()
