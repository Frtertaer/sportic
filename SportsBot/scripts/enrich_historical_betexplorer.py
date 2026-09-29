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
from historical_importers.betexplorer import (
    BETEXPLORER_HISTORICAL_LEAGUES,
    BetexplorerHistoricalEnricher,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich existing historical matches with Betexplorer closing market lines.",
    )
    parser.add_argument(
        "--league",
        required=True,
        choices=sorted(BETEXPLORER_HISTORICAL_LEAGUES.keys()),
        help="League key to enrich, for example por.1 or esp.1.",
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
        help="Parse and match rows without writing enriched market lines to SQLite.",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


async def _run() -> int:
    args = _parse_args()
    await db.init_db()

    enricher = BetexplorerHistoricalEnricher(
        league_key=args.league,
        date_from=_parse_date(args.date_from),
        date_to=_parse_date(args.date_to),
        dry_run=args.dry_run,
    )
    summary = await enricher.run()

    print(f"League: {summary.competition_name} ({summary.league_key})")
    print(f"Requested range: {summary.requested_from} -> {summary.requested_to}")
    print(f"Dry run: {args.dry_run}")
    print(f"Run id: {summary.run_id}")
    print(f"Candidate matches: {summary.candidate_matches}")
    print(f"Matched rows: {summary.matched_rows}")
    print(f"Market rows inserted: {summary.market_rows_inserted}")
    print(f"Market rows updated: {summary.market_rows_updated}")
    print(f"Unmatched rows: {summary.unmatched_rows}")
    print(f"Odds fetch failures: {summary.odds_fetch_failures}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
