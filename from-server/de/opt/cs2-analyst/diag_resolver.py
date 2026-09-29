import sys
sys.path.insert(0, "/opt/cs2-analyst")
import bo3_client as bo3, veto_sim as vs

def _norm(s): return "".join(c for c in (s or "").upper() if c.isalnum())

# how many finished CS2 matches does bo3 return, and how far back?
for lim in (100, 250):
    try:
        r = bo3._get("/matches", {"filter[matches.status][eq]": "finished",
                                  "filter[matches.discipline_id][eq]": 1,
                                  "page[limit]": lim, "sort": "-start_date"})
        fin = r.get("results", r) or []
    except Exception as e:
        print("fail", e); fin = []
    conn = vs._conn(); names = {}
    def nm(t):
        if t not in names:
            try: names[t] = vs.team_name(conn, t)
            except: names[t] = ""
        return names[t]
    allnames = set()
    oldest = None
    for m in fin:
        allnames.add(_norm(nm(m.get("team1_id")))); allnames.add(_norm(nm(m.get("team2_id"))))
        oldest = m.get("start_date")
    conn.close()
    targets = ["JUMBO", "ALAHLI", "PRESTIGE", "VASTERAS", "PRESTIGEACADEMY"]
    found = {t: any(t in n or n in t for n in allnames if n) for t in targets}
    print(f"limit={lim}: {len(fin)} matches, oldest start={oldest}")
    print(f"   targets in bo3: {found}")
