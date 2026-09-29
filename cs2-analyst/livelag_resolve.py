#!/usr/bin/env python3
"""Grade the LIVE line-lag value bets (live_edge.py) vs bo3 final SERIES results -> a real win-rate
track (data/livelag_ft_results.jsonl). The bet is 'leader wins the series' at the logged live odd."""
from __future__ import annotations
import json, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3   # noqa: E402
import veto_sim as vs      # noqa: E402

LOG = ROOT / "data" / "livelag_ft_log.jsonl"
RES = ROOT / "data" / "livelag_ft_results.jsonl"


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _finished() -> list[dict]:
    try:
        r = bo3._get("/matches", {"filter[matches.status][eq]": "finished",
                                  "filter[matches.discipline_id][eq]": 1,
                                  "page[limit]": 60, "sort": "-start_date"})
        return r.get("results", r) or []
    except Exception as e:  # noqa: BLE001
        print("bo3 finished fetch failed:", e); return []


def run() -> None:
    bets = _read(LOG)
    done = {f"{r['t1']}|{r['t2']}|{r['logged_at']}" for r in _read(RES)}
    pending = [b for b in bets if f"{b['t1']}|{b['t2']}|{b['logged_at']}" not in done]
    if not pending:
        print("no pending live-lag bets"); return
    fin = _finished()
    conn = vs._conn()
    names = {}

    def nm(tid):
        if tid not in names:
            try:
                names[tid] = vs.team_name(conn, tid)
            except Exception:  # noqa: BLE001
                names[tid] = ""
        return names[tid]

    graded = 0
    out = []
    for b in pending:
        b1, b2, lead = _norm(b["t1"]), _norm(b["t2"]), _norm(b["leader"])
        match = None
        for m in fin:
            n1, n2 = _norm(nm(m.get("team1_id"))), _norm(nm(m.get("team2_id")))
            pair = {n1, n2}
            if not (n1 and n2):
                continue
            if (any(x and (x in n1 or n1 in x) for x in (b1, b2)) and
                    any(x and (x in n2 or n2 in x) for x in (b1, b2))):
                match = m
                break
        if not match:
            continue
        s1, s2 = int(match.get("team1_score") or 0), int(match.get("team2_score") or 0)
        win_name = _norm(nm(match.get("winner_team_id")))
        won = bool(win_name and (lead in win_name or win_name in lead))
        rec = dict(b)
        rec["outcome"] = "win" if won else "loss"
        rec["series_final"] = f"{s1}-{s2}"
        rec["pl_units"] = round((b["odd"] - 1) if won else -1.0, 2)
        rec["settled_at"] = datetime.now(timezone.utc).isoformat()
        out.append(rec)
        graded += 1
    conn.close()
    if out:
        with RES.open("a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"graded {graded} live-lag bets (pending was {len(pending)})")


if __name__ == "__main__":
    run()
