import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst

Q = """You are a professional betting-syndicate quant. Design the SINGLE highest-EV, most-scalable,
lowest-variance SYSTEM to systematically beat bookmakers long-term ("destroy the book"). Be brutally
honest (no fantasy), rank the proven edges, and give the concrete architecture to BUILD given my ASSETS.

MY ASSETS (already built + running):
- CS2 esports bot with a PROVEN LIVE line-lag edge: bets near-certain live outcomes (map 11-4) on stale
  winline LIVE odds -> ~100% win-rate / +89% ROI on a small sample. Also a calibrated round-totals model
  (+13% ROI) and a pre-match model-vs-line anomaly scanner.
- Football bot: Elo+Poisson/Dixon-Coles+LightGBM+3-agent-AI, calibrated on 7800 matches; math ~breakeven
  vs market; value computed vs PINNACLE (sharp book).
- Data feeds: the-odds-api (Pinnacle + ~24-book consensus, MANY sports), winline.ru (soft RU book I bet on)
  scraped pre-match + LIVE, grok live-search (free, cookie), a free 4-AI council, ~500 CS2 results.
- I bet on winline (soft RU book). Account limits are a real constraint (books limit/ban winners).

ANSWER (numbered, terse, concrete):
1. RANK the genuinely +EV edges by (scalability x edge-size x how-hard-to-get-limited): sharp-vs-soft
   value/CLV, live latency-arbitrage, pure arbitrage/middles, steam-following, info (live-search). Which
   ONE is the core engine?
2. The CORE: Pinnacle vig-free fair price -> bet where winline's price beats it. Give the exact method:
   how to de-vig Pinnacle (which method), the min-CLV/edge threshold to bet, staking (Kelly fraction),
   and expected realistic ROI% + why CLV is the only honest proof of long-run +EV.
3. ARCHITECTURE to build on my stack: one multi-sport scanner (odds-api Pinnacle fair vs winline price) +
   the live-lag latency layer + closing-line capture for CLV tracking. What are the components + data flow?
4. Account-limit survival: concrete tactics (stake rounding, bet timing, market/sport spread) so I don't
   get limited in month 1.
5. Honest ceiling: realistic long-run ROI% and monthly variance for a disciplined solo operator on ONE
   soft book + this stack. What kills most people trying this?"""

for w in ("grok", "claude-code", "gpt-cursor", "gemini-cli"):
    print("\n" + "=" * 62 + "\n[" + w + "]\n" + "=" * 62)
    t0 = time.time()
    try:
        print(analyst.call_omni(w, Q, max_tokens=950))
    except Exception as ex:
        print("FAIL:", type(ex).__name__, getattr(ex, "code", ""), str(ex)[:90])
    print(f"  ({time.time()-t0:.1f}s)")
