#!/usr/bin/env python3
"""Prospective four-AI football council, isolated from live recommendations.

The script scrapes one frozen, non-AI context, asks four distinct OmniRoute
workers for independent forecasts, runs one anonymous revision round, and saves
both consensuses to a separate SQLite database.  It never writes sportsbot.db or
sends Telegram notifications.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
import scrapers as scraper_module
from analyzer import _build_data_context, _format_numeric_baseline_block
from baseline_model import build_numeric_baseline, extract_feature_snapshot
from paper_council.core import (
    COUNCIL_MEMBERS,
    EXPERIMENT_VERSION,
    INPUT_BUILDER_VERSION,
    INTENDED_MODEL_ROSTER,
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    run_four_ai_council,
)
from paper_council.winline import WINLINE_SOURCE_URL, load_snapshot, match_espn_event
from paper_council.store import (
    connect,
    connect_readonly,
    finalize_council_run,
    has_designated_event,
    mark_council_run_failed,
    reconcile_stale_running,
    reserve_council_run,
    store_status,
)


logger = logging.getLogger("paper_council")
DEFAULT_DB_PATH = Path(os.getenv("COUNCIL_DB_PATH", "/var/lib/sportsbot-paper-council/council.sqlite3"))
HEALTH_URL = os.getenv("COUNCIL_HEALTH_URL", "http://127.0.0.1:8906/health")
OMNIROUTE_INTERNAL_URL = os.getenv(
    "COUNCIL_OMNIROUTE_URL", "http://127.0.0.1:20130"
).rstrip("/")
REGULATION_LEAGUE_KEYS = frozenset(
    {
        "soccer_brazil_campeonato",
        "soccer_brazil_serie_b",
        "soccer_finland_veikkausliiga",
        "soccer_norway_eliteserien",
        "soccer_sweden_allsvenskan",
    }
)
SPORT_KEYS = tuple(
    item.strip()
    for item in os.getenv(
        "COUNCIL_SPORT_KEYS", "soccer_brazil_campeonato"
    ).split(",")
    if item.strip()
)
UNSAFE_SPORT_KEYS = set(SPORT_KEYS) - REGULATION_LEAGUE_KEYS
if UNSAFE_SPORT_KEYS:
    raise RuntimeError(
        "paper council accepts regulation-only league keys; rejected: "
        + ", ".join(sorted(UNSAFE_SPORT_KEYS))
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


ESPN_LEAGUES = {
    "soccer_brazil_campeonato": "bra.1",
    "soccer_finland_veikkausliiga": "fin.1",
    "soccer_norway_eliteserien": "nor.1",
    "soccer_sweden_allsvenskan": "swe.1",
}
COMPETITION_NAMES = {
    "soccer_brazil_campeonato": "Brazil Serie A",
    "soccer_finland_veikkausliiga": "Finland Veikkausliiga",
    "soccer_norway_eliteserien": "Norway Eliteserien",
    "soccer_sweden_allsvenskan": "Sweden Allsvenskan",
}
if set(SPORT_KEYS) - set(ESPN_LEAGUES):
    raise RuntimeError(
        "paper council has no exact ESPN resolver mapping for: "
        + ", ".join(sorted(set(SPORT_KEYS) - set(ESPN_LEAGUES)))
    )


def _safe_http_status(response: httpx.Response, label: str) -> None:
    if response.is_error:
        raise RuntimeError(f"{label} returned HTTP {response.status_code}")


async def _safe_provider_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str],
    label: str,
) -> httpx.Response:
    try:
        response = await client.get(url, params=params)
    except httpx.TimeoutException:
        raise RuntimeError(f"{label} timed out") from None
    except httpx.RequestError:
        raise RuntimeError(f"{label} network error") from None
    _safe_http_status(response, label)
    return response


def _espn_fixture(raw: dict[str, Any], *, sport_key: str, league: str) -> dict[str, Any] | None:
    competitions = raw.get("competitions") or []
    competition = competitions[0] if competitions else {}
    competitors = competition.get("competitors") or []
    by_side = {
        str(item.get("homeAway")): str((item.get("team") or {}).get("displayName") or "")
        for item in competitors
        if isinstance(item, dict)
    }
    event_id = str(raw.get("id") or "")
    kickoff = str(raw.get("date") or competition.get("date") or "")
    home, away = by_side.get("home", "").strip(), by_side.get("away", "").strip()
    if not event_id or not kickoff or not home or not away:
        return None
    return {
        "event_id": event_id,
        "league": league,
        "sport_key": sport_key,
        "home_team": home,
        "away_team": away,
        "commence_time": kickoff,
    }


async def fetch_upcoming_events() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join free ESPN fixtures to a fresh, local Winline price snapshot."""
    snapshot_path = Path(
        os.getenv("COUNCIL_WINLINE_SNAPSHOT", "/opt/sportsbot/data/football_winline.json")
    )
    max_age = max(60, int(os.getenv("COUNCIL_WINLINE_MAX_AGE_SECONDS", "900")))
    snapshot = load_snapshot(snapshot_path, now=utc_now(), max_age_seconds=max_age)
    winline_events = snapshot["events"]
    out: list[dict[str, Any]] = []
    matched_winline_ids: set[str] = set()
    seen_espn: set[tuple[str, str]] = set()
    today = utc_now().date()
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for sport_key in SPORT_KEYS:
            league = ESPN_LEAGUES[sport_key]
            for offset in range(4):
                day = (today + timedelta(days=offset)).strftime("%Y%m%d")
                response = await _safe_provider_get(
                    client,
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard",
                    params={"dates": day, "limit": "100"},
                    label=f"ESPN {league} scoreboard",
                )
                payload = response.json()
                for raw in payload.get("events") or []:
                    fixture = _espn_fixture(raw, sport_key=sport_key, league=league)
                    if fixture is None:
                        continue
                    fixture_key = (league, fixture["event_id"])
                    if fixture_key in seen_espn:
                        continue
                    seen_espn.add(fixture_key)
                    price = match_espn_event(fixture, winline_events)
                    if price is None:
                        continue
                    winline_id = str(price["price_event_id"])
                    if winline_id in matched_winline_ids:
                        raise RuntimeError("one Winline event matched more than one ESPN fixture")
                    matched_winline_ids.add(winline_id)
                    benchmark = price["benchmark_probabilities"]
                    out.append(
                        {
                            "event_id": f"espn:{league}:{fixture['event_id']}",
                            "provider_event_id": winline_id,
                            "sport_key": sport_key,
                            "competition": COMPETITION_NAMES[sport_key],
                            "home_team": fixture["home_team"],
                            "away_team": fixture["away_team"],
                            "commence_time": fixture["commence_time"],
                            "captured_at": snapshot["captured_at"],
                            "pricing_source": "winline-public-prematch",
                            "n_books": 1,
                            "h2h": {
                                "home": price["h2h"]["home"],
                                "draw": price["h2h"]["draw"],
                                "away": price["h2h"]["away"],
                            },
                            "totals_2_5": {
                                "over": price["totals_2_5"]["over"],
                                "under": price["totals_2_5"]["under"],
                            },
                            "benchmark_probabilities": benchmark,
                            "benchmark_book_counts": {"1X2": 1, "TOTALS_2_5": 1},
                            "benchmark_is_executable": False,
                            "settlement_basis": "REGULATION_ONLY_LEAGUE_FINAL",
                            "winline": {
                                "event_id": winline_id,
                                "team1_ru": price["identity_match"]["winline_home_team"],
                                "team2_ru": price["identity_match"]["winline_away_team"],
                                "league_raw": price["competition"],
                                "kickoff_delta_seconds": price["identity_match"]["kickoff_delta_seconds"],
                                "source_url": WINLINE_SOURCE_URL,
                            },
                        }
                    )
    source_status = {
        "source": "winline",
        "snapshot_captured_at": snapshot["captured_at"],
        "snapshot_events": len(winline_events),
        "espn_fixtures": len(seen_espn),
        "matched_events": len(out),
    }
    logger.info(
        "Winline snapshot=%s events=%s ESPN fixtures=%s exact matches=%s",
        source_status["snapshot_captured_at"],
        source_status["snapshot_events"],
        source_status["espn_fixtures"],
        source_status["matched_events"],
    )
    return out, source_status


