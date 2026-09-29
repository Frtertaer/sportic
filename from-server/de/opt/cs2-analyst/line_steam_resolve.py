#!/usr/bin/env python3
"""Grade winline STEAM follows (line_anomaly.py --live) vs bo3 final results -> a real win-rate track
(data/line_steam_results.jsonl). Bet = 'the steamed side wins the match' at the entry odd. THE
VALIDATION GATE: does 'follow the steam' actually win? No real money until this shows an edge."""
from __future__ import annotations
import json, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3   # noqa: E402
import veto_sim as vs      # noqa: E402
import gcs_lookup          # noqa: E402

LOG = ROOT / "data" / "line_steam_log.jsonl"
RES = ROOT / "data" / "line_steam_results.jsonl"


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum()).upper()


def _read(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def _finished() -> list[dict]:
    try:
        r = bo3._get("/matches", {"filter[matches.status][eq]": "finished",
                                  "filter[matches.discipline_id][eq]": 1,
                                  "page[limit]": 80, "sort": "-start_date"})
        return r.get("results", r) or []
    except Exception as e:  # noqa: BLE001
        print("bo3 finished fetch failed:", e); return []


def _summary() -> None:
    res = _read(RES)
    if not res:
        print("STEAM track: 0 resolved yet"); return
    n = len(res); w = sum(1 for r in res if r.get("outcome") == "win")
    pl = sum(r.get("pl_units", 0) for r in res)
    print(f"STEAM track: {w}/{n} = {100*w/n:.0f}% winrate | P/L {pl:+.2f}u | ROI {100*pl/n:+.1f}%")


def run() -> None:
    bets = _read(LOG)
    done = {f"{r['t1']}|{r['t2']}|{r['logged_at']}" for r in _read(RES)}
    pending = [b for b in bets if f"{b['t1']}|{b['t2']}|{b['logged_at']}" not in done]
    if not pending:
        print("no pending steams"); _summary(); return
    gcs_res = gcs_lookup.load()
    fin = _finished()
    conn = vs._conn(); names = {}

    def nm(tid):
        if tid not in names:
            try:
                names[tid] = vs.team_name(conn, tid)
            except Exception:  # noqa: BLE001
                names[tid] = ""
        return names[tid]

    out = []
    for b in pending:
        b1, b2, side = _norm(b["t1"]), _norm(b["t2"]), _norm(b["side"])
        g = gcs_lookup.find(b["t1"], b["t2"], gcs_res)
        if g:
            won = bool(side and (side in g["winner_n"] or g["winner_n"] in side))
            rec = dict(b); rec["outcome"] = "win" if won else "loss"
            rec["series_final"] = f'{g["s1"]}-{g["s2"]}'; rec["source"] = "gcs"
            rec["pl_units"] = round((b["entry"] - 1) if won else -1.0, 2)
            rec["settled_at"] = datetime.now(timezone.utc).isoformat(); out.append(rec); continue
        match = None
        for m in fin:
            n1, n2 = _norm(nm(m.get("team1_id"))), _norm(nm(m.get("team2_id")))
            if not (n1 and n2):
                continue
            if (any(x and (x in n1 or n1 in x) for x in (b1, b2)) and
                    any(x and (x in n2 or n2 in x) for x in (b1, b2))):
                match = m
                break
        if not match:
            continue
        s1, s2 = int(match.get("team1_score") or 0), int(match.get("team2_score") or 0)
        win = _norm(nm(match.get("winner_team_id")))
        won = bool(win and (side in win or win in side))
        rec = dict(b)
        rec["outcome"] = "win" if won else "loss"
        rec["series_final"] = f"{s1}-{s2}"
        rec["pl_units"] = round((b["entry"] - 1) if won else -1.0, 2)
        rec["settled_at"] = datetime.now(timezone.utc).isoformat()
        out.append(rec)
    conn.close()
    if out:
        with RES.open("a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"graded {len(out)} steams (pending was {len(pending)})")
    _summary()


if __name__ == "__main__":
    run()
