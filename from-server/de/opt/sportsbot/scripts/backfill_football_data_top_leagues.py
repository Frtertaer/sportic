#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import database as db
from historical_importers.football_data import FootballDataHistoricalImporter
from historical_importers.football_data_download import (
    DEFAULT_TOP_DIVISIONS,
    build_season_code,
    download_csv,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and import football-data.co.uk CSVs for top leagues and seasons.",
    )
    parser.add_argument(
        "--divisions",
        nargs="+",
        default=list(DEFAULT_TOP_DIVISIONS),
        help="Division codes, for example: E0 SP1 D1 I1 F1 P1",
    )
    parser.add_argument(
        "--start-season",
        type=int,
        default=2022,
        help="Season start year, for example 2022 for 2022/23.",
    )
    parser.add_argument(
        "--end-season",
        type=int,
        default=2025,
        help="Season start year upper bound, inclusive.",
    )
    parser.add_argument(
        "--download-dir",
        default=str(ROOT_DIR / "data" / "football_data_co_uk"),
        help="Where downloaded CSVs should be stored.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and summarize without writing to SQLite.",
    )
    parser.add_argument(
        "--overwrite-downloads",
        action="store_true",
        help="Re-download CSVs even if they already exist locally.",
    )
    parser.add_argument(
        "--skip-recalibration",
        action="store_true",
        help="Skip numeric recalibration after the full import.",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    await db.init_db()

    download_dir = Path(args.download_dir)
    start_years = list(range(args.start_season, args.end_season + 1))

    total_downloaded = 0
    total_rows = 0
    total_matches_inserted = 0
    total_matches_updated = 0
    total_market_inserted = 0
    total_market_updated = 0
    total_snapshot_inserted = 0
    total_snapshot_updated = 0
    total_skipped = 0

    for start_year in start_years:
        season_code = build_season_code(start_year)
        print(f"Season {start_year}/{(start_year + 1) % 100:02d} ({season_code})")
        for division in args.divisions:
            csv_path = download_csv(
                division_code=division,
                start_year=start_year,
                base_dir=download_dir,
                overwrite=args.overwrite_downloads,
            )
            total_downloaded += 1
            importer = FootballDataHistoricalImporter(csv_path=csv_path, dry_run=args.dry_run)
            summary = await importer.run()
            print(
                f"  {division}: rows {summary.rows_processed}, "
                f"matches +{summary.match_rows_inserted}/~{summary.match_rows_updated}, "
                f"markets +{summary.market_rows_inserted}/~{summary.market_rows_updated}, "
                f"snapshots +{summary.snapshot_rows_inserted}/~{summary.snapshot_rows_updated}, "
                f"skipped {summary.skipped_rows}"
            )
            total_rows += summary.rows_processed
            total_matches_inserted += summary.match_rows_inserted
            total_matches_updated += summary.match_rows_updated
            total_market_inserted += summary.market_rows_inserted
            total_market_updated += summary.market_rows_updated
            total_snapshot_inserted += summary.snapshot_rows_inserted
            total_snapshot_updated += summary.snapshot_rows_updated
            total_skipped += summary.skipped_rows
        print("")

    print("Totals")
    print(f"Downloaded CSVs: {total_downloaded}")
    print(f"Rows processed: {total_rows}")
    print(f"Match rows inserted: {total_matches_inserted}")
    print(f"Match rows updated: {total_matches_updated}")
    print(f"Market rows inserted: {total_market_inserted}")
    print(f"Market rows updated: {total_market_updated}")
    print(f"Snapshot rows inserted: {total_snapshot_inserted}")
    print(f"Snapshot rows updated: {total_snapshot_updated}")
    print(f"Skipped rows: {total_skipped}")

    if not args.dry_run and not args.skip_recalibration:
        recalibration = await db.recompute_numeric_recalibration()
        print("")
        print("Numeric recalibration")
        print(f"Rows evaluated: {int(recalibration.get('rows_evaluated', 0) or 0)}")
        print(f"Profiles written: {int(recalibration.get('profiles_written', 0) or 0)}")

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
