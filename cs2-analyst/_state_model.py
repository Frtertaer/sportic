"""CS2 map-close probability from the live round score — the core of the live line-lag edge.
Map = first to 13 (MR12); 12-12 -> MR3 overtime (first to 16, then 19...). We model remaining
regulation rounds as a race where the leader wins each with prob q. iid is OVERCONFIDENT for CS2
(economy runs cause comebacks), so we (a) keep q=0.5 (neutral from here — conservative vs assuming
the leader stays dominant) and (b) apply a mild comeback haircut calibrated to the tier-2/3 anchor
11-4 -> ~0.94 that all 4 council AIs converged on. Only BET when P>=0.88 (big leads) + edge>=8%."""
from functools import lru_cache

HAIRCUT = 0.90   # shrink excess-over-0.5 (calibrated: 11-4 iid 0.989 -> ~0.94)

@lru_cache(maxsize=4096)
def _race(a: int, b: int, q: float = 0.5) -> float:
    """P(leader at score a reaches 13 before trailer at b), iid rounds, with simple OT handling."""
    if a >= 13:
        return 1.0
    if b >= 13:
        return 0.0
    if a == 12 and b == 12:      # overtime ~ coin flip (slight leader edge folded into q upstream)
        return 0.5
    return q * _race(a + 1, b, q) + (1 - q) * _race(a, b + 1, q)

def map_state_winprob(a: int, b: int) -> float:
    """Leader's prob to close the map from a-b (a=leader). Conservative, haircut-calibrated."""
    lead, trail = max(a, b), min(a, b)
    p = _race(lead, trail)
    return round(0.5 + (p - 0.5) * HAIRCUT, 4)

if __name__ == "__main__":
    anchors = {(11, 4): 0.94, (12, 5): 0.96, (11, 6): 0.88, (10, 5): 0.88,
               (9, 6): 0.80, (8, 7): 0.72, (12, 8): 0.90, (12, 10): 0.75, (11, 9): 0.70,
               (13, 8): 1.0, (7, 5): None, (10, 3): None, (12, 6): None, (11, 5): None}
    print(f"{'score':8} {'model':>6} {'AI-anchor':>10}")
    for (a, b), anc in anchors.items():
        m = map_state_winprob(a, b)
        print(f"  {a:2}-{b:<3}  {m*100:5.1f}%  {('~'+str(int(anc*100))+'%') if anc else '(interp)':>10}")
