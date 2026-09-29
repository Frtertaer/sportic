from __future__ import annotations

import unittest
from datetime import date

from scripts.enrich_historical_betexplorer_top_leagues import (
    build_date_windows,
    build_successful_window_index,
)


class EnrichHistoricalBetexplorerTopLeaguesTests(unittest.TestCase):
    def test_build_date_windows_splits_inclusive_ranges(self) -> None:
        windows = build_date_windows(date(2026, 2, 1), date(2026, 3, 15), window_days=20)
        self.assertEqual(
            windows,
            [
                (date(2026, 2, 1), date(2026, 2, 20)),
                (date(2026, 2, 21), date(2026, 3, 12)),
                (date(2026, 3, 13), date(2026, 3, 15)),
            ],
        )

    def test_build_date_windows_rejects_reversed_range(self) -> None:
        with self.assertRaises(ValueError):
            build_date_windows(date(2026, 3, 15), date(2026, 2, 1), window_days=10)

    def test_build_successful_window_index_filters_only_successful_betexplorer_runs(self) -> None:
        index = build_successful_window_index(
            [
                {
                    "provider_name": "betexplorer",
                    "status": "SUCCESS",
                    "competition_name": "Premier League",
                    "requested_from": "2024-08-01",
                    "requested_to": "2024-08-30",
                },
                {
                    "provider_name": "betexplorer",
                    "status": "FAILED",
                    "competition_name": "Premier League",
                    "requested_from": "2024-08-31",
                    "requested_to": "2024-09-29",
                },
                {
                    "provider_name": "espn",
                    "status": "SUCCESS",
                    "competition_name": "Premier League",
                    "requested_from": "2024-08-01",
                    "requested_to": "2024-08-30",
                },
            ]
        )
        self.assertEqual(
            index,
            {
                ("Premier League", "2024-08-01", "2024-08-30"),
            },
        )
