#!/usr/bin/env python3
"""Measure the REAL hit-rate of every football market on resolved predictions, to find the one
worth betting. Reads /opt/sportsbot/sportsbot.db read-only. Prints per-market win-rate + n, and
win-rate at confidence tiers (a market is only useful if it stays profitable at a bet threshold)."""
import sqlite3

DB = "/opt/sportsbot/sportsbot.db"
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute(
    "SELECT prob_w1,prob_draw,prob_w2,prob_over_2_5,prob_btts_yes,top_confidence_pct,"
    "actual_result,actual_goals_home,actual_goals_away FROM predictions")]
c.close()

res = [r for r in rows if r.get("actual_result") in ("W1", "DRAW", "W2")
       and r.get("actual_goals_home") is not None and r.get("actual_goals_away") is not None]
print(f"resolved matches: {len(res)} (of {len(rows)} total)\n")

# normalise prob scale (some cols 0-1, some 0-100)
def sc(v):
    v = v or 0
    return v * 100 if v <= 1.5 else v

def rate(hits):
    hits = [h for h in hits if h is not None]
    n = len(hits); w = sum(1 for h in hits if h)
    return n, w, (100 * w / n if n else 0)

def show(name, hits):
    n, w, pct = rate(hits)
    if n:
        print(f"  {name:34} {pct:5.1f}%  ({w}/{n})")

markets = {}
for r in res:
    w1, dr, w2 = sc(r["prob_w1"]), sc(r["prob_draw"]), sc(r["prob_w2"])
    tot = (r["actual_goals_home"] or 0) + (r["actual_goals_away"] or 0)
    btts_actual = (r["actual_goals_home"] or 0) >= 1 and (r["actual_goals_away"] or 0) >= 1
    ar = r["actual_result"]
    po = sc(r["prob_over_2_5"]); pb = sc(r["prob_btts_yes"])

    # 1X2 straight (argmax)
    pick = "W1" if w1 >= dr and w1 >= w2 else ("DRAW" if dr >= w2 else "W2")
    markets.setdefault("1X2 (макс. вероятность)", []).append(pick == ar)
    # Double chance = the two most likely outcomes
    order = sorted([("W1", w1), ("DRAW", dr), ("W2", w2)], key=lambda x: -x[1])
    dc = {order[0][0], order[1][0]}
    label = "1X" if dc == {"W1", "DRAW"} else ("12" if dc == {"W1", "W2"} else "X2")
    markets.setdefault("Двойной шанс (2 топ-исхода)", []).append(ar in dc)
    # Draw-no-bet: back the stronger of W1/W2, push (skip) on draw
    dnb_pick = "W1" if w1 >= w2 else "W2"
    if ar != "DRAW":
        markets.setdefault("DNB (фаворит, ничья=возврат)", []).append(dnb_pick == ar)
    # Over/Under 2.5
    over_pick = po >= 50
    markets.setdefault("Тотал 2.5 (О/У по вероятности)", []).append(over_pick == (tot >= 3))
    # BTTS
    btts_pick = pb >= 50
    markets.setdefault("Обе забьют (BTTS)", []).append(btts_pick == btts_actual)
    # Favourite straight win (higher of W1/W2)
    fav_pick = "W1" if w1 >= w2 else "W2"
    markets.setdefault("Победа фаворита (П1/П2)", []).append(fav_pick == ar)

print("=== ВИНРЕЙТ ПО МАРКЕТАМ (все сыгранные) ===")
for name, hits in sorted(markets.items(), key=lambda kv: -rate(kv[1])[2]):
    show(name, hits)

# confidence tiers for the two headline markets
print("\n=== 1X2 по порогу уверенности топ-исхода ===")
for thr in (50, 55, 60, 65, 70):
    hits = []
    for r in res:
        w1, dr, w2 = sc(r["prob_w1"]), sc(r["prob_draw"]), sc(r["prob_w2"])
        best = max(w1, dr, w2)
        if best < thr:
            continue
        pick = "W1" if w1 == best else ("DRAW" if dr == best else "W2")
        hits.append(pick == r["actual_result"])
    n, w, pct = rate(hits)
    if n:
        print(f"  топ-исход >= {thr}% :  {pct:5.1f}%  ({w}/{n})")

print("\n=== Двойной шанс по порогу (сумма 2 топ-исходов) ===")
for thr in (65, 70, 75, 80, 85):
    hits = []
    for r in res:
        w1, dr, w2 = sc(r["prob_w1"]), sc(r["prob_draw"]), sc(r["prob_w2"])
        order = sorted([("W1", w1), ("DRAW", dr), ("W2", w2)], key=lambda x: -x[1])
        s2 = order[0][1] + order[1][1]
        if s2 < thr:
            continue
        dc = {order[0][0], order[1][0]}
        hits.append(r["actual_result"] in dc)
    n, w, pct = rate(hits)
    if n:
        print(f"  сумма 2 исходов >= {thr}% :  {pct:5.1f}%  ({w}/{n})")
