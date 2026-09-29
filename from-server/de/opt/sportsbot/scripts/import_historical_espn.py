#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import database as db
from historical_importers.espn import ESPNHistoricalImporter
from scrapers.fotmob import ESPN_SCHEDULE_LEAGUES


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import historical finished matches and market lines from ESPN scoreboard into the historical_* tables.",
    )
    parser.add_argument(
        "--league",
        required=True,
        choices=sorted(ESPN_SCHEDULE_LEAGUES.keys()),
        help="ESPN soccer league slug, for example por.1 or esp.1.",
    )
    parser.add_argument(
        "--date-from",
        required=True,
        help="Start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--date-to",
        required=True,
        help="End date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse without writing to SQLite.",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


async def _run() -> int:
    args = _parse_args()
    await db.init_db()

    importer = ESPNHistoricalImporter(
        league_slug=args.league,
        date_from=_parse_date(args.date_from),
        date_to=_parse_date(args.date_to),
        dry_run=args.dry_run,
    )
    summary = await importer.run()

    print(f"League: {summary.competition_name} ({summary.league_slug})")
    print(f"Requested range: {summary.requested_from} -> {summary.requested_to}")
    print(f"Dry run: {args.dry_run}")
    print(f"Run id: {summary.run_id}")
    print(f"Days processed: {summary.days_processed}")
    print(f"Failed days: {summary.failed_days}")
    print(f"Match rows inserted: {summary.match_rows_inserted}")
    print(f"Match rows updated: {summary.match_rows_updated}")
    print(f"Snapshot rows inserted: {summary.snapshot_rows_inserted}")
    print(f"Snapshot rows updated: {summary.snapshot_rows_updated}")
    print(f"Market rows inserted: {summary.market_rows_inserted}")
    print(f"Market rows updated: {summary.market_rows_updated}")
    if not args.dry_run:
        recalibration = await db.recompute_numeric_recalibration()
        print(
            "Numeric recalibration: "
            f"{recalibration['profiles_written']} profiles from "
            f"{recalibration['rows_evaluated']} historical rows"
        )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
