#!/usr/bin/env python3
"""Full pre-match BOARD: every upcoming CS2 match with the model's read — favourite %, and the
anti-sweep 'takes >=1 map' near-lock (from the MC score_dist). Needs ONLY the model, so it fills
even when winline has no pre-match line yet. Overlays winline odds + a value flag when a line
exists. Writes data/prematch_board.json for the dashboard 'Ближайшие матчи' section."""
from __future__ import annotations
import json, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3          # noqa: E402
import veto_sim as vs            # noqa: E402
import winline_odds              # noqa: E402
import line_anomaly as la       # noqa: E402

HOURS_AHEAD = 14
MIN_RATED = 3
OUT = ROOT / "data" / "prematch_board.json"
NEWS_CACHE = ROOT / "data" / "prematch_news_cache.json"


def _news_cached(n1, n2) -> str:
    """grok+gemini news for a match, cached 3h (value picks are few; avoids re-hitting the AIs)."""
    key = f"{la._norm(n1)}|{la._norm(n2)}"
    try:
        cache = json.loads(NEWS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    e = cache.get(key)
    if e and time.time() - e.get("ts", 0) < 10800:
        return e.get("news", "")
    try:
        news = la._news(n1, n2)
    except Exception:
        news = ""
    cache[key] = {"news": news, "ts": time.time()}
    try:
        NEWS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return news


def _log_picks(board, now):
    LOG = ROOT / "data" / "prematch_board_log.jsonl"
    existing = set()
    if LOG.exists():
        for l in LOG.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l); existing.add((r.get("t1"), r.get("t2"), r.get("side"), r.get("start_date")))
            except Exception:
                pass
    new = []
    for r in board:
        picks = []
        if r.get("verdict") == "VALUE" and r.get("bet_side"):
            picks.append(("ml", r["bet_side"], r.get("bet_odd")))
        asw = r.get("antisweep") or ["", 0]
        if (asw[1] or 0) >= 0.85:
            picks.append(("antisweep", asw[0], None))
        for market, side, odd in picks:
            if (r["t1"], r["t2"], side, r.get("start_date")) in existing:
                continue
            new.append({"t1": r["t1"], "t2": r["t2"], "market": market, "side": side, "odd": odd,
                        "pm_fav": r.get("pm_fav"), "news": r.get("news", ""),
                        "start_date": r.get("start_date"), "logged_at": now.isoformat()})
    if new:
        with LOG.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new)


def build() -> int:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=HOURS_AHEAD)
    try:
        r = bo3._get("/matches", {"filter[matches.status][eq]": "upcoming",
                                  "filter[matches.discipline_id][eq]": 1,
                                  "page[limit]": 60, "sort": "start_date"})
        ups = r.get("results", r) or []
    except Exception as e:  # noqa: BLE001
        print("bo3 fetch failed:", e); return 0
    board = []
    try:
        mm = la.movement_map()
    except Exception:
        mm = {}
    for m in ups:
        t1, t2, bo = m.get("team1_id"), m.get("team2_id"), m.get("bo_type") or 3
        sd = m.get("start_date")
        if not (t1 and t2):
            continue
        try:
            if datetime.fromisoformat(sd.replace("Z", "+00:00")) > horizon:
                continue
        except Exception:  # noqa: BLE001
            pass
        try:
            pred = vs.predict(t1, t2, bo)
        except Exception:  # noqa: BLE001
            continue
        if pred.get("rated1", 0) < MIN_RATED or pred.get("rated2", 0) < MIN_RATED:
            continue
        n1, n2 = pred["team1"], pred["team2"]
        pm1, pm2 = pred["p1_win"], pred["p2_win"]
        sw1 = pred["score_dist"].get("0-2", 0.0)      # team1 wins 0 maps
        sw2 = pred["score_dist"].get("2-0", 0.0)      # team2 wins 0 maps
        anti = [(n1, round(1 - sw1, 3)), (n2, round(1 - sw2, 3))]
        al = max(anti, key=lambda x: x[1])
        rec = {"t1": n1, "t2": n2, "bo": bo, "fav": n1 if pm1 >= pm2 else n2,
               "pm_fav": round(max(pm1, pm2), 3), "pf_fav": None, "ml1": None, "ml2": None,
               "verdict": "PASS", "bet_side": None, "bet_odd": None,
               "antisweep": [al[0], al[1]], "start_date": sd}
        try:
            wl = winline_odds.lookup(n1, n2)
        except Exception:  # noqa: BLE001
            wl = None
        if wl and (wl.get("odd1") or 0) > 1 and (wl.get("odd2") or 0) > 1:
            ml1, ml2 = wl["odd1"], wl["odd2"]
            q1, q2 = 1 / ml1, 1 / ml2
            pf1, pf2 = q1 / (q1 + q2), q2 / (q1 + q2)
            rec["ml1"], rec["ml2"] = ml1, ml2
            rec["pf_fav"] = round(max(pf1, pf2), 3)
            edge1 = pm1 - pf1
            if 0.07 <= abs(edge1) <= 0.15:            # model-side genuine value (same band as scanner)
                side, po = (n1, ml1) if edge1 > 0 else (n2, ml2)
                rec["verdict"], rec["bet_side"], rec["bet_odd"] = "VALUE", side, po
        mv = mm.get(frozenset((la._norm(n1), la._norm(n2))))
        if mv:
            model_side = rec.get("bet_side") or rec["fav"]
            rec["line"] = {"dir": "confirm" if la._norm(mv["side"]) == la._norm(model_side) else "against",
                           "side": mv["side"], "type": mv["type"]}
        rec["news"] = ""
        if rec["verdict"] == "VALUE" or (rec.get("antisweep") or ["", 0])[1] >= 0.78:
            rec["news"] = _news_cached(n1, n2)
        board.append(rec)
    OUT.write_text(json.dumps({"updated": now.isoformat(), "matches": board}, ensure_ascii=False),
                   encoding="utf-8")
    try:
        nlog = _log_picks(board, now)
        if nlog:
            print(f"logged {nlog} new board picks for tracking")
    except Exception as _e:
        print("log_picks err:", _e)
    locks = sum(1 for r in board if r["antisweep"][1] >= 0.90)
    print(f"board: {len(board)} upcoming | {locks} near-locks (>=90% take >=1 map)")
    return len(board)


if __name__ == "__main__":
    build()
