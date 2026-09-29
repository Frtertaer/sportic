from __future__ import annotations

import unittest
from pathlib import Path

from historical_importers.football_data_download import (
    build_csv_url,
    build_download_path,
    build_season_code,
)


class FootballDataDownloadTests(unittest.TestCase):
    def test_build_season_code_formats_two_year_pair(self) -> None:
        self.assertEqual(build_season_code(2022), "2223")
        self.assertEqual(build_season_code(2025), "2526")

    def test_build_csv_url_uses_official_path_shape(self) -> None:
        self.assertEqual(
            build_csv_url("E0", 2025),
            "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
        )

    def test_build_download_path_groups_by_season_code(self) -> None:
        path = build_download_path("C:/tmp/fd", "SP1", 2024)
        self.assertEqual(path, Path("C:/tmp/fd/2425/SP1.csv"))


if __name__ == "__main__":
    unittest.main()
