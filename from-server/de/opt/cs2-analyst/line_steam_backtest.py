#!/usr/bin/env python3
"""Retroactive backtest: did 'follow the steam' actually WIN? Grades every steam/early signal from the
odds-history vs bo3 finished results — a fast early read before the live track accumulates."""
import sys
sys.path.insert(0, "/opt/cs2-analyst")
import line_anomaly as la
import bo3_client as bo3
import veto_sim as vs
import gcs_lookup


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum()).upper()


sig = [r for r in la.scan() if r["type"] in ("STEAM", "EARLY") and r["side"]]
try:
    fin = bo3._get("/matches", {"filter[matches.status][eq]": "finished",
                                "filter[matches.discipline_id][eq]": 1,
                                "page[limit]": 100, "sort": "-start_date"}).get("results", [])
except Exception as e:  # noqa: BLE001
    print("bo3 fail:", e); fin = []

conn = vs._conn(); names = {}
def nm(tid):
    if tid not in names:
        try:
            names[tid] = vs.team_name(conn, tid)
        except Exception:  # noqa: BLE001
            names[tid] = ""
    return names[tid]

gcs_res = gcs_lookup.load()
graded, pending = [], []
for r in sig:
    b1, b2, side = _norm(r["t1"]), _norm(r["t2"]), _norm(r["side"])
    g = gcs_lookup.find(r["t1"], r["t2"], gcs_res)
    if g:
        won = bool(side and (side in g["winner_n"] or g["winner_n"] in side))
        entry = r["close"][0] if side == _norm(r["t1"]) else r["close"][1]
        graded.append((r, won, entry, f'{g["s1"]}-{g["s2"]}')); continue
    match = None
    for m in fin:
        n1, n2 = _norm(nm(m.get("team1_id"))), _norm(nm(m.get("team2_id")))
        if not (n1 and n2):
            continue
        if (any(x and (x in n1 or n1 in x) for x in (b1, b2)) and
                any(x and (x in n2 or n2 in x) for x in (b1, b2))):
            match = m; break
    if not match:
        pending.append(r); continue
    win = _norm(nm(match.get("winner_team_id")))
    won = bool(win and (side in win or win in side))
    entry = r["close"][0] if side == _norm(r["t1"]) else r["close"][1]
    graded.append((r, won, entry, f"{match.get('team1_score')}-{match.get('team2_score')}"))
conn.close()

n = len(graded); w = sum(1 for _, won, _, _ in graded if won)
pl = sum((e - 1) if won else -1 for _, won, e, _ in graded)
print(f"=== STEAM БЭКТЕСТ: {w}/{n} = {100*w/n:.0f}% winrate | P/L {pl:+.2f}u | ROI {100*pl/n:+.1f}% ===\n"
      if n else "=== нет сыгранных стим-матчей для оценки ===\n")
for r, won, e, sc in graded:
    print(f"  {'✅' if won else '❌'} {r['t1']} vs {r['t2']} → ставил {r['side']} @ {e} ({r['type']}) | счёт {sc}")
print(f"\n⏳ ещё не сыграны ({len(pending)}):")
for r in pending[:12]:
    print(f"   {r['t1']} vs {r['t2']} → {r['side']} ({r['type']})")
