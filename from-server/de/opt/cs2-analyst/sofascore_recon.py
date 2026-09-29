import json, urllib.request
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json", "Referer": "https://www.sofascore.com/"})
    return json.load(urllib.request.urlopen(req, timeout=25))


try:
    live = get("https://api.sofascore.com/api/v1/sport/football/events/live")
    ev = live.get("events", [])
    print(f"LIVE футбол-матчей сейчас: {len(ev)}")
    inplay = [e for e in ev if e.get("status", {}).get("type") == "inprogress"]
    print(f"из них in-progress: {len(inplay)}")
    for e in inplay[:5]:
        t = e.get("time", {})
        print(f"  {e['homeTeam']['name']} {e.get('homeScore',{}).get('current','?')}:"
              f"{e.get('awayScore',{}).get('current','?')} {e['awayTeam']['name']} "
              f"| id {e['id']} | {e.get('tournament',{}).get('name','?')}")
    target = inplay[0] if inplay else (ev[0] if ev else None)
    if target:
        eid = target["id"]
        print(f"\n=== статистика матча id {eid} ({target['homeTeam']['name']} vs {target['awayTeam']['name']}) ===")
        stats = get(f"https://api.sofascore.com/api/v1/event/{eid}/statistics")
        for period in stats.get("statistics", []):
            if period.get("period") != "ALL":
                continue
            for g in period.get("groups", []):
                for it in g.get("statisticsItems", []):
                    print(f"  {it['name']}: {it.get('home')} - {it.get('away')}")
except Exception as ex:  # noqa: BLE001
    import traceback
    print("FAIL:", type(ex).__name__, str(ex)[:200])
