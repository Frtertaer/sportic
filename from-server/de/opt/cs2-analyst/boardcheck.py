import json, urllib.request
b = json.load(open("/opt/cs2-analyst/data/prematch_board.json"))
print("board matches:", len(b.get("matches", [])))
for r in b["matches"]:
    print(f"  {r['t1']} vs {r['t2']} | fav {r['fav']} модель {round(r['pm_fav']*100)}% | verdict {r['verdict']} "
          f"bet {r.get('bet_side')} | line={r.get('line')}")
h = urllib.request.urlopen("http://127.0.0.1:8920/", timeout=10).read().decode()
print("\nрынок подтверждает present:", "рынок подтверждает" in h)
print("рынок против present     :", "рынок против" in h)
print("traceback:", "Traceback" in h)
# also show what movement_map sees right now (are there any live directional moves to confirm against?)
import sys; sys.path.insert(0, "/opt/cs2-analyst")
import line_anomaly as la
mm = la.movement_map()
print(f"\nmovement_map: {len(mm)} directional moves on the board right now")
for k, v in list(mm.items())[:8]:
    print("  ", v["side"], v["type"], round(v["strength"], 2))