async def preflight_health(*, max_age_seconds: int = 900) -> dict[str, Any]:
    parsed = urlsplit(HEALTH_URL)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("COUNCIL_HEALTH_URL must be a loopback HTTP endpoint")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        trust_env=False,
    ) as client:
        response = await client.get(HEALTH_URL)
        _safe_http_status(response, "council health")
        payload = response.json()
    workers = payload.get("workers") or {}
    now_epoch = utc_now().timestamp()
    failures: list[str] = []
    for member in COUNCIL_MEMBERS:
        status = workers.get(member) or {}
        if status.get("up") is not True:
            failures.append(f"{member}:down")
            continue
        last_check = status.get("last_check")
        if not isinstance(last_check, (int, float)) or now_epoch - float(last_check) > max_age_seconds:
            failures.append(f"{member}:stale-health")
    if failures:
        raise RuntimeError("council health quorum failed: " + ", ".join(failures))
    return payload


class OmniRouteSession:
    """One authenticated cookie session shared by parallel council requests."""

    def __init__(self) -> None:
        self.base_url = OMNIROUTE_INTERNAL_URL
        self.username = str(getattr(config, "OMNIROUTE_USER", "") or "")
        self.password = str(getattr(config, "OMNIROUTE_PASS", "") or "")
        self.client: httpx.AsyncClient | None = None
        self.configured_models = dict(INTENDED_MODEL_ROSTER)
        self.config_overrides: set[str] = set()

    async def __aenter__(self) -> "OmniRouteSession":
        if not self.base_url or not self.password:
            raise RuntimeError("OmniRoute credentials are not configured")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("COUNCIL_OMNIROUTE_URL must be a loopback HTTP endpoint")
        timeout = httpx.Timeout(float(os.getenv("COUNCIL_MEMBER_TIMEOUT", "90")), connect=15.0)
        self.client = httpx.AsyncClient(timeout=timeout, trust_env=False)
        try:
            response = await self.client.post(
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self.password},
            )
            _safe_http_status(response, "OmniRoute login")
            auth_token = response.cookies.get("auth_token")
            if not auth_token:
                raise RuntimeError("OmniRoute login returned no auth cookie")
            # The gateway marks its public cookie Secure.  The experiment uses the
            # loopback upstream only, so forward it explicitly without network egress.
            self.client.headers["Cookie"] = f"auth_token={auth_token}"
            workers_response = await self.client.get(
                f"{self.base_url}/api/managed-agents/workers"
            )
            _safe_http_status(workers_response, "OmniRoute worker configuration")
            workers_payload = workers_response.json()
            for item in workers_payload.get("workers") or []:
                if not isinstance(item, dict):
                    continue
                worker_id = str(item.get("workerId") or "")
                configured_model = str(item.get("model") or "")
                if worker_id in COUNCIL_MEMBERS and configured_model:
                    self.configured_models[worker_id] = configured_model
                    self.config_overrides.add(worker_id)
            if set(self.configured_models) != set(COUNCIL_MEMBERS):
                raise RuntimeError("OmniRoute effective-model roster is incomplete")
            return self
        except BaseException:
            await self.client.aclose()
            self.client = None
            raise

    async def __aexit__(self, *_exc: object) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def call(self, model: str, system: str, prompt: str, max_tokens: int) -> str:
        if model not in COUNCIL_MEMBERS:
            raise ValueError(f"model alias is not a council seat: {model}")
        assert self.client is not None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.post(
                    f"{self.base_url}/api/managed-agents/chat-completion",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": f"{system}\n\n{prompt}"}],
                        "stream": False,
                        "tools": [],
                        "tool_choice": "none",
                        "max_tokens": max_tokens,
                    },
                )
                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                _safe_http_status(response, f"OmniRoute seat {model}")
                payload = response.json()
                text = payload["choices"][0]["message"]["content"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("OmniRoute returned empty content")
                served_worker = payload.get("model")
                if served_worker != model:
                    raise ValueError(
                        f"OmniRoute response identity {served_worker!r} differs from {model!r}"
                    )
                request_id = payload.get("id") or response.headers.get("x-request-id")
                if not request_id:
                    raise ValueError("OmniRoute returned no request identity")
                response_model = payload.get("served_model")
                if response_model:
                    effective_model = str(response_model)
                    identity_source = "gateway-response"
                else:
                    effective_model = self.configured_models[model]
                    identity_source = (
                        "gateway-config-override"
                        if model in self.config_overrides
                        else "gateway-static-default"
                    )
                return {
                    "text": text.strip(),
                    "served_worker": served_worker,
                    "served_model": effective_model,
                    "request_id": str(request_id),
                    "identity_source": identity_source,
                }
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                break
        raise RuntimeError(f"{model} transport failed after exact-worker retry: {type(last_error).__name__}")


async def scrape_without_ai_sources(team1: str, team2: str):
    """Collect non-AI, non-betting-market features for the sealed forecast input."""
    original_registry = scraper_module.SCRAPER_REGISTRY
    original_clv = getattr(config, "CLV_TRACKING_ENABLED", False)
    original_debug_snapshots = getattr(config, "SCRAPER_DEBUG_SNAPSHOTS", False)
    excluded = {"x_social", "betexplorer", "market_signals"}
    scraper_module.SCRAPER_REGISTRY = tuple(
        spec for spec in original_registry if spec.key not in excluded
    )
    config.CLV_TRACKING_ENABLED = False
    config.SCRAPER_DEBUG_SNAPSHOTS = False
    try:
        return await scraper_module.scrape_all_parallel(team1, team2)
    finally:
        scraper_module.SCRAPER_REGISTRY = original_registry
        config.CLV_TRACKING_ENABLED = original_clv
        config.SCRAPER_DEBUG_SNAPSHOTS = original_debug_snapshots


def build_evidence(
    data,
    event: dict[str, Any],
    *,
    snapshot_captured_at: str,
) -> tuple[str, set[str], str, dict[str, Any]]:
    """Build a price-blind forecast hash plus a sealed Winline audit envelope."""
    frozen = copy.deepcopy(data)
    frozen.x_social = None
    frozen.odds = None
    frozen.market_signals = None
    frozen.pinnacle_odds = None
    feature_snapshot = extract_feature_snapshot(frozen)
    numeric_baseline = build_numeric_baseline(frozen, feature_snapshot, recalibration_profiles=[])
    context = _build_data_context(
        frozen,
        calibration_block="",
        team_memory_block="",
        elo_block="",
        numeric_baseline_block=_format_numeric_baseline_block(numeric_baseline),
    )
    visible_lines = [
        f"Kickoff UTC: {event['commence_time']}",
        f"Forecast snapshot captured UTC: {snapshot_captured_at}",
        "Betting-market prices are intentionally withheld from all AI forecasters.",
    ]
    raw_lines = visible_lines + context.splitlines()
    evidence_lines: list[str] = []
    evidence_ids: set[str] = set()
    for raw in raw_lines:
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(raw)).strip()
        if not clean or set(clean) <= {"-", "=", "─", "═"}:
            continue
        clean = clean[:600]
        evidence_id = f"E{len(evidence_lines) + 1:04d}"
        evidence_ids.add(evidence_id)
        evidence_lines.append(f"[{evidence_id}] {clean}")
        if len(evidence_lines) >= 220:
            break
    evidence = "\n".join(evidence_lines)
    visible_envelope = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "experiment_version": EXPERIMENT_VERSION,
        "policy_version": POLICY_VERSION,
        "input_builder_version": INPUT_BUILDER_VERSION,
        "event": {
            "sport_key": event["sport_key"],
            "team1": event["home_team"],
            "team2": event["away_team"],
            "kickoff_utc": event["commence_time"],
        },
        "forecast_snapshot_captured_at": snapshot_captured_at,
        "forecast_evidence": evidence_lines,
    }
    visible_canonical = json.dumps(
        visible_envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    context_hash = hashlib.sha256(visible_canonical.encode("utf-8")).hexdigest()
    envelope = {
        "forecast_input": visible_envelope,
        "forecast_input_hash": context_hash,
        "sealed_event_identity": {
            "event_id": event["event_id"],
            "winline_event_id": event["provider_event_id"],
        },
        "selector_quote_snapshot": {
            "source": event["pricing_source"],
            "captured_at": event["captured_at"],
            "benchmark_is_executable": False,
            "settlement_basis": event["settlement_basis"],
            "benchmark_book_counts": event["benchmark_book_counts"],
            "benchmark_probabilities": event["benchmark_probabilities"],
            "average_decimal_odds": {
                "h2h": event["h2h"],
                "totals_2_5": event["totals_2_5"],
            },
            "winline_provenance": event["winline"],
        },
    }
    sealed_canonical = json.dumps(
        envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    envelope["sealed_envelope_hash"] = hashlib.sha256(
        sealed_canonical.encode("utf-8")
    ).hexdigest()
    return evidence, evidence_ids, context_hash, envelope


def eligible_events(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    min_hours: float,
    max_hours: float,
) -> list[tuple[dict[str, Any], float]]:
    eligible: list[tuple[dict[str, Any], float]] = []
    for event in events:
        try:
            kickoff = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        horizon = (kickoff - now).total_seconds() / 3600.0
        if min_hours <= horizon <= max_hours:
            eligible.append((event, horizon))
    return sorted(eligible, key=lambda item: item[1])


async def main() -> None:
    parser = argparse.ArgumentParser(description="Four-AI football paper council")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--min-hours", type=float, default=6.0)
    parser.add_argument("--max-hours", type=float, default=40.0)
    parser.add_argument("--max-fixtures", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="preflight only; no scrapers or model calls")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    now = utc_now()
    health_label = "deferred until after prospective reservation"
    if args.dry_run:
        await preflight_health()
        health_label = "4/4 up"
    events, price_source_status = await fetch_upcoming_events()
    eligible = [
        (event, horizon)
        for event, horizon in eligible_events(
            events, now=now, min_hours=args.min_hours, max_hours=args.max_hours
        )
        if event.get("benchmark_probabilities")
        and int((event.get("benchmark_book_counts") or {}).get("1X2", 0)) == 1
        and int((event.get("benchmark_book_counts") or {}).get("TOTALS_2_5", 0)) == 1
    ]
    conn = None
    if args.dry_run:
        if args.db.is_file():
            conn = connect_readonly(args.db)
        candidates = [
            (event, horizon)
            for event, horizon in eligible
            if conn is None or not has_designated_event(conn, event["event_id"])
        ][: max(0, args.max_fixtures)]
        status = store_status(conn) if conn is not None else {
            "attempts": 0,
            "designated": 0,
            "complete": 0,
            "running": 0,
            "terminal": 0,
            "settled_events": 0,
            "designated_settled": 0,
            "quorum_completion_rate": None,
            "resolution_coverage": None,
        }
    else:
        conn = connect(args.db)
        stale_minutes = max(20, int(os.getenv("COUNCIL_STALE_MINUTES", "30")))
        stale_recovered = reconcile_stale_running(
            conn,
            stale_before_utc=iso_utc(now - timedelta(minutes=stale_minutes)),
            finished_at=iso_utc(now),
        )
        candidates = [
            (event, horizon)
            for event, horizon in eligible
            if not has_designated_event(conn, event["event_id"])
        ][: max(0, args.max_fixtures)]
        status = store_status(conn)
        status["stale_running_recovered"] = stale_recovered

    print(
        json.dumps(
            {
                "mode": "dry-run" if args.dry_run else "paper-council",
                "health": health_label,
                "priced_events": len(events),
                "price_source": price_source_status,
                "eligible_new_events": len(candidates),
                "store": status,
            },
            ensure_ascii=False,
        )
    )
    if args.dry_run or not candidates:
        if conn is not None:
            conn.close()
        return

    max_tokens = int(os.getenv("COUNCIL_MAX_TOKENS", "1200"))
    assert conn is not None
    for event, horizon_hours in candidates:
        run_id = str(uuid.uuid4())
        selected_at = iso_utc(utc_now())
        designated = reserve_council_run(
            conn,
            run_id=run_id,
            event_id=event["event_id"],
            team1=event["home_team"],
            team2=event["away_team"],
            sport_key=event["sport_key"],
            competition=event["competition"],
            kickoff_utc=event["commence_time"],
            as_of_utc=selected_at,
            horizon_hours=horizon_hours,
            odds_snapshot=event,
            created_at=selected_at,
        )
        if not designated:
            continue
        try:
            await preflight_health()
            data = await scrape_without_ai_sources(event["home_team"], event["away_team"])
            snapshot_captured_at = iso_utc(utc_now())
            evidence, evidence_ids, context_hash, input_envelope = build_evidence(
                data, event, snapshot_captured_at=snapshot_captured_at
            )
            async with OmniRouteSession() as omni:
                result = await run_four_ai_council(
                    call_model=omni.call,
                    team1=event["home_team"],
                    team2=event["away_team"],
                    as_of_utc=snapshot_captured_at,
                    evidence=evidence,
                    evidence_ids=evidence_ids,
                    context_hash=context_hash,
                    benchmark_probabilities=event["benchmark_probabilities"],
                    max_tokens=max_tokens,
                )
            finalize_council_run(
                conn,
                run_id=run_id,
                as_of_utc=snapshot_captured_at,
                context_hash=context_hash,
                data_completeness=float(getattr(data, "applicable_data_completeness", 0.0)),
                evidence_count=len(evidence_ids),
                input_envelope=input_envelope,
                result=result,
                finished_at=iso_utc(utc_now()),
            )
            final = result.get("debated_consensus") or {}
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "event_id": event["event_id"],
                        "match": f"{event['home_team']} vs {event['away_team']}",
                        "status": result.get("status"),
                        "designated": True,
                        "final": {
                            "status": final.get("status"),
                            "market_code": final.get("market_code"),
                            "selection_code": final.get("selection_code"),
                            "agreement": final.get("agreement_ratio"),
                            "reason": final.get("reason"),
                        },
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 - failure remains in the denominator
            mark_council_run_failed(
                conn,
                run_id=run_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
                finished_at=iso_utc(utc_now()),
            )
            logger.error(
                "paper council run %s failed after prospective reservation; type=%s",
                run_id,
                type(exc).__name__,
            )
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "event_id": event["event_id"],
                        "status": "PIPELINE_ERROR",
                        "designated": True,
                        "error_type": type(exc).__name__,
                    },
                    ensure_ascii=False,
                )
            )
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
