#!/usr/bin/env python3
"""Dedup live-lag at the SOURCE (the log), so the resolver can't regenerate the phantom 17.
Keep the earliest fire per unique opportunity (teams + series state + leader)."""
import json, pathlib
log = pathlib.Path("/opt/cs2-analyst/data/livelag_ft_log.jsonl")
res = pathlib.Path("/opt/cs2-analyst/data/livelag_ft_results.jsonl")
rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
seen, uniq = set(), []
for r in sorted(rows, key=lambda r: r.get("logged_at", "")):
    k = (r.get("t1", "").lower(), r.get("t2", "").lower(), tuple(r.get("series") or []), r.get("leader"))
    if k in seen:
        continue
    seen.add(k); uniq.append(r)
log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in uniq) + "\n", encoding="utf-8")
res.write_text("", encoding="utf-8")   # clear -> resolver regenerates from the deduped log
print(f"log deduped {len(rows)} -> {len(uniq)} unique opportunities; results cleared for re-resolve")
