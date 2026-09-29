#!/usr/bin/env python3
"""Grade the pre-match VALUE bets (prematch_scan.py) vs bo3 final SERIES results
-> data/prematch_ft_results.jsonl. Bet = 'bet_side wins the series' at bet_odd."""
from __future__ import annotations
import json, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3   # noqa: E402
import veto_sim as vs      # noqa: E402

LOG = ROOT / "data" / "prematch_ft_log.jsonl"
RES = ROOT / "data" / "prematch_ft_results.jsonl"


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def run() -> None:
    bets = [b for b in _read(LOG) if b.get("bet_side") and b.get("bet_odd")]
    done = {r.get("slug") for r in _read(RES)}
    pending = [b for b in bets if b.get("slug") not in done]
    if not pending:
        print("no pending pre-match bets"); return
    out = []
    for b in pending:
        try:
            m = bo3._get(f"/matches/{b['slug']}", {"scope": "show-match"})
            m = m.get("results", m)
        except Exception:  # noqa: BLE001
            continue
        if not m or m.get("status") != "finished":
            continue
        s1, s2 = int(m.get("team1_score") or 0), int(m.get("team2_score") or 0)
        conn = vs._conn()
        winner = _norm(vs.team_name(conn, m.get("winner_team_id")))
        conn.close()
        won = bool(winner and (_norm(b["bet_side"]) in winner or winner in _norm(b["bet_side"])))
        rec = dict(b)
        rec["outcome"] = "win" if won else "loss"
        rec["series_final"] = f"{s1}-{s2}"
        rec["pl_units"] = round((b["bet_odd"] - 1) if won else -1.0, 2)
        rec["settled_at"] = datetime.now(timezone.utc).isoformat()
        out.append(rec)
    if out:
        with RES.open("a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"graded {len(out)} pre-match bets (pending was {len(pending)})")


if __name__ == "__main__":
    run()
