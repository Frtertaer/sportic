import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst

Q = """CS2 esports betting — build a PRE-MATCH (hours before start) SUSPICIOUS/FIXED-MATCH + anomaly
scanner. Tier-2/3 CS2 is heavily match-fixed; I want to detect it from odds + news BEFORE the match.
Terse, concrete, numeric rules.

MY DATA per upcoming match:
- winline.ru pre-match ML (moneyline / series-winner) odds ml1, ml2. (I do NOT currently scrape map
  handicap / "win a map" markets — only ML. Can extend the scraper to open the match page if worth it.)
- My model (per-map Glicko -> veto Monte-Carlo): P(series winner), P(each map winner), P(map round totals).
- bo3.gg: tier, bo_type, start time, team ids.
- I just started logging ML snapshots over time (odds_history.jsonl) -> can detect movement soon.
- grok live-search (news): I can query "roster/stand-in/injury/motivation/visa/DNP" for a team right now.

ANSWER THESE (numbered, terse):
1. SUSPICIOUS/FIXED signature from PRE-MATCH odds I can compute NOW (ML + model divergence, no history
   yet): give exact rules. e.g. book implied prob vs my model prob gap > X? extreme short price < Y with
   a mismatched model? which direction to bet (fade or follow)? expected hit-rate?
2. The user's idea: "a team is near-CERTAIN to win >=1 of the (>=2) maps in a Bo3" (anti-sweep / map
   handicap +1.5). Is this a real +EV high-win-rate market? At what model per-map prob p does
   P(win>=1 map)=1-(1-p)^2 clear a useful bar (e.g. >=0.90)? Is it worth extending the scraper to grab
   the winline map-handicap line, or is the ML-derived signal enough? expected win-rate?
3. NEWS anomalies (injury / stand-in / a team not trying / dead rubber / already-qualified): exactly what
   to query via grok live-search per match, and what red flag => SKIP the match vs BET a side.
4. Once I have odds MOVEMENT history: what drop pattern = a real steam/fix (numeric: % move, window)?
5. Honest: what pre-match win-rate is realistically achievable with fix/anomaly detection on ONE RU book,
   and which of the above single signal is highest-value to build FIRST?"""

for w in ("grok", "claude-code", "gemini-cli", "gpt-cursor"):
    print("\n" + "=" * 60 + "\n[" + w + "]\n" + "=" * 60)
    t0 = time.time()
    try:
        print(analyst.call_omni(w, Q, max_tokens=850))
    except Exception as e:
        print("FAIL:", type(e).__name__, getattr(e, "code", ""), str(e)[:90])
    print(f"  ({time.time()-t0:.1f}s)")
