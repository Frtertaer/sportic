"""CS2 map-close probability from the live round score — core of the live line-lag edge.
Map = first to 13 (MR12); 12-12 -> overtime (~coin flip). We model remaining regulation rounds as
a race where the leader wins each with prob q=0.5 (neutral-from-here: conservative vs assuming the
leader stays dominant), then apply a comeback haircut calibrated to the tier-2/3 anchor 11-4 -> ~0.94
that all 4 council AIs converged on. Only ACT on big leads (P>=0.88) + a real edge (>=8%)."""
from functools import lru_cache

HAIRCUT = 0.90

@lru_cache(maxsize=8192)
def _race(a: int, b: int, q: float = 0.5) -> float:
    if a >= 13:
        return 1.0
    if b >= 13:
        return 0.0
    if a == 12 and b == 12:
        return 0.5
    return q * _race(a + 1, b, q) + (1 - q) * _race(a, b + 1, q)

def map_state_winprob(a: int, b: int) -> float:
    """Leader's prob to close the map from round score a-b (leader = max). Haircut-calibrated."""
    lead, trail = max(a, b), min(a, b)
    if lead >= 13:              # map already won — no haircut
        return 1.0
    p = _race(lead, trail)
    return round(0.5 + (p - 0.5) * HAIRCUT, 4)

def series_winprob(sa: int, sb: int, p_map_leader: float, need: int, fut: float = 0.5) -> float:
    """Prob the CURRENT-MAP leader wins the SERIES. sa/sb = series maps won (leader/other),
    p_map_leader = prob the leader closes the in-progress map, need = maps to win the series,
    fut = leader's win prob on any not-yet-started map (0.5 = neutral, the safe default)."""
    @lru_cache(maxsize=None)
    def f(a: int, b: int) -> float:
        if a >= need:
            return 1.0
        if b >= need:
            return 0.0
        return fut * f(a + 1, b) + (1 - fut) * f(a, b + 1)
    return p_map_leader * f(sa + 1, sb) + (1 - p_map_leader) * f(sa, sb + 1)
