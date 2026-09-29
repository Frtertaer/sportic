from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin

import database as db
from scrapers.base import BaseScraper
from scrapers.fotmob import _names_match
from scrapers.oddsportal import (
    BASE_URL,
    _BE_HEADERS,
    _ODDS_HEADERS,
    OddsportalScraper,
)
from team_parser import normalize_team_name

logger = logging.getLogger(__name__)
_ENRICHMENT_FETCH_CONCURRENCY = 4

ResultsFetcher = Callable[[str], Awaitable[str]]


BETEXPLORER_HISTORICAL_LEAGUES: dict[str, tuple[str, str]] = {
    "eng.1": ("Premier League", "/football/england/premier-league/results/"),
    "eng.2": ("Championship", "/football/england/championship/results/"),
    "esp.1": ("La Liga", "/football/spain/laliga/results/"),
    "fra.1": ("Ligue 1", "/football/france/ligue-1/results/"),
    "ger.1": ("Bundesliga", "/football/germany/bundesliga/results/"),
    "ita.1": ("Serie A", "/football/italy/serie-a/results/"),
    "ned.1": ("Eredivisie", "/football/netherlands/eredivisie/results/"),
    "por.1": ("Primeira Liga", "/football/portugal/liga-portugal/results/"),
    "uefa.champions": ("Champions League", "/football/europe/champions-league/results/"),
    "uefa.europa": ("Europa League", "/football/europe/europa-league/results/"),
    "uefa.conference": ("Conference League", "/football/europe/conference-league/results/"),
}

BETEXPLORER_COMPETITION_ALIASES: dict[str, tuple[str, ...]] = {
    "eng.1": ("Premier League", "English Premier League"),
    "eng.2": ("Championship", "English League Championship"),
    "esp.1": ("La Liga", "Spanish LALIGA", "LALIGA EA Sports"),
    "fra.1": ("Ligue 1", "French Ligue 1"),
    "ger.1": ("Bundesliga", "German Bundesliga"),
    "ita.1": ("Serie A", "Italian Serie A"),
    "ned.1": ("Eredivisie", "Dutch Eredivisie"),
    "por.1": ("Primeira Liga", "Portuguese Primeira Liga"),
    "uefa.champions": ("Champions League", "UEFA Champions League"),
    "uefa.europa": ("Europa League", "UEFA Europa League"),
    "uefa.conference": ("Conference League", "UEFA Conference League"),
}


@dataclass(slots=True)
class EnrichmentSummary:
    league_key: str
    competition_name: str
    requested_from: str
    requested_to: str
    run_id: Optional[int]
    candidate_matches: int = 0
    matched_rows: int = 0
    market_rows_inserted: int = 0
    market_rows_updated: int = 0
    unmatched_rows: int = 0
    odds_fetch_failures: int = 0


class _BetexplorerHistoryClient(BaseScraper):
    DOMAIN = "betexplorer.com"

    async def scrape(self):
        raise NotImplementedError("Historical enricher uses only _fetch_with_retry().")


def _normalized_probs_from_decimal(*odds: Optional[float]) -> tuple[Optional[float], ...]:
    if any(odd is None or odd <= 1.0 for odd in odds):
        return tuple(None for _ in odds)
    implied = [1.0 / float(odd) for odd in odds]
    total = sum(implied)
    if total <= 0:
        return tuple(None for _ in odds)
    return tuple(round((part / total) * 100.0, 4) for part in implied)


def _decimal_from_probability_pct(probability_pct: Optional[float]) -> Optional[float]:
    if probability_pct is None or probability_pct <= 0.0:
        return None
    return round(100.0 / probability_pct, 4)


def _assign_derived_markets(payload: dict) -> None:
    implied_w1, implied_draw, implied_w2 = _normalized_probs_from_decimal(
        payload.get("odds_w1"),
        payload.get("odds_draw"),
        payload.get("odds_w2"),
    )
    payload["implied_prob_w1"] = implied_w1
    payload["implied_prob_draw"] = implied_draw
    payload["implied_prob_w2"] = implied_w2
    if None in (implied_w1, implied_draw, implied_w2):
        return
    p1 = float(implied_w1)
    pd = float(implied_draw)
    p2 = float(implied_w2)
    no_draw_total = p1 + p2
    if no_draw_total > 0:
        payload["odds_dnb_team1"] = _decimal_from_probability_pct((p1 * 100.0) / no_draw_total)
        payload["odds_dnb_team2"] = _decimal_from_probability_pct((p2 * 100.0) / no_draw_total)
    payload["odds_dc_1x"] = _decimal_from_probability_pct(p1 + pd)
    payload["odds_dc_12"] = _decimal_from_probability_pct(p1 + p2)
    payload["odds_dc_x2"] = _decimal_from_probability_pct(pd + p2)


