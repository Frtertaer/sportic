#!/usr/bin/env python3
"""Grade ALL pending signals right now by fetching their SofaScore FINAL score — reveals the REAL
recent win-rate the stuck resolver isn't showing."""
import asyncio, json
from pathlib import Path
import sofa_resolve as SR

R = Path("/opt/cs2-analyst/data")
def rd(f): p=R/f; return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []

log = rd("sofa_signal_log.jsonl"); res = rd("sofa_signal_results.jsonl")
done = {f"{r['id']}|{r.get('logged_at')}" for r in res}
bad = SR._glitchy()
pending = [s for s in log if f"{s['id']}|{s.get('logged_at')}" not in done and s["id"] not in bad]
print(f"pending к грейду: {len(pending)}, матчей: {len(set(s['id'] for s in pending))}")

finals = asyncio.run(SR._finals(list({s["id"] for s in pending})))
print(f"получил финалы для {len(finals)} матчей\n")

w=l=nf=0
for s in sorted(pending, key=lambda x: x.get("logged_at","")):
    fin = finals.get(s["id"])
    tgt = s.get("tot_goals",0)+s.get("need_goals",1)
    if fin is None:
        nf+=1; out="?нет финала (ещё идёт?)"
    else:
        win = fin>=tgt; out = ("WIN " if win else "LOSS") + f" (финал {fin}, нужно ≥{tgt})"
        w += win; l += (not win)
    print(f"  {s.get('logged_at','')[5:16]} {s.get('home','')[:12]:12} v {s.get('away','')[:12]:12} {s.get('minute')}' {s.get('market','')} -> {out}")

print(f"\nРЕАЛЬНО из pending: {w}W {l}L  (+{nf} без финала)")
if w+l: print(f"винрейт по этим: {100*w/(w+l):.0f}%")
