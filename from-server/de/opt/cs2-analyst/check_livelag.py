import json, os, time, subprocess
D = "/opt/cs2-analyst/data"

# which mode? grep the startup line from journal
j = subprocess.run(["journalctl", "-u", "cs2-livelag-bot", "-n", "40", "--no-pager"],
                   capture_output=True, text=True).stdout
lines = [l for l in j.splitlines() if "livelag" in l.lower()]
print("=== recent daemon log ===")
for l in lines[-12:]:
    print("  ", l.split("]: ")[-1][:130] if "]: " in l else l[-130:])
mode = "DRY-RUN sniper (auth)" if any("sniper up" in l for l in lines) else \
       ("measure (no auth)" if any("measuring" in l for l in lines) else "unknown")
print("mode:", mode)

print("\n=== did it FIRE (WOULD-BET / placed)? ===")
f = f"{D}/livelag_bets_placed.jsonl"
if os.path.exists(f):
    rows = [json.loads(l) for l in open(f) if l.strip()]
    print(f"bets logged: {len(rows)}")
    for r in rows[-6:]:
        tag = "PLACED" if r.get("placed") else ("WOULD-BET(dry)" if r.get("dry_run") else "skip")
        print(f"  {tag} {r.get('lead')} @ {r.get('lead_odd')} P{round((r.get('p_series') or 0)*100)}% {r.get('stake_rub')}₽ — {r.get('reason')}")
else:
    print("  никогда не стрелял (нет near-lock состояний за это время)")

print("\n=== auth present? (determines mode) ===")
print("  winline_auth.json:", "YES" if os.path.exists(f"{D}/winline_auth.json") else "NO")

print("\n=== live now ===")
lf = f"{D}/winline_live.json"
if os.path.exists(lf):
    d = json.load(open(lf)); age = round(time.time() - os.path.getmtime(lf))
    print(f"  live matches: {len(d)} (feed age {age}s)")
    for r in d[:6]:
        ms = r.get("map_score")
        lead = (max(ms) - min(ms)) if ms and len(ms) >= 2 else "?"
        print(f"    {r['t1']} vs {r['t2']} серия{r.get('series')} карта{r.get('cur_map')} {ms} разрыв {lead}")
else:
    print("  no live feed")
