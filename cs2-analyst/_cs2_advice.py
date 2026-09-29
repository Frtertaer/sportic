import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import analyst

Q = """You are a senior quant advising a CS2 (Counter-Strike 2) esports betting bot. Give ONE concrete,
implementable improvement to raise win-rate / ROI. Be specific and terse.

MODEL: per-map Glicko2 ratings -> a veto Monte-Carlo sim produces P(map winner) and P(series winner);
round-totals use an empirical (map-specific) distribution. Bets are logged only when model edge vs the
bookmaker (Winline) exceeds a threshold. 499 resolved bets so far.

CALIBRATION (predicted prob vs realized win-rate):
- SERIES winner: n=113, realized 55.8%, model-predicted avg 56.3% -> WELL CALIBRATED (gap -0.5pp), Brier .239.
- MAP winner:    n=197, realized 51.3%, model-predicted avg 61.9% -> OVERCONFIDENT by +10.6pp, Brier .271.
    calibration curve: pred 50-60% -> real 48% | pred 60-70% -> real 61% (fine) |
    pred 70-80% -> real 43% | pred 80-90% -> real 57%.  So high-confidence map favorites LOSE.
- ROUND TOTALS: n=189, realized 56.6%, predicted 56.0% -> WELL CALIBRATED (+0.7pp), Brier .246.
    100% of total bets are the UNDER (ТМ) side. Forward-test ROI = +3.2% over 43 bets.

QUESTION: Given the map-winner model is badly overconfident on favorites (70%+ picks realize only ~43-57%)
while series + round-totals are calibrated and totals are +EV, what is the SINGLE highest-value change to
improve overall win-rate/ROI? Options to weigh: (a) recalibrate map probs (Platt/isotonic/shrink-to-0.5,
esp. above 65%); (b) stop betting high-confidence map picks entirely; (c) redirect stake to the +EV
round-totals niche and widen its coverage; (d) something else. Pick ONE, justify in 2 lines, and give the
concrete formula/rule to implement (numbers, thresholds)."""

for w in ("claude-code", "grok", "gpt-cursor", "gemini-cli"):
    print("\n" + "=" * 62 + "\n[" + w + "]\n" + "=" * 62)
    t0 = time.time()
    try:
        print(analyst.call_omni(w, Q, max_tokens=650))
    except Exception as e:
        print("FAIL:", type(e).__name__, getattr(e, "code", ""), str(e)[:100])
    print(f"  ({time.time()-t0:.1f}s)")
