import sys, time
sys.path.insert(0, "/opt/cs2-analyst")
import line_anomaly as la
# test the grok+gemini news layer on a real upcoming pair
t0 = time.time()
print(la._news("INNER CIRCLE ACADEMY", "HONVED"))
print(f"({time.time()-t0:.1f}s)")