def _has_market_data(payload: dict) -> bool:
    return any(
        payload.get(key) is not None
        for key in (
            "odds_w1",
            "odds_draw",
            "odds_w2",
            "odds_over_1_5",
            "odds_under_1_5",
            "odds_over_2_5",
            "odds_under_2_5",
            "odds_over_3_5",
            "odds_under_3_5",
            "odds_btts_yes",
            "odds_btts_no",
            "odds_dnb_team1",
            "odds_dnb_team2",
            "odds_dc_1x",
            "odds_dc_12",
            "odds_dc_x2",
            "ah_team1_odds",
            "ah_team2_odds",
        )
    )


def _extract_odd(cell) -> Optional[float]:
    if cell is None:
        return None
    raw_value = cell.get("data-odd")
    if raw_value in (None, ""):
        nested = cell.select_one("[data-odd]")
        raw_value = None if nested is None else nested.get("data-odd")
    if raw_value in (None, ""):
        return None
    try:
        odd = float(raw_value)
    except (TypeError, ValueError):
        return None
    if odd <= 1.0:
        return None
    return round(odd, 4)


def _parse_score(score_text: str) -> Optional[tuple[int, int]]:
    try:
        left, right = str(score_text).replace(" ", "").replace(":", "-", 1).split("-", 1)
        return int(left), int(right)
    except (AttributeError, TypeError, ValueError):
        return None


def _parse_results_date(date_text: str, *, reference_date: date) -> Optional[str]:
    normalized = " ".join(str(date_text or "").strip().split()).lower()
    if not normalized:
        return None
    if normalized == "today":
        return reference_date.isoformat()
    if normalized == "yesterday":
        return (reference_date - timedelta(days=1)).isoformat()

    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})?", normalized)
    if not match:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    year_group = match.group(3)
    if year_group:
        year = int(year_group)
        if year < 100:
            year += 2000
    else:
        year = reference_date.year
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    if not year_group and parsed > (reference_date + timedelta(days=7)):
        parsed = date(year - 1, month, day)
    return parsed.isoformat()


def _season_start_year(value: date) -> int:
    # Betexplorer archive slugs use the common European football season split.
    return value.year if value.month >= 7 else value.year - 1


def _season_slug_for_date(value: date) -> str:
    start_year = _season_start_year(value)
    return f"{start_year}-{start_year + 1}"


def _build_results_paths_for_range(
    base_results_path: str,
    *,
    date_from: date,
    date_to: date,
    reference_date: date,
) -> list[str]:
    results_suffix = "/results/"
    normalized_base = "/" + str(base_results_path or "").strip("/")
    if normalized_base.endswith("/results"):
        normalized_base = normalized_base + "/"
    elif not normalized_base.endswith(results_suffix):
        normalized_base = normalized_base.rstrip("/") + results_suffix
    if normalized_base.endswith(results_suffix):
        root_path = normalized_base[: -len(results_suffix)]
    else:
        root_path = normalized_base.rstrip("/")
    current_season = _season_slug_for_date(reference_date)

    paths: list[str] = [normalized_base]
    seen: set[str] = {normalized_base}

    season_start = _season_start_year(date_from)
    season_end = _season_start_year(date_to)
    for start_year in range(season_start, season_end + 1):
        season_slug = f"{start_year}-{start_year + 1}"
        if season_slug == current_season:
            continue
        archive_path = f"{root_path}-{season_slug}/results/"
        if archive_path in seen:
            continue
        seen.add(archive_path)
        paths.append(archive_path)
    return paths


