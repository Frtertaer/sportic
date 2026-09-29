import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst

Q = """CS2 esports betting bot. New directive: MAXIMISE WIN-RATE via MARKET inefficiencies, NOT match
prediction. Rank strategies by achievable win-rate AND implementability given my DATA, give the exact
detection rule for the top pick. Be concrete & terse.

DATA I HAVE (winline.ru, a RU book):
- pre-match ML odds (moneyline) per match, ONE snapshot, overwritten each fetch (I can start APPENDING
  snapshots to build odds-movement history from now on).
- pre-match round-total lines+odds (line ~21.5, over/under).
- LIVE feed: current-map ROUND SCORE (e.g. 11-4) + current map number + LIVE ML odds, refreshed ~2-3 min.
- bo3.gg match data (maps, scores) + a calibrated round-totals model (+13% ROI at edge>=6%).
- ~500 resolved bet results, tier-2/3 CS2 mostly.

CANDIDATE MARKET STRATEGIES — evaluate each for realistic win-rate + how to detect with my data:
1. STEAM / line-drop following: log odds over time, detect sharp drops (money/insider on one side),
   bet the dropped side. What drop % over what window signals a real steam? expected win-rate?
2. LIVE line-lag: the book's live ML/total is slow to reprice after a map/round swings. Bet the leader
   when the round score makes the outcome near-certain but the live odds still pay. e.g. map at 11-4
   (leader ~95% to close the map) — is the live ML still > fair? This is the "near-certain outcome"
   play — highest win-rate but check profitability.
3. SUSPICIOUS/FIXED-MATCH detection (tier-2/3 CS2 is notoriously fixed): abnormal drops, odds far from
   model, specific teams. Detection signature? win-rate of following the steam side?
4. FAVOURITE-LONGSHOT / short-price favourites: trivially ~80% win-rate at odds ~1.2 but is it +EV?
5. ARBITRAGE / middles: not really win-rate, needs 2+ books.

QUESTIONS:
A. Which ONE gets closest to a HIGH win-rate (70-85%) while staying +EV, and is buildable from my data?
B. Give the exact numeric rule to implement it (thresholds, window, filters).
C. Honestly: is 80% win-rate on +EV bets achievable here, or only on near-certain live outcomes /
   short favourites? What win-rate should I actually target?"""

for w in ("grok", "claude-code", "gpt-cursor", "gemini-cli"):
    print("\n" + "=" * 60 + "\n[" + w + "]\n" + "=" * 60)
    t0 = time.time()
    try:
        print(analyst.call_omni(w, Q, max_tokens=800))
    except Exception as e:
        print("FAIL:", type(e).__name__, getattr(e, "code", ""), str(e)[:90])
    print(f"  ({time.time()-t0:.1f}s)")
