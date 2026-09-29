"""Closing-line desk for SportsBot.

The archive proof is not a pre-match picker. A 1X2 stake stays at 0 until the
bot's own resolved 1X2 prices have a large, positive mean CLV.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

MIN_OWN_SAMPLE = 200
CLV_BEAT_CLOSE = 0.0

_SELECTION_CLOSE = {
    "TEAM1": "close_w1",
    "W1": "close_w1",
    "TEAM2": "close_w2",
    "W2": "close_w2",
    "DRAW": "close_draw",
    "X": "close_draw",
}
_SELECTION_OPEN = {
    "TEAM1": "pin_w1",
    "W1": "pin_w1",
    "TEAM2": "pin_w2",
    "W2": "pin_w2",
    "DRAW": "pin_draw",
    "X": "pin_draw",
}


def clv_percent(placement_odds: Optional[float], closing_odds: Optional[float]) -> Optional[float]:
    """Same sign as scrapers.pinnacle.compute_clv. None when the price is unusable."""
    if placement_odds is None or closing_odds is None:
        return None
    if placement_odds <= 1.0 or closing_odds <= 1.0:
        return None
    placement_implied = 1.0 / placement_odds
    closing_implied = 1.0 / closing_odds
    return round((closing_implied - placement_implied) / placement_implied * 100.0, 2)


def own_1x2_clv(row: dict) -> Optional[float]:
    market = str(row.get("top_market") or "").strip().upper()
    if market != "1X2":
        return None
    selection = str(row.get("top_selection") or "").strip().upper()
    close_key = _SELECTION_CLOSE.get(selection)
    if not close_key:
        return None
    placement = row.get("placement_odds")
    if placement is None:
        placement = row.get(_SELECTION_OPEN.get(selection, ""))
    return clv_percent(placement, row.get(close_key))


def mean_clv(samples: list[float]) -> Optional[float]:
    clean = [float(value) for value in samples if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def stake_allowed(samples: list[float]) -> bool:
    clean = [float(value) for value in samples if value is not None]
    if len(clean) < MIN_OWN_SAMPLE:
        return False
    average = sum(clean) / len(clean)
    return average > CLV_BEAT_CLOSE


def gate_reason(samples: list[float]) -> str:
    clean = [float(value) for value in samples if value is not None]
    average = mean_clv(clean)
    shown = "n/a" if average is None else f"{average:+.2f}%"
    return (
        f"1X2 stake is 0u until {MIN_OWN_SAMPLE} resolved own prices beat the close "
        f"(ledger n={len(clean)}, mean CLV {shown})."
    )


def apply_1x2_stake_gate(report_dict: dict, samples: list[float]) -> bool:
    """Zero a positive 1X2 stake when the own ledger is not yet proven."""
    market = str(report_dict.get("top_market_code") or "").strip().upper()
    if market != "1X2":
        return False
    if float(report_dict.get("stake_units") or 0.0) <= 0.0:
        return False
    if stake_allowed(samples):
        return False
    report_dict["stake_units"] = 0.0
    report_dict["stake_label"] = "No bet"
    report_dict["stake_rationale"] = gate_reason(samples)
    warnings = list(report_dict.get("data_warnings") or [])
    note = "CLV desk withheld the 1X2 stake"
    if note not in warnings:
        warnings.append(note)
    report_dict["data_warnings"] = warnings
    return True


def load_own_1x2_clv(db_path: Optional[Path] = None) -> list[float]:
    path = Path(db_path) if db_path else Path(__file__).resolve().parent / "sportsbot.db"
    if not path.exists():
        return []
    uri = "file:" + path.as_posix() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT top_market, top_selection, placement_odds,
                       pin_w1, pin_draw, pin_w2,
                       close_w1, close_draw, close_w2
                FROM clv_tracking
                WHERE resolved = 1
                """
            ).fetchall()
        except sqlite3.Error:
            return []
    finally:
        conn.close()
    samples: list[float] = []
    for row in rows:
        value = own_1x2_clv(dict(row))
        if value is not None:
            samples.append(value)
    return samples


def favorite_side(odds_w1: Optional[float], odds_w2: Optional[float]) -> Optional[str]:
    if odds_w1 is None or odds_w2 is None or odds_w1 <= 1.0 or odds_w2 <= 1.0:
        return None
    if odds_w1 == odds_w2:
        return None
    return "W1" if odds_w1 < odds_w2 else "W2"


def archive_favorite_clv(open_row: dict, close_row: dict, actual_result: Optional[str]) -> Optional[dict]:
    side = favorite_side(open_row.get("odds_w1"), open_row.get("odds_w2"))
    if side is None or actual_result not in {"W1", "W2", "DRAW"}:
        return None
    open_price = open_row.get("odds_w1") if side == "W1" else open_row.get("odds_w2")
    close_price = close_row.get("odds_w1") if side == "W1" else close_row.get("odds_w2")
    clv = clv_percent(open_price, close_price)
    if clv is None:
        return None
    won = actual_result == side
    profit = (float(open_price) - 1.0) if won else -1.0
    return {"side": side, "clv": clv, "won": won, "profit": profit}
