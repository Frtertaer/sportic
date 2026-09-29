from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import database as db
from scrapers.base import BaseScraper
from scrapers.fotmob import ESPN_HEADERS, ESPN_SCHEDULE_LEAGUES, ESPN_SCOREBOARD_URL
from team_parser import normalize_team_name

logger = logging.getLogger(__name__)

ScoreboardFetcher = Callable[[str], Awaitable[dict]]


class _ESPNHistoryClient(BaseScraper):
    DOMAIN = "espn.com"

    async def scrape(self):
        raise NotImplementedError("Historical importer uses only _fetch_with_retry().")


@dataclass(slots=True)
class ImportSummary:
    league_slug: str
    competition_name: str
    requested_from: str
    requested_to: str
    run_id: Optional[int]
    days_processed: int = 0
    match_rows_inserted: int = 0
    match_rows_updated: int = 0
    market_rows_inserted: int = 0
    market_rows_updated: int = 0
    snapshot_rows_inserted: int = 0
    snapshot_rows_updated: int = 0
    failed_days: int = 0

    @property
    def total_records_inserted(self) -> int:
        return self.match_rows_inserted + self.market_rows_inserted + self.snapshot_rows_inserted

    @property
    def total_records_updated(self) -> int:
        return self.match_rows_updated + self.market_rows_updated + self.snapshot_rows_updated


def _daterange(date_from: date, date_to: date) -> list[date]:
    if date_to < date_from:
        raise ValueError("date_to must be >= date_from")
    days = (date_to - date_from).days + 1
    return [date_from + timedelta(days=offset) for offset in range(days)]


