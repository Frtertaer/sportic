import json, os, subprocess
from collections import defaultdict
from datetime import datetime
root = "/opt/cs2-analyst"
f = f"{root}/data/odds_history.jsonl"
rows = [json.loads(l) for l in open(f) if l.strip()]
print("history records:", len(rows))
print("keys:", list(rows[0].keys()))
print("sample record:", json.dumps(rows[0], ensure_ascii=False)[:200])
# time span
ts = [r.get("fetched_at") or r.get("ts") or r.get("t") for r in rows if (r.get("fetched_at") or r.get("ts") or r.get("t"))]
if ts:
    print("time span:", min(ts), "->", max(ts))
# distinct matches + how many snapshots each (movement depth)
key = lambda r: f"{r.get('t1')}|{r.get('t2')}"
snaps = defaultdict(list)
for r in rows:
    snaps[key(r)].append(r)
multi = {k: v for k, v in snaps.items() if len(v) >= 3}
print(f"\ndistinct matches: {len(snaps)} | with >=3 snapshots (movement trackable): {len(multi)}")
# show a match with the biggest odds swing
def odd1(r): return r.get("odd1") or r.get("ml1")
best = None
for k, v in snaps.items():
    o = [odd1(x) for x in v if odd1(x)]
    if len(o) >= 3:
        swing = (max(o) - min(o)) / min(o) if min(o) else 0
        if best is None or swing > best[1]:
            best = (k, swing, o)
if best:
    print(f"\nbiggest odd1 swing: {best[0]}  {best[1]*100:.0f}%  trajectory {[round(x,2) for x in best[2]][:12]}")
print("\n=== who reads odds_history? ===")
print(subprocess.run(["grep", "-rl", "odds_history", root], capture_output=True, text=True).stdout or "  (nobody reads it — pure capture, unused for detection)")
