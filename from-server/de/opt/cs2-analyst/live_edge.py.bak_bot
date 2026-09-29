#!/usr/bin/env python3
"""CS2 LIVE line-lag edge — the market-inefficiency play (council-unanimous, 2026-07).

winline.ru reprices its LIVE series moneyline every ~2-3 min, but the current-map ROUND SCORE
changes faster. When a team is up big on the in-progress map (e.g. 11-4), it closes that map
~94% of the time; if that map's result nearly decides the SERIES (decider, or the leader is
already 1-0 and winning), the true series prob is ~0.9+ while the stale live ML still pays value.
We exploit the LATENCY, not prediction. Fire only on near-certain states (P_series>=0.85) with a
real edge (>=8%) on a FRESH scrape, and skip suspicious (big underdog suddenly dominating = throw risk).

Reads data/winline_live.json (scraped by cs2-winline-live.timer). Logs qualifying value bets to
data/livelag_ft_log.jsonl and DMs Telegram. A resolver grades them vs bo3 -> a real win-rate track.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import state_model as sm          # noqa: E402
import monitor as mon            # noqa: E402  (telegram_send)

WL = ROOT / "data" / "winline_live.json"
PRE = ROOT / "data" / "winline_odds.json"
FT_LOG = ROOT / "data" / "livelag_ft_log.jsonl"
SEEN = ROOT / "data" / "livelag_seen.json"

MIN_P_SERIES = 0.85     # only near-certain series outcomes
MIN_EDGE = 0.08         # book must still lag our fair price by >=8%
MIN_LEADER_RD = 10      # leader must have >=10 rounds (map near its end)
MIN_LEAD = 4            # round differential
MAX_STALE_S = 200       # skip if the scrape is older than this (we'd be the sucker)
SUSPECT_UNDERDOG = 2.6  # leader whose pre-match ML was >this = possible throw -> skip


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _prematch_odd(t_lead: str, t_other: str) -> float | None:
    """Pre-match ML for the current-map leader, to flag a suspicious underdog surge."""
    try:
        rows = json.loads(PRE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    a, b = _norm(t_lead), _norm(t_other)
    for r in rows:
        r1, r2 = _norm(r.get("t1", "")), _norm(r.get("t2", ""))
        if (a in r1 or r1 in a) and (b in r2 or r2 in b) and r.get("ml1"):
            return r["ml1"]
        if (a in r2 or r2 in a) and (b in r1 or r1 in b) and r.get("ml2"):
            return r["ml2"]
    return None


def scan(dry: bool = False) -> int:
    if not WL.exists():
        print("no winline_live.json"); return 0
    age = time.time() - WL.stat().st_mtime
    if age > MAX_STALE_S:
        print(f"live feed stale ({age:.0f}s) — skip"); return 0
    try:
        rows = json.loads(WL.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("bad winline_live.json:", e); return 0
    try:
        seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    except Exception:  # noqa: BLE001
        seen = {}

    fired = 0
    for r in rows:
        ms = r.get("map_score")
        ser = r.get("series") or [0, 0]
        if not ms or len(ms) < 2:
            continue
        r1, r2 = int(ms[0]), int(ms[1])
        t1, t2 = r.get("t1"), r.get("t2")
        o1, o2 = r.get("odd1"), r.get("odd2")
        cur_map = r.get("cur_map")
        if not (t1 and t2 and o1 and o2 and cur_map):
            continue
        if max(r1, r2) - min(r1, r2) < MIN_LEAD or max(r1, r2) < MIN_LEADER_RD:
            continue
        # current-map leader + its series-ML odd
        if r1 > r2:
            lead, other, lead_odd, sa, sb = t1, t2, o1, int(ser[0]), int(ser[1])
        else:
            lead, other, lead_odd, sa, sb = t2, t1, o2, int(ser[1]), int(ser[0])
        need = 3 if max(sa, sb) >= 2 else 2                 # Bo5 if a team already has 2, else Bo3
        p_map = sm.map_state_winprob(max(r1, r2), min(r1, r2))
        p_series = sm.series_winprob(sa, sb, p_map, need)
        edge = p_series * lead_odd - 1
        if p_series < MIN_P_SERIES or edge < MIN_EDGE:
            continue
        # suspicious: current-map leader was a big pre-match underdog -> throw risk, skip
        pre = _prematch_odd(lead, other)
        suspect = pre is not None and pre > SUSPECT_UNDERDOG
        key = f"{_norm(t1)}|{_norm(t2)}|m{cur_map}|{max(r1,r2)}-{min(r1,r2)}"
        if key in seen:
            continue
        tag = "  ⚠️ underdog-surge (throw risk) — SKIP" if suspect else ""
        line = (f"⚡ <b>CS2 LIVE lag-value</b> — {t1} vs {t2}\n"
                f"🗺 карта {cur_map}: <b>{lead} ведёт {max(r1,r2)}-{min(r1,r2)}</b> "
                f"(закрытие карты ~{p_map*100:.0f}%)\n"
                f"🎯 серия {sa}-{sb} → P({lead} берёт серию) = <b>{p_series*100:.0f}%</b>  "
                f"(справедл. {1/p_series:.2f})\n"
                f"🏦 winline live: <b>{lead} @ {lead_odd}</b> → край <b>+{edge*100:.0f}%</b>{tag}")
        print(line.replace("<b>", "").replace("</b>", ""))
        if suspect:
            seen[key] = "skip-suspect"
            continue
        if not dry:
            mon.telegram_send(line)
            with FT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "t1": t1, "t2": t2, "cur_map": cur_map, "series": [sa, sb],
                    "map_score": [max(r1, r2), min(r1, r2)], "leader": lead, "odd": lead_odd,
                    "p_map": round(p_map, 3), "p_series": round(p_series, 3), "edge": round(edge, 3),
                    "logged_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
        seen[key] = datetime.now(timezone.utc).isoformat()
        fired += 1

    SEEN.write_text(json.dumps(seen), encoding="utf-8")
    print(f"done. live-lag value bets fired={fired}")
    return fired


if __name__ == "__main__":
    scan(dry="--dry" in sys.argv)