def parse_results_page(html: str, *, reference_date: date) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for row in soup.select("table.table-main tr"):
        link = row.select_one("a.in-match[href]")
        if link is None:
            continue
        team_spans = link.select("span")
        if len(team_spans) >= 2:
            home = team_spans[0].get_text(" ", strip=True)
            away = team_spans[1].get_text(" ", strip=True)
        else:
            text = link.get_text(" ", strip=True)
            parts = [part.strip() for part in text.split(" - ", 1)]
            if len(parts) != 2:
                continue
            home, away = parts

        cells = row.select("td")
        if len(cells) < 2:
            continue
        score_text = cells[1].get_text(" ", strip=True)
        parsed_score = _parse_score(score_text)
        if parsed_score is None:
            continue

        date_iso = _parse_results_date(cells[-1].get_text(" ", strip=True), reference_date=reference_date)
        if date_iso is None:
            continue

        odds_cells = row.select("td.table-main__odds")
        odds_w1 = _extract_odd(odds_cells[0]) if len(odds_cells) >= 1 else None
        odds_draw = _extract_odd(odds_cells[1]) if len(odds_cells) >= 2 else None
        odds_w2 = _extract_odd(odds_cells[2]) if len(odds_cells) >= 3 else None

        rows.append(
            {
                "match_date_utc": date_iso,
                "home": home,
                "away": away,
                "home_normalized": normalize_team_name(home).lower(),
                "away_normalized": normalize_team_name(away).lower(),
                "score_home": parsed_score[0],
                "score_away": parsed_score[1],
                "href": urljoin(BASE_URL, str(link.get("href") or "")),
                "odds_w1": odds_w1,
                "odds_draw": odds_draw,
                "odds_w2": odds_w2,
            }
        )
    return rows


def _historical_row_matches(match_row: dict, result_row: dict) -> bool:
    if str(match_row.get("match_date_utc") or "") != str(result_row.get("match_date_utc") or ""):
        return False
    if match_row.get("actual_goals_home") is not None and match_row.get("actual_goals_away") is not None:
        if (
            int(match_row["actual_goals_home"]) != int(result_row["score_home"])
            or int(match_row["actual_goals_away"]) != int(result_row["score_away"])
        ):
            return False
    return (
        _names_match([str(match_row.get("team1_name") or "")], [str(result_row.get("home") or "")])
        and _names_match([str(match_row.get("team2_name") or "")], [str(result_row.get("away") or "")])
    )


