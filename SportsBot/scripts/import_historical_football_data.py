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
from historical_importers.betexplorer import BetexplorerHistoricalEnricher
from historical_importers.football_data import FootballDataHistoricalImporter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import football-data.co.uk CSV files into historical_* tables.",
    )
    parser.add_argument(
        "csv_paths",
        nargs="+",
        help="Path(s) to football-data.co.uk CSV files, for example F1.csv or E0.csv.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and summarize without writing to SQLite.",
    )
    parser.add_argument(
        "--skip-betexplorer-enrichment",
        action="store_true",
        help="Skip post-import Betexplorer enrichment for supported leagues.",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    await db.init_db()

    total_rows = 0
    total_matches_inserted = 0
    total_matches_updated = 0
    total_market_inserted = 0
    total_market_updated = 0
    total_snapshot_inserted = 0
    total_snapshot_updated = 0
    total_skipped = 0
    total_enriched_matches = 0
    total_enriched_market_inserted = 0
    total_enriched_market_updated = 0
    total_enriched_unmatched = 0
    total_enriched_failures = 0

    for csv_path in args.csv_paths:
        importer = FootballDataHistoricalImporter(csv_path=csv_path, dry_run=args.dry_run)
        summary = await importer.run()
        print(f"CSV: {summary.csv_path}")
        print(f"Competition: {summary.competition_name}")
        print(f"Division code: {summary.division_code}")
        print(f"Betexplorer league key: {summary.betexplorer_league_key}")
        print(f"Range: {summary.requested_from} -> {summary.requested_to}")
        print(f"Dry run: {args.dry_run}")
        print(f"Run id: {summary.run_id}")
        print(f"Rows processed: {summary.rows_processed}")
        print(f"Match rows inserted: {summary.match_rows_inserted}")
        print(f"Match rows updated: {summary.match_rows_updated}")
        print(f"Market rows inserted: {summary.market_rows_inserted}")
        print(f"Market rows updated: {summary.market_rows_updated}")
        print(f"Snapshot rows inserted: {summary.snapshot_rows_inserted}")
        print(f"Snapshot rows updated: {summary.snapshot_rows_updated}")
        print(f"Skipped rows: {summary.skipped_rows}")
        print("")

        total_rows += summary.rows_processed
        total_matches_inserted += summary.match_rows_inserted
        total_matches_updated += summary.match_rows_updated
        total_market_inserted += summary.market_rows_inserted
        total_market_updated += summary.market_rows_updated
        total_snapshot_inserted += summary.snapshot_rows_inserted
        total_snapshot_updated += summary.snapshot_rows_updated
        total_skipped += summary.skipped_rows

        should_enrich = (
            not args.dry_run
            and not args.skip_betexplorer_enrichment
            and summary.betexplorer_league_key is not None
            and summary.requested_from is not None
            and summary.requested_to is not None
        )
        if should_enrich:
            enricher = BetexplorerHistoricalEnricher(
                league_key=summary.betexplorer_league_key,
                date_from=date.fromisoformat(summary.requested_from),
                date_to=date.fromisoformat(summary.requested_to),
            )
            enrichment = await enricher.run()
            print("Betexplorer enrichment")
            print(f"League key: {enrichment.league_key}")
            print(f"Candidates: {enrichment.candidate_matches}")
            print(f"Matched rows: {enrichment.matched_rows}")
            print(f"Market rows inserted: {enrichment.market_rows_inserted}")
            print(f"Market rows updated: {enrichment.market_rows_updated}")
            print(f"Unmatched rows: {enrichment.unmatched_rows}")
            print(f"Odds fetch failures: {enrichment.odds_fetch_failures}")
            print("")

            total_enriched_matches += enrichment.matched_rows
            total_enriched_market_inserted += enrichment.market_rows_inserted
            total_enriched_market_updated += enrichment.market_rows_updated
            total_enriched_unmatched += enrichment.unmatched_rows
            total_enriched_failures += enrichment.odds_fetch_failures

    print("Totals")
    print(f"Rows processed: {total_rows}")
    print(f"Match rows inserted: {total_matches_inserted}")
    print(f"Match rows updated: {total_matches_updated}")
    print(f"Market rows inserted: {total_market_inserted}")
    print(f"Market rows updated: {total_market_updated}")
    print(f"Snapshot rows inserted: {total_snapshot_inserted}")
    print(f"Snapshot rows updated: {total_snapshot_updated}")
    print(f"Skipped rows: {total_skipped}")
    if not args.dry_run and not args.skip_betexplorer_enrichment:
        print(f"Betexplorer matched rows: {total_enriched_matches}")
        print(f"Betexplorer market rows inserted: {total_enriched_market_inserted}")
        print(f"Betexplorer market rows updated: {total_enriched_market_updated}")
        print(f"Betexplorer unmatched rows: {total_enriched_unmatched}")
        print(f"Betexplorer odds fetch failures: {total_enriched_failures}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
