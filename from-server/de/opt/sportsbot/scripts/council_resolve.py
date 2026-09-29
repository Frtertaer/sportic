#!/usr/bin/env python3
"""Resolve designated four-AI paper-council forecasts by exact ESPN event id."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_council.store import (
    connect,
    mark_resolution_status,
    pending_designated_runs,
    recent_designated_runs,
    save_match_result,
    store_status,
)


DEFAULT_DB_PATH = Path(
    os.getenv("COUNCIL_DB_PATH", "/var/lib/sportsbot-paper-council/council.sqlite3")
)
CORRECTION_LOOKBACK_DAYS = 3
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/"
    "{league_code}/scoreboard"
)
SPORT_KEY_TO_ESPN_LEAGUE = {
    "soccer_brazil_campeonato": "bra.1",
    "soccer_finland_veikkausliiga": "fin.1",
    "soccer_norway_eliteserien": "nor.1",
    "soccer_sweden_allsvenskan": "swe.1",
}
REGULATION_LEAGUE_KEYS = frozenset(SPORT_KEY_TO_ESPN_LEAGUE)
_ESPN_EVENT_ID_RE = re.compile(r"^espn:([a-z]{3}\.[1-9][0-9]*):([1-9][0-9]*)$")
_STRICT_SCORE_RE = re.compile(r"^[0-9]+$")
_SPECIAL_LATIN_FOLD = str.maketrans(
    {
        "æ": "ae",
        "ð": "d",
        "đ": "d",
        "ł": "l",
        "ø": "o",
        "œ": "oe",
        "ß": "ss",
        "þ": "th",
    }
)


class ScoresFetchError(RuntimeError):
    """An intentionally sanitised provider error safe for service logs."""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalise_team(value: str) -> str:
    """Fold presentation-only differences without weakening team identity."""

    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.translate(_SPECIAL_LATIN_FOLD)
    return "".join(char for char in text if char.isalnum())


def parse_espn_event_id(value: object) -> tuple[str, str] | None:
    match = _ESPN_EVENT_ID_RE.fullmatch(str(value or ""))
    if match is None:
        return None
    return match.group(1), match.group(2)


def _parse_utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _safe_request_id(response: httpx.Response) -> str | None:
    raw = response.headers.get("x-request-id") or response.headers.get("cf-ray")
    if not raw:
        return None
    return re.sub(r"[^A-Za-z0-9._:-]", "?", str(raw))[:80]


def _safe_http_status(response: httpx.Response, league_code: str) -> None:
    if 200 <= response.status_code < 300:
        return
    request_id = _safe_request_id(response)
    request_suffix = f", request_id={request_id}" if request_id else ""
    raise ScoresFetchError(
        f"ESPN scoreboard request failed for league={league_code}: "
        f"HTTP {response.status_code}{request_suffix}"
    )


def _fetch_window(
    runs: list[Mapping[str, Any]], *, now: datetime
) -> tuple[date, date]:
    """Cover each candidate's UTC kickoff +/- one day and the correction window."""

    dates = {
        (now - timedelta(days=offset)).date()
        for offset in range(CORRECTION_LOOKBACK_DAYS + 1)
    }
    for run in runs:
        kickoff = _parse_utc(run.get("kickoff_utc"))
        if kickoff is None:
            continue
        dates.update(
            {
                (kickoff - timedelta(days=1)).date(),
                kickoff.date(),
                (kickoff + timedelta(days=1)).date(),
            }
        )
    return min(dates), max(dates)


def _response_has_exact_league(payload: Mapping[str, Any], league_code: str) -> bool:
    leagues = payload.get("leagues")
    if not isinstance(leagues, list):
        return False
    slugs = {
        str(item.get("slug"))
        for item in leagues
        if isinstance(item, Mapping) and item.get("slug")
    }
    return slugs == {league_code}


