"""Replay OPEN vs latest CLOSE favorite CLV. Read-only. Does not stake."""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clv_desk import archive_favorite_clv

DEFAULT_DB = Path(r"D:/sportic/from-server/de/opt/sportsbot/sportsbot.db")


def _year(match_date: str) -> str:
    text = str(match_date or "")
    if text.isdigit():
        return str(datetime.fromtimestamp(int(text), timezone.utc).year)
    return text[:4]


def replay(db_path: Path) -> dict:
    uri = "file:" + db_path.as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT m.match_date_utc, m.actual_result,
               o.odds_w1 AS open_w1, o.odds_w2 AS open_w2,
               c.odds_w1 AS close_w1, c.odds_w2 AS close_w2
        FROM historical_matches m
        JOIN historical_market_lines o
          ON o.historical_match_id = m.id AND o.snapshot_kind = 'OPEN'
        JOIN historical_market_lines c
          ON c.id = (
              SELECT id FROM historical_market_lines
              WHERE historical_match_id = m.id AND snapshot_kind = 'CLOSE'
              ORDER BY captured_at DESC, id DESC
              LIMIT 1
          )
        WHERE m.actual_result IN ('W1', 'W2', 'DRAW')
        """
    ).fetchall()
    conn.close()
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        item = archive_favorite_clv(
            {"odds_w1": row["open_w1"], "odds_w2": row["open_w2"]},
            {"odds_w1": row["close_w1"], "odds_w2": row["close_w2"]},
            row["actual_result"],
        )
        if item is None:
            continue
        buckets["ALL"].append(item)
        buckets[_year(row["match_date_utc"])].append(item)
    return {key: _summary(value) for key, value in sorted(buckets.items())}


def _summary(items: list[dict]) -> dict:
    plus = [item for item in items if item["clv"] > 0.0]
    minus = [item for item in items if item["clv"] < 0.0]
    return {
        "n": len(items),
        "plus_n": len(plus),
        "plus_roi": _roi(plus),
        "minus_n": len(minus),
        "minus_roi": _roi(minus),
    }


def _roi(items: list[dict]) -> None | float:
    if not items:
        return None
    return round(sum(item["profit"] for item in items) / len(items), 4)


def main() -> None:
    report = replay(DEFAULT_DB)
    for key, row in report.items():
        print(
            key,
            "n",
            row["n"],
            "plus",
            row["plus_n"],
            row["plus_roi"],
            "minus",
            row["minus_n"],
            row["minus_roi"],
        )


if __name__ == "__main__":
    main()
