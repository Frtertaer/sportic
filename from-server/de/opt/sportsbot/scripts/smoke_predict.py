#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from analyzer import analyze_match
from scrapers import scrape_all_parallel


def _configure_stdout() -> None:
    stdout = getattr(sys, "stdout", None)
    if stdout is None or not hasattr(stdout, "reconfigure"):
        return

    try:
        stdout.reconfigure(errors="replace")
    except ValueError:
        # Some environments expose a stdout wrapper that cannot be reconfigured.
        return


def _safe_print(text: str) -> None:
    stdout = getattr(sys, "stdout", None)
    encoding = getattr(stdout, "encoding", None) or "utf-8"
    sanitized = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(sanitized)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local smoke-check for the prediction pipeline.",
    )
    parser.add_argument("--team1", required=True, help="Home team / first team")
    parser.add_argument("--team2", required=True, help="Away team / second team")
    parser.add_argument(
        "--with-ai",
        action="store_true",
        help="Run live AI analysis. This can spend Anthropic/OpenClaw balance.",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()

    data = await scrape_all_parallel(args.team1, args.team2)
    completeness_pct = int(data.data_completeness * 100)
    applicable_completeness_pct = int(data.applicable_data_completeness * 100)

    _safe_print(f"Match: {args.team1} vs {args.team2}")
    _safe_print(f"Scrape completeness (raw): {completeness_pct}%")
    _safe_print(f"Scrape completeness (applicable): {applicable_completeness_pct}%")
    _safe_print(
        "Failed sources: "
        + (", ".join(data.failed_sources) if data.failed_sources else "none")
    )
    _safe_print(
        "Unsupported sources: "
        + (", ".join(data.unsupported_sources) if data.unsupported_sources else "none")
    )

    if completeness_pct == 0:
        _safe_print("Smoke check failed: scraping returned no usable sources.")
        return 1

    run_ai = args.with_ai and not args.skip_ai
    if not run_ai:
        _safe_print("AI step skipped by default. Pass --with-ai to run live analysis.")
        return 0

    report = await analyze_match(data)
    _safe_print(f"Top recommendation: {report.top_recommendation}")
    _safe_print(f"Top confidence: {report.top_confidence_pct:.0f}%")
    _safe_print(f"Risk level: {report.risk_level}")
    if report.data_warnings:
        _safe_print("Data warnings:")
        for warning in report.data_warnings:
            _safe_print(f"  - {warning}")

    if any(warning.startswith("Analysis error:") for warning in report.data_warnings):
        _safe_print("Smoke check failed: AI analysis fell back due to an error.")
        return 1

    return 0


def main() -> None:
    _configure_stdout()
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
