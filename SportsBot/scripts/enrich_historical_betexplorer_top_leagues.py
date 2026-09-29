#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import database as db
from historical_importers.betexplorer import (
    BETEXPLORER_HISTORICAL_LEAGUES,
    BetexplorerHistoricalEnricher,
)


DEFAULT_TOP_LEAGUES = ("eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "por.1")


@dataclass(slots=True)
class AggregateSummary:
    candidate_matches: int = 0
    matched_rows: int = 0
    market_rows_inserted: int = 0
    market_rows_updated: int = 0
    unmatched_rows: int = 0
    odds_fetch_failures: int = 0
    skipped_windows: int = 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk-enrich historical matches with Betexplorer closing markets in smaller date windows.",
    )
    parser.add_argument(
        "--leagues",
        nargs="+",
        default=list(DEFAULT_TOP_LEAGUES),
        choices=sorted(BETEXPLORER_HISTORICAL_LEAGUES.keys()),
        help="Betexplorer league keys, for example eng.1 esp.1 ger.1.",
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
        "--window-days",
        type=int,
        default=45,
        help="Date window size to keep each enrichment pass manageable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and match rows without writing enriched market lines to SQLite.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip windows that already have a successful Betexplorer import run recorded.",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def build_date_windows(date_from: date, date_to: date, *, window_days: int) -> list[tuple[date, date]]:
    if date_to < date_from:
        raise ValueError("date_to must be on or after date_from")
    size = max(1, int(window_days))
    windows: list[tuple[date, date]] = []
    current_start = date_from
    while current_start <= date_to:
        current_end = min(date_to, current_start + timedelta(days=size - 1))
        windows.append((current_start, current_end))
        current_start = current_end + timedelta(days=1)
    return windows


def build_successful_window_index(runs: list[dict]) -> set[tuple[str, str, str]]:
    completed: set[tuple[str, str, str]] = set()
    for run in runs:
        if str(run.get("provider_name") or "") != "betexplorer":
            continue
        if str(run.get("status") or "") != "SUCCESS":
            continue
        competition_name = str(run.get("competition_name") or "").strip()
        requested_from = str(run.get("requested_from") or "").strip()
        requested_to = str(run.get("requested_to") or "").strip()
        if not competition_name or not requested_from or not requested_to:
            continue
        completed.add((competition_name, requested_from, requested_to))
    return completed


async def _run() -> int:
    args = _parse_args()
    await db.init_db()

    date_from = _parse_date(args.date_from)
    date_to = _parse_date(args.date_to)
    windows = build_date_windows(date_from, date_to, window_days=args.window_days)
    successful_windows: set[tuple[str, str, str]] = set()
    if args.resume and not args.dry_run:
        successful_windows = build_successful_window_index(
            await db.get_recent_historical_import_runs(limit=5000)
        )

    overall = AggregateSummary()
    print(f"Windows: {len(windows)} ({date_from.isoformat()} -> {date_to.isoformat()}, {args.window_days}d)")
    for league_key in args.leagues:
        competition_name = BETEXPLORER_HISTORICAL_LEAGUES[league_key][0]
        league_total = AggregateSummary()
        print(f"\nLeague: {competition_name} ({league_key})")
        for window_from, window_to in windows:
            window_key = (competition_name, window_from.isoformat(), window_to.isoformat())
            if window_key in successful_windows:
                league_total.skipped_windows += 1
                overall.skipped_windows += 1
                print(
                    f"  {window_from.isoformat()} -> {window_to.isoformat()}: "
                    "skipped (already successful)"
                )
                continue
            enricher = BetexplorerHistoricalEnricher(
                league_key=league_key,
                date_from=window_from,
                date_to=window_to,
                dry_run=args.dry_run,
            )
            summary = await enricher.run()
            league_total.candidate_matches += summary.candidate_matches
            league_total.matched_rows += summary.matched_rows
            league_total.market_rows_inserted += summary.market_rows_inserted
            league_total.market_rows_updated += summary.market_rows_updated
            league_total.unmatched_rows += summary.unmatched_rows
            league_total.odds_fetch_failures += summary.odds_fetch_failures
            print(
                f"  {window_from.isoformat()} -> {window_to.isoformat()}: "
                f"candidates {summary.candidate_matches}, matched {summary.matched_rows}, "
                f"+{summary.market_rows_inserted}/~{summary.market_rows_updated}, "
                f"unmatched {summary.unmatched_rows}, fetch failures {summary.odds_fetch_failures}"
            )

        overall.candidate_matches += league_total.candidate_matches
        overall.matched_rows += league_total.matched_rows
        overall.market_rows_inserted += league_total.market_rows_inserted
        overall.market_rows_updated += league_total.market_rows_updated
        overall.unmatched_rows += league_total.unmatched_rows
        overall.odds_fetch_failures += league_total.odds_fetch_failures
        print(
            f"  Total: candidates {league_total.candidate_matches}, matched {league_total.matched_rows}, "
            f"+{league_total.market_rows_inserted}/~{league_total.market_rows_updated}, "
            f"unmatched {league_total.unmatched_rows}, fetch failures {league_total.odds_fetch_failures}, "
            f"skipped {league_total.skipped_windows}"
        )

    print("\nOverall")
    print(f"Candidate matches: {overall.candidate_matches}")
    print(f"Matched rows: {overall.matched_rows}")
    print(f"Market rows inserted: {overall.market_rows_inserted}")
    print(f"Market rows updated: {overall.market_rows_updated}")
    print(f"Unmatched rows: {overall.unmatched_rows}")
    print(f"Odds fetch failures: {overall.odds_fetch_failures}")
    print(f"Skipped windows: {overall.skipped_windows}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
