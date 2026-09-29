#!/usr/bin/env python3
"""Read livelag_measure.jsonl and print the verdict: is the live-lag real money or a mirage?
The key number = how many seconds the fat (exploitable) price survives. A human needs minutes;
a warm-page sniper needs the price to live ~8s+. If most opportunities collapse in <3s or the odds
crater (first_odd -> min_odd), the edge is not bettable in practice."""
import json, statistics as st
from pathlib import Path

f = Path(__file__).resolve().parent / "data" / "livelag_measure.jsonl"
rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []
if not rows:
    print("no measurements yet — waiting for near-lock live states to occur"); raise SystemExit

secs = [r.get("fat_secs", 0) for r in rows]
placeable = [r for r in rows if r.get("fat_secs", 0) >= 8]     # survived long enough for a sniper
collapse = [r for r in rows if r.get("min_odd", 9) < r.get("first_odd", 0) * 0.6]  # odds cratered
print(f"opportunities observed: {len(rows)}")
print(f"fat-price lifetime  : median {st.median(secs):.1f}s  mean {st.mean(secs):.1f}s  max {max(secs):.1f}s")
print(f"survived >=8s (sniper-placeable): {len(placeable)}/{len(rows)} ({100*len(placeable)/len(rows):.0f}%)")
print(f"odds collapsed >40%  : {len(collapse)}/{len(rows)} ({100*len(collapse)/len(rows):.0f}%)  <- these were mirages")
print()
for r in sorted(rows, key=lambda x: -x.get("fat_secs", 0))[:12]:
    print(f"  {r['fat_secs']:5.1f}s  {r['leader']:>14} @ {r['first_odd']}→{r['min_odd']:<5} "
          f"P{r['p_series']*100:.0f}%  {r['match']}")
print("\nVERDICT: if 'survived>=8s' is high and 'collapsed' is low -> real, worth a login sniper.")
print("         if most collapse in <3s -> the edge is a display lag you can't actually bet.")
