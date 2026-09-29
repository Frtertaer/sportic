#!/usr/bin/env python3
"""Append the current winline pre-match ML + totals snapshots (timestamped) to
data/odds_history.jsonl — builds the odds-movement history that steam / suspicious-match
detection needs (council: start accumulating now). Cheap, append-only."""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
HIST = ROOT / "data" / "odds_history.jsonl"
now = datetime.now(timezone.utc).isoformat()
recs = []

try:
    for r in json.loads((ROOT / "data" / "winline_odds.json").read_text(encoding="utf-8")):
        recs.append({"ts": now, "kind": "ml", "t1": r.get("t1"), "t2": r.get("t2"),
                     "ml1": r.get("ml1"), "ml2": r.get("ml2")})
except Exception:  # noqa: BLE001
    pass

try:
    for r in json.loads((ROOT / "data" / "winline_totals.json").read_text(encoding="utf-8")):
        for t in (r.get("totals") or []):
            recs.append({"ts": now, "kind": "total", "t1": r.get("t1"), "t2": r.get("t2"),
                         "map_no": t.get("map_no"), "line": t.get("line"),
                         "over": t.get("over"), "under": t.get("under")})
except Exception:  # noqa: BLE001
    pass

if recs:
    with HIST.open("a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"odds_history: appended {len(recs)} snapshots")
