#!/usr/bin/env python3
"""VALUE ENGINE — the core of the "beat the bookmaker" system (council-unanimous #1 edge).
Pinnacle = the market's truth. De-vig its odds to a FAIR price, then a soft book (winline) price that
BEATS the fair price is +EV. CLV (bet price vs Pinnacle close) is the only honest proof of long-run edge.

Pure-stdlib. Functions:
  devig(odds, method) -> fair probabilities (shin | power | proportional)
  fair_odds(odds, method) -> fair decimal odds per outcome
  value(soft_odd, fair_prob) -> {edge, kelly, stake_pct}   (edge = soft_odd*fair_prob - 1)
  clv(bet_odd, close_odd) -> closing-line value %
De-vig default = SHIN (models the insider-trader proportion z; best on fav/longshot bias)."""
from __future__ import annotations
import math

KELLY_FRACTION = 0.25      # quarter-Kelly (council: full Kelly assumes perfect probs; you don't have them)
MAX_STAKE_PCT = 0.015      # hard cap 1.5% bankroll / bet
MIN_EDGE_PREMATCH = 0.03   # 3% pre-match
MIN_EDGE_LIVE = 0.06       # 6% live


def _implied(odds: list[float]) -> list[float]:
    return [1.0 / o for o in odds]


def devig_proportional(odds: list[float]) -> list[float]:
    q = _implied(odds); s = sum(q)
    return [x / s for x in q]


def devig_power(odds: list[float]) -> list[float]:
    """Power method: p_i = q_i**k, solve k so sum(p)=1. Corrects fav/longshot bias better than proportional."""
    q = _implied(odds)
    lo, hi = 0.5, 2.0
    for _ in range(60):
        k = (lo + hi) / 2
        s = sum(x ** k for x in q)
        if s > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    p = [x ** k for x in q]
    t = sum(p)
    return [x / t for x in p]


def devig_shin(odds: list[float]) -> list[float]:
    """Shin's method — models the fraction z of insider money. Empirically closest to true probs.
    Solve z in [0,0.25] s.t. sum p_i = 1, where p_i = (sqrt(z^2 + 4(1-z) q_i^2 / B) - z) / (2(1-z))."""
    q = _implied(odds); B = sum(q)
    if B <= 1.0:
        return devig_proportional(odds)

    def psum(z):
        if z >= 1:
            return 0.0
        tot = 0.0
        for qi in q:
            tot += (math.sqrt(z * z + 4 * (1 - z) * qi * qi / B) - z) / (2 * (1 - z))
        return tot
    lo, hi = 0.0, 0.35
    if psum(lo) < 1:  # no insider component -> proportional
        return devig_proportional(odds)
    for _ in range(80):
        z = (lo + hi) / 2
        if psum(z) > 1:
            lo = z
        else:
            hi = z
    z = (lo + hi) / 2
    p = [(math.sqrt(z * z + 4 * (1 - z) * qi * qi / B) - z) / (2 * (1 - z)) for qi in q]
    t = sum(p)
    return [x / t for x in p]


def devig(odds: list[float], method: str = "shin") -> list[float]:
    return {"shin": devig_shin, "power": devig_power, "proportional": devig_proportional}.get(
        method, devig_shin)(odds)


def fair_odds(odds: list[float], method: str = "shin") -> list[float]:
    return [round(1.0 / p, 4) if p > 1e-9 else None for p in devig(odds, method)]


def kelly(soft_odd: float, fair_prob: float) -> float:
    b = soft_odd - 1.0
    if b <= 0:
        return 0.0
    f = (fair_prob * soft_odd - 1.0) / b
    return max(0.0, f)


def value(soft_odd: float, fair_prob: float, live: bool = False) -> dict:
    edge = soft_odd * fair_prob - 1.0
    f = kelly(soft_odd, fair_prob)
    stake = min(MAX_STAKE_PCT, KELLY_FRACTION * f)
    thr = MIN_EDGE_LIVE if live else MIN_EDGE_PREMATCH
    return {"edge": round(edge, 4), "fair_prob": round(fair_prob, 4),
            "fair_odd": round(1 / fair_prob, 3) if fair_prob > 1e-9 else None,
            "kelly": round(f, 4), "stake_pct": round(stake * 100, 3),
            "bet": edge >= thr and stake > 0}


def clv(bet_odd: float, close_odd: float, method: str = "shin",
        close_others: list[float] | None = None) -> float | None:
    """CLV% = your entry fair-prob-adjusted vs Pinnacle CLOSING. If close_others given (the other outcomes'
    Pinnacle close), de-vig the close for a true CLV; else raw odds ratio."""
    if not (bet_odd and close_odd):
        return None
    if close_others:
        allo = [close_odd] + list(close_others)
        p_close = devig(allo, method)[0]
        p_bet = 1.0 / bet_odd
        return round((p_close / p_bet - 1.0) * 100, 2) if p_bet else None
    return round((bet_odd / close_odd - 1.0) * 100, 2)


if __name__ == "__main__":
    print("=== de-vig comparison (Pinnacle 1X2  1.90 / 3.60 / 4.20) ===")
    line = [1.90, 3.60, 4.20]
    print("  raw implied sum (vig):", round(sum(_implied(line)) - 1, 4))
    for m in ("proportional", "power", "shin"):
        p = devig(line, m)
        print(f"  {m:12}: probs {[round(x, 3) for x in p]}  fair {[round(1 / x, 2) for x in p]}")
    print("\n=== value: winline offers 2.15 on the fav (Pinnacle fair via shin) ===")
    fp = devig_shin(line)[0]
    print("  ", value(2.15, fp))
    print("=== value: winline 1.95 on fav (below fair) ===")
    print("  ", value(1.95, fp))
    print("\n=== CLV: bet 2.15, Pinnacle closed 1.98 (fav) w/ others 3.7/4.4 ===")
    print("  CLV%:", clv(2.15, 1.98, close_others=[3.70, 4.40]))