class BetexplorerHistoricalEnricher:
    def __init__(
        self,
        *,
        league_key: str,
        date_from: date,
        date_to: date,
        fetcher: Optional[ResultsFetcher] = None,
        dry_run: bool = False,
        reference_date: Optional[date] = None,
    ) -> None:
        if league_key not in BETEXPLORER_HISTORICAL_LEAGUES:
            raise ValueError(f"Unsupported Betexplorer league key: {league_key}")
        self.league_key = league_key
        self.competition_name, self.results_path = BETEXPLORER_HISTORICAL_LEAGUES[league_key]
        self.date_from = date_from
        self.date_to = date_to
        self.fetcher = fetcher
        self.dry_run = dry_run
        self.reference_date = reference_date or datetime.now(timezone.utc).date()
        self._client: Optional[_BetexplorerHistoryClient] = None

    async def _load_candidate_matches(self) -> list[dict]:
        aliases = BETEXPLORER_COMPETITION_ALIASES.get(
            self.league_key,
            (self.competition_name,),
        )
        seen_ids: set[int] = set()
        candidates: list[dict] = []

        for alias in aliases:
            rows = await db.get_historical_matches_for_range(
                self.date_from.isoformat(),
                self.date_to.isoformat(),
                competition_name=alias,
            )
            for row in rows:
                row_id = int(row["id"])
                if row_id in seen_ids:
                    continue
                seen_ids.add(row_id)
                candidates.append(row)

        if candidates:
            return candidates

        # Fallback to date-range-only candidate selection when provider naming differs.
        return await db.get_historical_matches_for_range(
            self.date_from.isoformat(),
            self.date_to.isoformat(),
        )

    async def _ensure_client(self) -> _BetexplorerHistoryClient:
        if self._client is None:
            self._client = _BetexplorerHistoryClient("historical", self.competition_name)
        return self._client

    async def _fetch_html(self, url: str, *, headers: Optional[dict] = None, json_mode: bool = False) -> str:
        if self.fetcher is not None:
            return await self.fetcher(url)
        client = await self._ensure_client()
        return await client._fetch_with_retry(url, headers=headers, json_mode=json_mode)

    async def _fetch_results_rows(self) -> list[dict]:
        results_paths = _build_results_paths_for_range(
            self.results_path,
            date_from=self.date_from,
            date_to=self.date_to,
            reference_date=self.reference_date,
        )
        html_pages = await asyncio.gather(
            *[
                self._fetch_html(urljoin(BASE_URL, path), headers=_BE_HEADERS)
                for path in results_paths
            ]
        )

        filtered_rows: list[dict] = []
        seen_keys: set[tuple[str, str, int, int]] = set()
        for html in html_pages:
            rows = parse_results_page(html, reference_date=self.reference_date)
            for row in rows:
                if not (self.date_from.isoformat() <= row["match_date_utc"] <= self.date_to.isoformat()):
                    continue
                row_key = (
                    str(row.get("href") or ""),
                    str(row.get("match_date_utc") or ""),
                    int(row.get("score_home") or 0),
                    int(row.get("score_away") or 0),
                )
                if row_key in seen_keys:
                    continue
                seen_keys.add(row_key)
                filtered_rows.append(row)
        return filtered_rows

    async def _write_coverage_status(
        self,
        historical_match_id: int,
        *,
        status: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if self.dry_run:
            return
        await db.upsert_historical_provider_coverage(
            {
                "historical_match_id": historical_match_id,
                "source_key": "betexplorer_historical_close",
                "source_label": "Betexplorer historical close",
                "status": status,
                "error_type": error_type,
                "error_message": error_message,
                "duration_ms": None,
                "provider_name": "betexplorer",
            }
        )

    async def _fetch_market_payload(self, historical_match_id: int, result_row: dict) -> Optional[dict]:
        match_url = str(result_row.get("href") or "")
        match_id_match = re.search(r"/([A-Za-z0-9]+)/?$", match_url.rstrip("/"))
        if not match_id_match:
            return None
        match_id = match_id_match.group(1)

        async def _fetch_odds_fragment(bet_type: str) -> str:
            url = f"{BASE_URL}/match-odds/{match_id}/0/{bet_type}/bestOdds/?lang=en"
            raw = await self._fetch_html(
                url,
                headers={**_ODDS_HEADERS, "Referer": match_url},
                json_mode=True,
            )
            try:
                return str((json.loads(raw) or {}).get("odds") or "")
            except (json.JSONDecodeError, TypeError, AttributeError):
                return ""

        html_ou, html_bts, html_ah = await asyncio.gather(
            _fetch_odds_fragment("ou"),
            _fetch_odds_fragment("bts"),
            _fetch_odds_fragment("ah"),
            return_exceptions=True,
        )

        payload = {
            "historical_match_id": historical_match_id,
            "snapshot_kind": "CLOSE",
            "captured_at": f"{result_row['match_date_utc']}T23:59:59+00:00",
            "bookmaker_count": 1,
            "source_provider": "betexplorer:historical-close",
            "odds_w1": result_row.get("odds_w1"),
            "odds_draw": result_row.get("odds_draw"),
            "odds_w2": result_row.get("odds_w2"),
            "consensus_favourite": (
                result_row["home"]
                if result_row.get("odds_w1") and result_row.get("odds_w2")
                and float(result_row["odds_w1"]) < float(result_row["odds_w2"])
                else None
            ),
        }
        _assign_derived_markets(payload)

        if not isinstance(html_ou, Exception):
            odds_1_5 = OddsportalScraper._extract_total_line_odds(html_ou, 1.5)
            odds_2_5 = OddsportalScraper._extract_total_line_odds(html_ou, 2.5)
            odds_3_5 = OddsportalScraper._extract_total_line_odds(html_ou, 3.5)
            payload["odds_over_1_5"] = odds_1_5.get("over")
            payload["odds_under_1_5"] = odds_1_5.get("under")
            payload["odds_over_2_5"] = odds_2_5.get("over")
            payload["odds_under_2_5"] = odds_2_5.get("under")
            payload["odds_over_3_5"] = odds_3_5.get("over")
            payload["odds_under_3_5"] = odds_3_5.get("under")

        if not isinstance(html_bts, Exception):
            odds_bts = OddsportalScraper._extract_best_odds(html_bts, "bts")
            payload["odds_btts_yes"] = odds_bts.get("btts_yes")
            payload["odds_btts_no"] = odds_bts.get("btts_no")

        if not isinstance(html_ah, Exception):
            ah = OddsportalScraper._extract_best_ah_odds(html_ah)
            if ah:
                sign = "+" if ah["line"] > 0 else ""
                payload["ah_main_line"] = f"{result_row['home']} {sign}{ah['line']}"
                payload["ah_team1_odds"] = ah.get("home")
                payload["ah_team2_odds"] = ah.get("away")

        if not _has_market_data(payload):
            return None
        return payload

    async def _fetch_market_payload_with_limit(
        self,
        match_row: dict,
        result_row: dict,
        semaphore: asyncio.Semaphore,
    ) -> tuple[dict, Optional[dict], Optional[str]]:
        async with semaphore:
            try:
                payload = await self._fetch_market_payload(int(match_row["id"]), result_row)
            except Exception:
                logger.exception(
                    "Betexplorer historical market fetch failed for match id=%s",
                    match_row.get("id"),
                )
                return match_row, None, "ODDS_FETCH_FAILED"
        if payload is None:
            return match_row, None, "NO_MARKET_DATA"
        return match_row, payload, None

    async def run(self) -> EnrichmentSummary:
        run_id: Optional[int] = None
        summary = EnrichmentSummary(
            league_key=self.league_key,
            competition_name=self.competition_name,
            requested_from=self.date_from.isoformat(),
            requested_to=self.date_to.isoformat(),
            run_id=None,
        )

        if not self.dry_run:
            run_id = await db.start_historical_import_run(
                "MARKET_ENRICHMENT",
                "betexplorer",
                season_label=None,
                competition_name=self.competition_name,
                requested_from=self.date_from.isoformat(),
                requested_to=self.date_to.isoformat(),
            )
            summary.run_id = run_id

        try:
            candidate_matches = await self._load_candidate_matches()
            summary.candidate_matches = len(candidate_matches)
            result_rows = await self._fetch_results_rows()
            semaphore = asyncio.Semaphore(_ENRICHMENT_FETCH_CONCURRENCY)
            fetch_tasks: list[asyncio.Task] = []

            for match_row in candidate_matches:
                matched = next((row for row in result_rows if _historical_row_matches(match_row, row)), None)
                if matched is None:
                    summary.unmatched_rows += 1
                    await self._write_coverage_status(
                        int(match_row["id"]),
                        status="UNSUPPORTED",
                        error_type="NO_RESULT_ROW",
                        error_message="Betexplorer results page did not contain a matching finished row.",
                    )
                    continue
                summary.matched_rows += 1
                fetch_tasks.append(
                    asyncio.create_task(
                        self._fetch_market_payload_with_limit(match_row, matched, semaphore)
                    )
                )

            fetch_results = await asyncio.gather(*fetch_tasks) if fetch_tasks else []
            for match_row, payload, error_type in fetch_results:
                if error_type is not None:
                    summary.odds_fetch_failures += 1
                    await self._write_coverage_status(
                        int(match_row["id"]),
                        status="FAILED",
                        error_type=error_type,
                        error_message=(
                            "Betexplorer market enrichment fetch raised an exception."
                            if error_type == "ODDS_FETCH_FAILED"
                            else "Betexplorer did not return usable market odds for this match."
                        ),
                    )
                    continue

                existing_lines = [] if self.dry_run else await db.get_historical_market_lines(int(match_row["id"]))
                had_existing = any(
                    line.get("snapshot_kind") == "CLOSE"
                    and line.get("source_provider") == payload["source_provider"]
                    for line in existing_lines
                )
                if not self.dry_run:
                    await db.upsert_historical_market_line(payload)
                if had_existing:
                    summary.market_rows_updated += 1
                else:
                    summary.market_rows_inserted += 1
                await self._write_coverage_status(
                    int(match_row["id"]),
                    status="OK",
                )

            if run_id is not None:
                await db.finish_historical_import_run(
                    run_id,
                    status="SUCCESS",
                    records_inserted=summary.market_rows_inserted,
                    records_updated=summary.market_rows_updated,
                    records_failed=summary.unmatched_rows + summary.odds_fetch_failures,
                    error_summary=None,
                )
            return summary
        except Exception as exc:
            if run_id is not None:
                await db.finish_historical_import_run(
                    run_id,
                    status="FAILED",
                    records_inserted=summary.market_rows_inserted,
                    records_updated=summary.market_rows_updated,
                    records_failed=max(summary.unmatched_rows + summary.odds_fetch_failures, 1),
                    error_summary=str(exc),
                )
            raise
        finally:
            if self._client is not None:
                await self._client._close()
