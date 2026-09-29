#!/usr/bin/env python3
"""Patch live_edge.py: fix double-counting (dedup per series opportunity, not per round score)
and tighten the stale-scrape window so we never log a ghost edge on an old quote."""
import re, pathlib
p = pathlib.Path("/opt/cs2-analyst/live_edge.py")
s = p.read_text(encoding="utf-8")
orig = s

# 1) Freshness: 200s is long enough for a whole map to finish -> the "edge" would be a ghost.
s = s.replace("MAX_STALE_S = 200", "MAX_STALE_S = 100")

# 2) Dedup key: was per (map, round-score) so 12-6 and 12-8 fired the SAME series bet twice.
#    Key on the series opportunity (teams + series state + leader) so it fires ONCE.
s = s.replace(
    'key = f"{_norm(t1)}|{_norm(t2)}|m{cur_map}|{max(r1,r2)}-{min(r1,r2)}"',
    'key = f"{_norm(t1)}|{_norm(t2)}|s{sa}-{sb}|{_norm(lead)}"')

if s == orig:
    print("!! no change — markers not found"); raise SystemExit(1)
p.write_text(s, encoding="utf-8")
print("patched live_edge.py: MAX_STALE_S=100, dedup per series opportunity")

# de-duplicate the ALREADY-logged track so the win-rate isn't inflated by progressive re-fires
import json
from datetime import datetime
res = pathlib.Path("/opt/cs2-analyst/data/livelag_ft_results.jsonl")
if res.exists():
    rows = [json.loads(l) for l in res.read_text(encoding="utf-8").splitlines() if l.strip()]
    seen, dedup = set(), []
    for r in rows:
        k = (r.get("t1","").lower(), r.get("t2","").lower(), tuple(r.get("series") or []), r.get("leader"))
        if k in seen:
            continue
        seen.add(k); dedup.append(r)
    n_before, n_after = len(rows), len(dedup)
    wins = sum(1 for r in dedup if r.get("outcome") == "win")
    pl = sum(r.get("pl_units", 0) for r in dedup)
    res.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in dedup) + "\n", encoding="utf-8")
    print(f"deduped track: {n_before} -> {n_after} unique bets | wins {wins}/{n_after} "
          f"({100*wins/n_after:.0f}%) | P/L {pl:+.2f}u | ROI {100*pl/n_after:+.1f}%")