async def fetch_scores(
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch bounded ESPN scoreboards and index events by composite council id."""

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    by_league: dict[str, list[Mapping[str, Any]]] = {}
    requested_ids: dict[str, set[str]] = {}
    for event_id, run in candidates.items():
        parsed = parse_espn_event_id(event_id)
        if parsed is None:
            continue
        league_code, provider_event_id = parsed
        by_league.setdefault(league_code, []).append(run)
        requested_ids.setdefault(league_code, set()).add(provider_event_id)

    events_by_id: dict[str, dict[str, Any]] = {}
    headers = {
        "Accept": "application/json",
        "User-Agent": "sportsbot-paper-council-result-resolver/2.0",
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), headers=headers
    ) as client:
        for league_code in sorted(by_league):
            start_day, end_day = _fetch_window(by_league[league_code], now=now)
            date_range = f"{start_day:%Y%m%d}-{end_day:%Y%m%d}"
            try:
                response = await client.get(
                    ESPN_SCOREBOARD_URL.format(league_code=league_code),
                    params={"dates": date_range, "limit": 100},
                )
            except httpx.TimeoutException:
                raise ScoresFetchError(
                    f"ESPN scoreboard request timed out for league={league_code}"
                ) from None
            except httpx.RequestError:
                raise ScoresFetchError(
                    f"ESPN scoreboard request failed for league={league_code}: network error"
                ) from None
            _safe_http_status(response, league_code)
            try:
                payload = response.json()
            except ValueError:
                raise ScoresFetchError(
                    f"ESPN scoreboard returned invalid JSON for league={league_code}"
                ) from None
            if not isinstance(payload, dict) or not _response_has_exact_league(
                payload, league_code
            ):
                raise ScoresFetchError(
                    f"ESPN scoreboard league provenance mismatch for league={league_code}"
                )
            raw_events = payload.get("events")
            if not isinstance(raw_events, list):
                raise ScoresFetchError(
                    f"ESPN scoreboard returned unexpected events shape for league={league_code}"
                )
            for event in raw_events:
                if not isinstance(event, dict):
                    continue
                provider_event_id = str(event.get("id") or "")
                if provider_event_id not in requested_ids[league_code]:
                    continue
                composite_id = f"espn:{league_code}:{provider_event_id}"
                if composite_id in events_by_id:
                    raise ScoresFetchError(
                        f"ESPN scoreboard returned duplicate event id for league={league_code}"
                    )
                events_by_id[composite_id] = event
    return events_by_id


def _status_is_full_time(container: Mapping[str, Any]) -> bool:
    status = container.get("status")
    status_type = status.get("type") if isinstance(status, Mapping) else None
    return bool(
        isinstance(status_type, Mapping)
        and status_type.get("name") == "STATUS_FULL_TIME"
        and status_type.get("completed") is True
    )


def _team_aliases(competitor: Mapping[str, Any]) -> set[str]:
    team = competitor.get("team")
    if not isinstance(team, Mapping):
        return set()
    aliases = {
        normalise_team(team.get(field))
        for field in ("displayName", "name", "shortDisplayName", "location")
    }
    aliases.discard("")
    return aliases


def _strict_score(competitor: Mapping[str, Any]) -> int | None:
    value = str(competitor.get("score") or "").strip()
    if not _STRICT_SCORE_RE.fullmatch(value):
        return None
    score = int(value)
    return score if score <= 99 else None


def extract_score(
    event: Mapping[str, Any], team1: str, team2: str
) -> tuple[int, int] | None:
    """Extract a regulation-league final only after an exact oriented team check."""

    if not _status_is_full_time(event):
        return None
    event_id = str(event.get("id") or "")
    competitions = event.get("competitions")
    if not event_id or not isinstance(competitions, list):
        return None
    matching = [
        item
        for item in competitions
        if isinstance(item, Mapping) and str(item.get("id") or "") == event_id
    ]
    if len(matching) != 1 or not _status_is_full_time(matching[0]):
        return None
    competition = matching[0]
    competitors = competition.get("competitors")
    if not isinstance(competitors, list) or len(competitors) != 2:
        return None
    homes = [
        item
        for item in competitors
        if isinstance(item, Mapping) and item.get("homeAway") == "home"
    ]
    aways = [
        item
        for item in competitors
        if isinstance(item, Mapping) and item.get("homeAway") == "away"
    ]
    if len(homes) != 1 or len(aways) != 1:
        return None
    home, away = homes[0], aways[0]
    expected_home, expected_away = normalise_team(team1), normalise_team(team2)
    home_aliases, away_aliases = _team_aliases(home), _team_aliases(away)
    if not expected_home or not expected_away:
        return None
    if expected_home not in home_aliases or expected_away not in away_aliases:
        return None
    # Fail closed when an expected name also identifies the opposite competitor.
    if expected_home in away_aliases or expected_away in home_aliases:
        return None
    home_score, away_score = _strict_score(home), _strict_score(away)
    if home_score is None or away_score is None:
        return None
    return home_score, away_score


def _valid_candidates(
    candidates: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    valid: dict[str, Mapping[str, Any]] = {}
    invalid: dict[str, str] = {}
    for event_id, run in candidates.items():
        sport_key = str(run.get("sport_key") or "")
        expected_league = SPORT_KEY_TO_ESPN_LEAGUE.get(sport_key)
        parsed = parse_espn_event_id(event_id)
        if parsed is None:
            invalid[event_id] = "event id is not a valid ESPN provenance id"
            continue
        if parsed[0] != expected_league:
            invalid[event_id] = "event id league does not match the council sport key"
            continue
        if _parse_utc(run.get("kickoff_utc")) is None:
            invalid[event_id] = "council kickoff is not a valid timezone-aware timestamp"
            continue
        valid[event_id] = run
    return valid, invalid


async def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve four-AI paper council")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if not args.db.is_file():
        print(
            json.dumps(
                {
                    "status": "NO_STORE",
                    "pending": 0,
                    "correction_candidates": 0,
                    "inserted": 0,
                    "updated": 0,
                    "unchanged": 0,
                }
            )
        )
        return

    conn = connect(args.db)
    try:
        now = datetime.now(timezone.utc)
        now_utc = now.isoformat().replace("+00:00", "Z")
        since_utc = (now - timedelta(days=CORRECTION_LOOKBACK_DAYS)).isoformat().replace(
            "+00:00", "Z"
        )
        pending = pending_designated_runs(conn, now_utc=now_utc)
        recent = recent_designated_runs(conn, now_utc=now_utc, since_utc=since_utc)
        expired = [run for run in pending if str(run.get("kickoff_utc") or "") < since_utc]
        recoverable_pending = [run for run in pending if run not in expired]
        for run in expired:
            mark_resolution_status(
                conn,
                event_id=str(run["event_id"]),
                status="UNRESOLVED_EXPIRED",
                detail="provider score window exceeded three days; historical source unavailable",
                attempted_at=now_utc,
            )

        # Recently settled events remain candidates so upstream score corrections are
        # observed through the same exact ESPN event id.
        candidates: dict[str, Mapping[str, Any]] = {
            str(run["event_id"]): run
            for run in (*recoverable_pending, *recent)
            if run.get("event_id") and run.get("sport_key")
        }
        sport_keys = {str(run["sport_key"]) for run in candidates.values()}
        unsafe_keys = sport_keys - REGULATION_LEAGUE_KEYS
        if unsafe_keys:
            raise RuntimeError(
                "refusing settlement without regulation-only league provenance: "
                + ", ".join(sorted(unsafe_keys))
            )
        if not candidates:
            print(
                json.dumps(
                    {
                        "pending": len(pending),
                        "correction_candidates": 0,
                        "unresolved_expired": len(expired),
                        "inserted": 0,
                        "updated": 0,
                        "unchanged": 0,
                        "store": store_status(conn),
                    }
                )
            )
            return

        valid_candidates, invalid_candidates = _valid_candidates(candidates)
        resolved_at = iso_now()
        for event_id, detail in invalid_candidates.items():
            mark_resolution_status(
                conn,
                event_id=event_id,
                status="PENDING_PROVIDER",
                detail=detail,
                attempted_at=resolved_at,
            )

        try:
            scores = await fetch_scores(valid_candidates, now=now)
        except ScoresFetchError as exc:
            print(
                json.dumps(
                    {
                        "error": "scores_fetch_failed",
                        "detail": str(exc),
                        "sport_keys": sorted(sport_keys),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from None

        result_counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        unmatched = list(invalid_candidates)
        for event_id, run in valid_candidates.items():
            event = scores.get(event_id)
            if event is None:
                unmatched.append(event_id)
                mark_resolution_status(
                    conn,
                    event_id=event_id,
                    status="PENDING_PROVIDER",
                    detail="exact ESPN event id absent from bounded league scoreboard",
                    attempted_at=resolved_at,
                )
                continue
            score = extract_score(event, str(run["team1"]), str(run["team2"]))
            if score is None:
                unmatched.append(event_id)
                mark_resolution_status(
                    conn,
                    event_id=event_id,
                    status="PENDING_PROVIDER",
                    detail=(
                        "exact ESPN event is not a full-time final with matching "
                        "home/away teams and integer scores"
                    ),
                    attempted_at=resolved_at,
                )
                continue
            outcome = save_match_result(
                conn,
                event_id=event_id,
                home_goals=score[0],
                away_goals=score[1],
                source="espn:event-id",
                settled_at=resolved_at,
                provider_updated_at=None,
                score_basis="REGULATION_ONLY_LEAGUE_FINAL",
            )
            result_counts[outcome] += 1

        print(
            json.dumps(
                {
                    "pending": len(pending),
                    "unresolved_expired": len(expired),
                    "correction_candidates": len(recent),
                    "candidate_events": len(candidates),
                    "sport_keys": sorted(sport_keys),
                    "provider": "espn",
                    "provider_returned_candidates": len(scores),
                    **result_counts,
                    "newly_settled": result_counts["inserted"],
                    "corrected": result_counts["updated"],
                    "unmatched_event_ids": sorted(unmatched),
                    "store": store_status(conn),
                },
                ensure_ascii=False,
            )
        )
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