def _slugify_provider_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def _american_to_decimal(value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        american = int(value)
    else:
        text = str(value).strip().replace("−", "-")
        if not text:
            return None
        try:
            american = int(text)
        except ValueError:
            return None

    if american == 0:
        return None
    if american > 0:
        return round(1.0 + (american / 100.0), 4)
    return round(1.0 + (100.0 / abs(american)), 4)


def _normalized_probs_from_decimal(*odds: Optional[float]) -> tuple[Optional[float], ...]:
    if any(odd is None or odd <= 1.0 for odd in odds):
        return tuple(None for _ in odds)
    implied = [1.0 / odd for odd in odds]  # type: ignore[arg-type]
    total = sum(implied)
    if total <= 0:
        return tuple(None for _ in odds)
    return tuple(round((part / total) * 100.0, 4) for part in implied)


def _decimal_from_probability_pct(probability_pct: Optional[float]) -> Optional[float]:
    if probability_pct is None or probability_pct <= 0.0:
        return None
    return round(100.0 / probability_pct, 4)


def _assign_derived_moneyline_markets(
    payload: dict,
    *,
    implied_w1: Optional[float],
    implied_draw: Optional[float],
    implied_w2: Optional[float],
) -> None:
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


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _scalar_text(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _get_competitors(competition: dict) -> tuple[Optional[dict], Optional[dict]]:
    competitors = competition.get("competitors") or []
    home = next((item for item in competitors if item.get("homeAway") == "home"), None)
    away = next((item for item in competitors if item.get("homeAway") == "away"), None)
    return home, away


def _score_to_int(competitor: Optional[dict]) -> Optional[int]:
    if competitor is None:
        return None
    score = competitor.get("score")
    if score in (None, ""):
        return None
    try:
        return int(score)
    except (TypeError, ValueError):
        return None


def _compute_actual_result(team1_score: Optional[int], team2_score: Optional[int]) -> Optional[str]:
    if team1_score is None or team2_score is None:
        return None
    if team1_score > team2_score:
        return "W1"
    if team1_score < team2_score:
        return "W2"
    return "DRAW"


def _parse_form_points(form: object) -> Optional[float]:
    if not form:
        return None
    total = 0.0
    seen = False
    for ch in str(form).upper():
        if ch == "W":
            total += 3.0
            seen = True
        elif ch == "D":
            total += 1.0
            seen = True
        elif ch == "L":
            seen = True
    return total if seen else None


def _parse_record_points(summary: object) -> Optional[int]:
    if not summary:
        return None
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*$", str(summary))
    if not match:
        return None
    wins, draws, _losses = (int(match.group(i)) for i in range(1, 4))
    return wins * 3 + draws


def _status_completed(competition: dict) -> bool:
    status = (competition.get("status") or {}).get("type") or {}
    return bool(status.get("completed"))


def _status_detail(competition: dict) -> str:
    status = (competition.get("status") or {}).get("type") or {}
    return str(status.get("shortDetail") or status.get("detail") or status.get("description") or "")


def _extract_total_market(total_payload: dict, snapshot_kind: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    over_data = (total_payload.get("over") or {}).get(snapshot_kind.lower()) or {}
    under_data = (total_payload.get("under") or {}).get(snapshot_kind.lower()) or {}
    over_line = over_data.get("line") or under_data.get("line")
    match = re.search(r"([ou])\s*([0-9]+(?:\.[0-9]+)?)", str(over_line).lower())
    line = float(match.group(2)) if match else None
    return (
        line,
        _american_to_decimal(over_data.get("odds")),
        _american_to_decimal(under_data.get("odds")),
    )


def _assign_total_fields(payload: dict, line: Optional[float], over_odds: Optional[float], under_odds: Optional[float]) -> None:
    if line is None:
        return
    if abs(line - 1.5) < 0.01:
        payload["odds_over_1_5"] = over_odds
        payload["odds_under_1_5"] = under_odds
    elif abs(line - 2.5) < 0.01:
        payload["odds_over_2_5"] = over_odds
        payload["odds_under_2_5"] = under_odds
    elif abs(line - 3.5) < 0.01:
        payload["odds_over_3_5"] = over_odds
        payload["odds_under_3_5"] = under_odds


def _build_market_line_payloads(event: dict, competition: dict) -> list[dict]:
    team1, team2 = _get_competitors(competition)
    team1_name = ((team1 or {}).get("team") or {}).get("displayName") or "Home"
    odds_entries = competition.get("odds") or []
    payloads: list[dict] = []

    for odds_entry in odds_entries:
        if not isinstance(odds_entry, dict):
            continue
        provider_name = ((odds_entry.get("provider") or {}).get("displayName") or "unknown").strip()
        source_provider = f"espn:{_slugify_provider_name(provider_name)}"
        moneyline = odds_entry.get("moneyline") or {}
        point_spread = odds_entry.get("pointSpread") or {}
        total_market = odds_entry.get("total") or {}

        for snapshot_kind in ("OPEN", "CLOSE"):
            ml_home = _american_to_decimal(((moneyline.get("home") or {}).get(snapshot_kind.lower()) or {}).get("odds"))
            ml_draw = _american_to_decimal(((moneyline.get("draw") or {}).get(snapshot_kind.lower()) or {}).get("odds"))
            ml_away = _american_to_decimal(((moneyline.get("away") or {}).get(snapshot_kind.lower()) or {}).get("odds"))
            implied = _normalized_probs_from_decimal(ml_home, ml_draw, ml_away)

            payload = {
                "historical_match_id": None,
                "snapshot_kind": snapshot_kind,
                "captured_at": competition.get("date") or event.get("date"),
                "bookmaker_count": 1,
                "source_provider": source_provider,
                "odds_w1": ml_home,
                "odds_draw": ml_draw,
                "odds_w2": ml_away,
                "implied_prob_w1": implied[0],
                "implied_prob_draw": implied[1],
                "implied_prob_w2": implied[2],
                "consensus_favourite": team1_name if (ml_home and ml_away and ml_home < ml_away) else None,
            }
            _assign_derived_moneyline_markets(
                payload,
                implied_w1=implied[0],
                implied_draw=implied[1],
                implied_w2=implied[2],
            )

            line, over_odds, under_odds = _extract_total_market(total_market, snapshot_kind)
            _assign_total_fields(payload, line, over_odds, under_odds)

            home_spread = ((point_spread.get("home") or {}).get(snapshot_kind.lower()) or {})
            away_spread = ((point_spread.get("away") or {}).get(snapshot_kind.lower()) or {})
            home_line = home_spread.get("line")
            if home_line not in (None, ""):
                payload["ah_main_line"] = f"{team1_name} {home_line}"
            payload["ah_team1_odds"] = _american_to_decimal(home_spread.get("odds"))
            payload["ah_team2_odds"] = _american_to_decimal(away_spread.get("odds"))

            open_home = _american_to_decimal(((moneyline.get("home") or {}).get("open") or {}).get("odds"))
            close_home = _american_to_decimal(((moneyline.get("home") or {}).get("close") or {}).get("odds"))
            open_away = _american_to_decimal(((moneyline.get("away") or {}).get("open") or {}).get("odds"))
            close_away = _american_to_decimal(((moneyline.get("away") or {}).get("close") or {}).get("odds"))
            if open_home and close_home:
                payload["movement_score_team1"] = round((1.0 / close_home) - (1.0 / open_home), 6)
            if open_away and close_away:
                payload["movement_score_team2"] = round((1.0 / close_away) - (1.0 / open_away), 6)

            payloads.append(payload)

    return payloads


def build_match_payload(event: dict, *, competition_name: str) -> Optional[dict]:
    competition = (event.get("competitions") or [{}])[0]
    home, away = _get_competitors(competition)
    if not home or not away:
        return None

    team1_name = (((home.get("team") or {}).get("displayName")) or "").strip()
    team2_name = (((away.get("team") or {}).get("displayName")) or "").strip()
    if not team1_name or not team2_name:
        return None

    kickoff = competition.get("date") or event.get("date")
    kickoff_dt = _parse_iso_datetime(kickoff)
    team1_score = _score_to_int(home)
    team2_score = _score_to_int(away)
    status_detail = _status_detail(competition).upper()

    return {
        "provider_match_key": str(event.get("id") or event.get("uid") or ""),
        "provider_name": "espn",
        "competition_name": competition_name,
        "competition_country": ((event.get("season") or {}).get("slug") or "").split("-")[-1] or None,
        "season_label": str((event.get("season") or {}).get("year") or "") or None,
        "stage_name": _scalar_text(competition.get("details")),
        "match_date_utc": kickoff_dt.date().isoformat() if kickoff_dt else None,
        "kickoff_utc": kickoff_dt.isoformat() if kickoff_dt else kickoff,
        "team1_name": team1_name,
        "team2_name": team2_name,
        "team1_normalized": normalize_team_name(team1_name).lower(),
        "team2_normalized": normalize_team_name(team2_name).lower(),
        "is_neutral_venue": competition.get("neutralSite"),
        "venue_name": ((competition.get("venue") or {}).get("fullName") or (event.get("venue") or {}).get("fullName")),
        "team1_score": team1_score,
        "team2_score": team2_score,
        "actual_result": _compute_actual_result(team1_score, team2_score),
        "went_extra_time": "AET" in status_detail or "ET" in status_detail,
        "went_penalties": "PEN" in status_detail,
        "status": "FINISHED" if _status_completed(competition) else "SCHEDULED",
    }


def build_team_snapshot_payload(event: dict, *, competition_name: str) -> Optional[dict]:
    competition = (event.get("competitions") or [{}])[0]
    home, away = _get_competitors(competition)
    if not home or not away:
        return None

    home_form_points = _parse_form_points(home.get("form"))
    away_form_points = _parse_form_points(away.get("form"))
    home_record_summary = next(
        (
            record.get("summary")
            for record in (home.get("records") or [])
            if (record.get("type") or "").lower() == "total"
        ),
        None,
    )
    away_record_summary = next(
        (
            record.get("summary")
            for record in (away.get("records") or [])
            if (record.get("type") or "").lower() == "total"
        ),
        None,
    )
    home_points = _parse_record_points(home_record_summary)
    away_points = _parse_record_points(away_record_summary)

    available_groups = 0
    if home_form_points is not None and away_form_points is not None:
        available_groups += 1
    if home_points is not None and away_points is not None:
        available_groups += 1
    if competition_name:
        available_groups += 1

    if available_groups == 0:
        return None

    return {
        "historical_match_id": None,
        "snapshot_source": "espn_scoreboard_basic",
        "captured_at": competition.get("date") or event.get("date"),
        "data_completeness": round(available_groups / 3.0, 4),
        "successful_source_count": 1,
        "total_source_count": 1,
        "team1_form_points_last5": home_form_points,
        "team2_form_points_last5": away_form_points,
        "team1_points": home_points,
        "team2_points": away_points,
        "competition_name": competition_name,
        "schedule_density_note": "ESPN scoreboard basic snapshot: form string and total record only.",
    }


class ESPNHistoricalImporter:
    def __init__(
        self,
        *,
        league_slug: str,
        date_from: date,
        date_to: date,
        fetcher: Optional[ScoreboardFetcher] = None,
        dry_run: bool = False,
    ) -> None:
        self.league_slug = league_slug
        self.date_from = date_from
        self.date_to = date_to
        self.fetcher = fetcher
        self.dry_run = dry_run
        self.competition_name = ESPN_SCHEDULE_LEAGUES.get(league_slug, (league_slug, ""))[0]
        self._client: Optional[_ESPNHistoryClient] = None

    async def _ensure_client(self) -> _ESPNHistoryClient:
        if self._client is None:
            self._client = _ESPNHistoryClient("historical", self.competition_name)
        return self._client

    async def _fetch_scoreboard_json(self, day: date) -> dict:
        if self.fetcher is not None:
            return await self.fetcher(day.strftime("%Y%m%d"))
        client = await self._ensure_client()
        raw = await client._fetch_with_retry(
            ESPN_SCOREBOARD_URL.format(league_slug=self.league_slug),
            params={"dates": day.strftime("%Y%m%d")},
            headers=ESPN_HEADERS,
            json_mode=True,
        )
        return json.loads(raw)

    async def _import_day(self, day: date, summary: ImportSummary) -> None:
        payload = await self._fetch_scoreboard_json(day)
        league_name = ((payload.get("leagues") or [{}])[0].get("name") or self.competition_name).strip() or self.competition_name

        for event in payload.get("events", []):
            try:
                competition = (event.get("competitions") or [{}])[0]
                if not _status_completed(competition):
                    continue

                match_payload = build_match_payload(event, competition_name=league_name)
                if match_payload is None or not match_payload.get("provider_match_key"):
                    continue

                existing = await db.get_historical_match_by_provider_key("espn", match_payload["provider_match_key"])
                if not self.dry_run:
                    match_id = await db.upsert_historical_match(match_payload)
                else:
                    match_id = existing["id"] if existing else 0
                if existing is None:
                    summary.match_rows_inserted += 1
                else:
                    summary.match_rows_updated += 1

                snapshot_payload = build_team_snapshot_payload(event, competition_name=league_name)
                if snapshot_payload is not None:
                    snapshot_payload["historical_match_id"] = match_id
                    had_existing_snapshot = False
                    if not self.dry_run and match_id:
                        had_existing_snapshot = await db.get_historical_team_snapshot(match_id) is not None
                        await db.upsert_historical_team_snapshot(snapshot_payload)
                    if had_existing_snapshot:
                        summary.snapshot_rows_updated += 1
                    else:
                        summary.snapshot_rows_inserted += 1

                for line_payload in _build_market_line_payloads(event, competition):
                    if not any(
                        line_payload.get(key)
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
                            "ah_team1_odds",
                            "ah_team2_odds",
                        )
                    ):
                        continue
                    line_payload["historical_match_id"] = match_id
                    had_existing = False
                    if not self.dry_run and match_id:
                        existing_lines = await db.get_historical_market_lines(match_id)
                        had_existing = any(
                            row.get("snapshot_kind") == line_payload["snapshot_kind"]
                            and row.get("source_provider") == line_payload["source_provider"]
                            for row in existing_lines
                        )
                        await db.upsert_historical_market_line(line_payload)
                    if had_existing:
                        summary.market_rows_updated += 1
                    else:
                        summary.market_rows_inserted += 1
            except Exception:
                logger.exception(
                    "Historical ESPN event import failed for %s on %s (event=%s)",
                    self.league_slug,
                    day,
                    event.get("id"),
                )

    async def run(self) -> ImportSummary:
        run_id: Optional[int] = None
        summary = ImportSummary(
            league_slug=self.league_slug,
            competition_name=self.competition_name,
            requested_from=self.date_from.isoformat(),
            requested_to=self.date_to.isoformat(),
            run_id=None,
        )

        if not self.dry_run:
            run_id = await db.start_historical_import_run(
                "BACKFILL",
                "espn",
                season_label=None,
                competition_name=self.competition_name,
                requested_from=self.date_from.isoformat(),
                requested_to=self.date_to.isoformat(),
            )
            summary.run_id = run_id

        try:
            for day in _daterange(self.date_from, self.date_to):
                try:
                    await self._import_day(day, summary)
                    summary.days_processed += 1
                except Exception:
                    logger.exception("Historical ESPN import failed for %s %s", self.league_slug, day)
                    summary.failed_days += 1

            if run_id is not None:
                await db.finish_historical_import_run(
                    run_id,
                    status="SUCCESS" if summary.failed_days == 0 else "PARTIAL",
                    records_inserted=summary.total_records_inserted,
                    records_updated=summary.total_records_updated,
                    records_failed=summary.failed_days,
                    error_summary=None if summary.failed_days == 0 else f"{summary.failed_days} day(s) failed during import",
                )
            return summary
        except Exception as exc:
            if run_id is not None:
                await db.finish_historical_import_run(
                    run_id,
                    status="FAILED",
                    records_inserted=summary.total_records_inserted,
                    records_updated=summary.total_records_updated,
                    records_failed=max(summary.failed_days, 1),
                    error_summary=str(exc),
                )
            raise
        finally:
            if self._client is not None:
                await self._client._close()
