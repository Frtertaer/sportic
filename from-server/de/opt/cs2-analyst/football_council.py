import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst

Q = """Football betting model, hit-rate measured on 30 resolved matches (small sample):
- Double Chance (back the 2 most-likely of 1X2) = 83% win
- Draw-No-Bet (back stronger team, push on draw) = 64%
- BTTS (both teams score) = 57%
- 1X2 straight (argmax) = 53%
- Over/Under 2.5 goals = 43% (WORST)
The bot currently HEADLINES Over 2.5 (its worst market). I want to switch to the best market to lift
real profit, not just win-rate. Answer TERSE and CONCRETE (numbered):
1. Double Chance wins 83% but odds are low (~1.15-1.45). Is it genuinely +EV or is high win-rate a
   trap here? Give the exact break-even: at what MIN ODDS does an 83% DC bet stop being profitable,
   and what odds range should I actually take?
2. Which ONE market should the bot headline for the best real ROI: Double Chance, filtered high-confidence
   1X2, or DNB? Why?
3. Concrete selection rule for the chosen market: which side, min-odds floor, when to SKIP.
4. n=30 is tiny. How many resolved bets before I trust this ranking? Any red flags in these numbers?"""

for w in ("grok", "claude-code", "gpt-cursor", "gemini-cli"):
    print("\n" + "=" * 60 + "\n[" + w + "]\n" + "=" * 60)
    t0 = time.time()
    try:
        print(analyst.call_omni(w, Q, max_tokens=650))
    except Exception as e:
        print("FAIL:", type(e).__name__, str(e)[:80])
    print(f"  ({time.time()-t0:.1f}s)")
