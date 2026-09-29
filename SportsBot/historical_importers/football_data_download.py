from __future__ import annotations

import urllib.request
from pathlib import Path


FOOTBALL_DATA_BASE_URL = "https://www.football-data.co.uk/mmz4281"
DEFAULT_TOP_DIVISIONS = ("E0", "SP1", "D1", "I1", "F1", "P1")


def build_season_code(start_year: int) -> str:
    if start_year < 2000 or start_year > 2099:
        raise ValueError(f"Unsupported season start year: {start_year}")
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def build_csv_url(division_code: str, start_year: int) -> str:
    season_code = build_season_code(start_year)
    normalized_division = str(division_code or "").strip().upper()
    if not normalized_division:
        raise ValueError("division_code is required")
    return f"{FOOTBALL_DATA_BASE_URL}/{season_code}/{normalized_division}.csv"


def build_download_path(base_dir: str | Path, division_code: str, start_year: int) -> Path:
    season_code = build_season_code(start_year)
    normalized_division = str(division_code or "").strip().upper()
    return Path(base_dir) / season_code / f"{normalized_division}.csv"


def download_csv(
    *,
    division_code: str,
    start_year: int,
    base_dir: str | Path,
    overwrite: bool = False,
    timeout_seconds: int = 30,
) -> Path:
    target_path = build_download_path(base_dir, division_code, start_year)
    if target_path.exists() and not overwrite:
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        build_csv_url(division_code, start_year),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content = response.read()
    target_path.write_bytes(content)
    return target_path
