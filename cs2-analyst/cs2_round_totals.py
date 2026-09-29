#!/usr/bin/env python3
"""CS2 map round-totals model (O/U map rounds) on top of map_rounds + per-map Glicko.

Total rounds on a map is driven mostly by CLOSENESS: in MR12 (first to 13), total
≈ 26 - margin, so an even matchup (small margin) runs long, a blowout ends short.
We anchor to each map's empirical mean/sd (from map_rounds) and shift the mean by the
matchup's closeness, derived from the per-map Glicko win probability. Gives O/U probs
for any line — the input for the round-totals betting market.

  python cs2_round_totals.py stats              per-map empirical round stats
  python cs2_round_totals.py prob <map> <p> <L> P(over L) for win-prob p on a map
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent / "cs2.db"

# CALIBRATED ON DATA (2026-06-27): P(over line) = the EMPIRICAL fraction of historical
# games on that map with total > line (from map_rounds, ~9.5k games). The old Normal+SWING
# model over-predicted totals by +3 rounds at flat win-probs (P(over23.5) 0.59 vs actual
# 0.23) — it pushed "Over" against reality. Empirical base-rate IS calibrated by construction.
# Closeness tilt is OPTIONAL and small (centered at the mean closeness so it adds 0 on average);
# off by default until validated, since our per-map win-probs are mostly ~0.5 anyway.
CLOSE_TILT = 0.0          # rounds shift per unit (avg_close - |p-0.5|); 0 = pure empirical
AVG_CLOSE = 0.08          # measured avg |p-0.5| our model produces (thin data -> flat)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=60)
    c.row_factory = sqlite3.Row
    return c


def map_stats() -> dict[str, tuple[float, float, int]]:
    """{map: (mean_total, sd_total, n)} from map_rounds."""
    conn = _conn()
    rows = conn.execute(
        "SELECT map, AVG(total_rounds) m, COUNT(*) n, "
        "AVG(total_rounds*total_rounds) m2 FROM map_rounds "
        "WHERE map IS NOT NULL AND total_rounds BETWEEN 13 AND 50 GROUP BY map"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        var = max(1.0, r["m2"] - r["m"] ** 2)
        out[r["map"]] = (r["m"], math.sqrt(var), r["n"])
    return out


_STATS_CACHE: dict | None = None
_TOTALS_CACHE: dict | None = None


def _stats() -> dict:
    global _STATS_CACHE
    if _STATS_CACHE is None:
        _STATS_CACHE = map_stats()
    return _STATS_CACHE


def _totals() -> dict:
    """{map: sorted list of total_rounds} — the empirical distribution per map."""
    global _TOTALS_CACHE
    if _TOTALS_CACHE is None:
        conn = _conn()
        rows = conn.execute(
            "SELECT map, total_rounds FROM map_rounds "
            "WHERE map IS NOT NULL AND total_rounds BETWEEN 13 AND 50").fetchall()
        conn.close()
        d: dict[str, list] = {}
        for r in rows:
            d.setdefault(r["map"], []).append(r["total_rounds"])
        _TOTALS_CACHE = {m: sorted(v) for m, v in d.items()}
    return _TOTALS_CACHE


def expected_total(mp: str, p: float) -> tuple[float, float]:
    """(expected_total_rounds, sd) — empirical map mean + a small optional closeness tilt."""
    mean, sd, n = _stats().get(mp, (21.4, 4.9, 0))
    exp = mean + CLOSE_TILT * (AVG_CLOSE - abs(p - 0.5))
    return exp, max(2.5, sd)


def _emp_over(mp: str, line: float) -> float | None:
    """Empirical P(total > line) for a map (Laplace-smoothed)."""
    tots = _totals().get(mp)
    if not tots:
        return None
    over = sum(1 for t in tots if t > line)
    return (over + 1) / (len(tots) + 2)


def avg_over(line: float) -> float | None:
    """Map-agnostic empirical P(total > line) pooled over ALL maps (Laplace-smoothed).
    Used pre-match when the actual map isn't known yet (all maps are ~21.4 anyway)."""
    allt = [t for v in _totals().values() for t in v]
    if not allt:
        return None
    over = sum(1 for t in allt if t > line)
    return (over + 1) / (len(allt) + 2)


def _norm_sf(x: float, mu: float, sd: float) -> float:
    return 0.5 * math.erfc((x - mu) / (sd * math.sqrt(2)))


def over_under(mp: str, p: float, line: float) -> dict:
    """Calibrated O/U: empirical base-rate for the line, with an optional small closeness
    tilt applied as an equivalent line shift (so heavy favourites -> shorter map -> lower
    P(over)). Falls back to Normal(empirical mean, sd) if a map has no history."""
    exp, sd = expected_total(mp, p)
    eff_line = line - CLOSE_TILT * (AVG_CLOSE - abs(p - 0.5))  # tilt as line shift
    p_over = _emp_over(mp, eff_line)
    if p_over is None:
        p_over = _norm_sf(eff_line, exp, sd)
    return {"map": mp, "expected": round(exp, 1), "sd": round(sd, 1),
            "line": line, "p_over": round(p_over, 3), "p_under": round(1 - p_over, 3),
            "fair_over": round(1 / p_over, 2) if p_over > 0.02 else None,
            "fair_under": round(1 / (1 - p_over), 2) if p_over < 0.98 else None}


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        for mp, (m, sd, n) in sorted(_stats().items(), key=lambda x: -x[1][2]):
            if n >= 20:
                print(f"  {mp:10s} n={n:5d}  mean={m:.1f}  sd={sd:.1f}")
    elif cmd == "prob":
        mp, p, L = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
        print(over_under(mp, p, L))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
