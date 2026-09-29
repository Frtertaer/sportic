import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst

Q = """CS2 esports betting bot — I need to RAISE the win-rate of placed bets. I backtested on REAL
forward-test data and found signals. Validate: robust +EV or small-sample overfit? Give a terse verdict
+ the exact thresholds to ship.

FINDING 1 — round-totals value bets (the money market), n=44 placed bets, current filter is
edge in [4%,12%]. Winrate/ROI vs a raised MIN edge (clean MONOTONIC gradient):
  edge>=4%: n=37 wr=54.1% ROI=+5.0%
  edge>=6%: n=31 wr=58.1% ROI=+13.1%
  edge>=8%: n=24 wr=66.7% ROI=+30.1%
(edge = model_p*book_odd - 1, i.e. size of the mispricing vs the book's line/odds.)
Edge-weighted staking (stake proportional to edge, cap 2u) beat flat: +6.49u vs +4.06u over the 31 bets.

FINDING 2 — series-winner tracking, n=113. By model confidence:
  model_p 50-52%: n=37 wr=46%  (near-coin-flip; realizes BELOW 50%)
  model_p 52-56%: mixed ~50-59%
  model_p >=56%:  wr 72-83% (small n)
So marginal-edge series (model_p<0.53) lose. Filtering them: series winrate 56%->61%.

FINDING 3 — round-totals by map: Nuke n=21 wr=48% (only losing map); all others 56-60%.

QUESTIONS (be terse, concrete):
1. Is raising totals MIN_EDGE 4%->6% (or 8%) a genuine edge-quality effect or n=44 noise? Which threshold
   maximizes long-run EV without over-thinning volume?
2. Edge-weighted staking — ship it, or fractional-Kelly instead? Give the exact stake formula.
3. Series model_p>=0.53 gate and skip-Nuke — legit or data-mined? Ship or hold?
Answer each in 1-2 lines with the number to use."""

for w in ("claude-code", "grok", "gemini-cli", "gpt-cursor"):
    print("\n" + "=" * 60 + "\n[" + w + "]\n" + "=" * 60)
    t0 = time.time()
    try:
        print(analyst.call_omni(w, Q, max_tokens=700))
    except Exception as e:
        print("FAIL:", type(e).__name__, getattr(e, "code", ""), str(e)[:90])
    print(f"  ({time.time()-t0:.1f}s)")
