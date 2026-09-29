"""Parallel scraping orchestrator backed by a scraper registry."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import config
from models import MatchData, SourceStatus
from scrapers.registry import SCRAPER_REGISTRY, ScraperSpec, build_source_status

logger = logging.getLogger(__name__)
_SCRAPE_LIMITERS: dict[int, asyncio.Semaphore] = {}  # keyed by id(loop), cleaned on mismatch
_SCRAPER_RUNTIME_METRICS: dict[str, "_ScraperMetric"] = {}


@dataclass(slots=True)
class _ScrapeExecution:
    spec: ScraperSpec
    result: object
    status: SourceStatus


@dataclass(slots=True)
class _ScraperMetric:
    label: str
    runs: int = 0
    successes: int = 0
    failures: int = 0
    total_duration_ms: int = 0
    max_duration_ms: int = 0
    last_duration_ms: int = 0
    last_error_message: str | None = None
    last_exception_type: str | None = None

    def as_dict(self) -> dict[str, object]:
        avg_duration_ms = round(self.total_duration_ms / self.runs, 1) if self.runs else 0.0
        return {
            "label": self.label,
            "runs": self.runs,
            "successes": self.successes,
            "failures": self.failures,
            "avg_duration_ms": avg_duration_ms,
            "max_duration_ms": self.max_duration_ms,
            "last_duration_ms": self.last_duration_ms,
            "last_error_message": self.last_error_message,
            "last_exception_type": self.last_exception_type,
        }


# Type alias for the progress callback
ProgressCallback = Optional[Callable[[SourceStatus], Awaitable[None]]]


def _get_scrape_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = id(loop)
    semaphore = _SCRAPE_LIMITERS.get(key)
    if semaphore is None:
        # New loop (or first call) — purge stale entries from old loops
        _SCRAPE_LIMITERS.clear()
        semaphore = asyncio.Semaphore(config.SEMAPHORE_LIMIT)
        _SCRAPE_LIMITERS[key] = semaphore
    return semaphore


def _record_scrape_metric(status: SourceStatus) -> None:
    metric = _SCRAPER_RUNTIME_METRICS.get(status.key)
    if metric is None:
        metric = _ScraperMetric(label=status.label)
        _SCRAPER_RUNTIME_METRICS[status.key] = metric
    metric.runs += 1
    metric.last_duration_ms = status.duration_ms or 0
    metric.max_duration_ms = max(metric.max_duration_ms, status.duration_ms or 0)
    metric.total_duration_ms += status.duration_ms or 0
    metric.last_error_message = status.error_message
    metric.last_exception_type = status.exception_type
    if status.scrape_success:
        metric.successes += 1
    else:
        metric.failures += 1


def get_scrape_metrics_snapshot() -> dict[str, dict[str, object]]:
    return {
        key: metric.as_dict()
        for key, metric in _SCRAPER_RUNTIME_METRICS.items()
    }


def reset_scrape_runtime_metrics() -> None:
    _SCRAPER_RUNTIME_METRICS.clear()


async def scrape_all_parallel(
    team1: str,
    team2: str,
    on_progress: ProgressCallback = None,
) -> MatchData:
    """
    Scrape all registered sources in parallel.
    Returns MatchData regardless of individual scraper failures.

    on_progress: optional async callback(status) called when each scraper finishes.
                 Useful for live Telegram updates.
    """
    semaphore = _get_scrape_semaphore()

    async def guarded(spec: ScraperSpec) -> _ScrapeExecution:
        start = time.perf_counter()
        scraper = None
        result: object
        try:
            scraper = spec.scraper_cls(team1, team2)
            async with semaphore:
                result = await scraper.scrape()
            duration_ms = int((time.perf_counter() - start) * 1000)
            status = build_source_status(
                spec,
                result,
                duration_ms=duration_ms,
            )
            if not status.scrape_success:
                logger.warning(
                    "Scraper %s failed in %d ms: %s",
                    spec.key,
                    duration_ms,
                    status.error_message or "unspecified failure",
                )
            elif duration_ms >= config.SCRAPER_SLOW_LOG_MS:
                logger.info("Scraper %s slow-path: %d ms", spec.key, duration_ms)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            status = build_source_status(
                spec,
                None,
                error_message=str(exc),
                duration_ms=duration_ms,
                exception_type=type(exc).__name__,
            )
            result = exc
            logger.exception("Scraper %s raised unexpected exception", spec.key)
        finally:
            close = getattr(scraper, "_close", None)
            if close is not None:
                try:
                    await close()
                except Exception as close_exc:
                    logger.debug("Scraper %s session cleanup failed: %s", spec.key, close_exc)

        _record_scrape_metric(status)
        if on_progress is not None:
            try:
                await on_progress(status)
            except Exception as cb_exc:
                logger.debug("on_progress callback error: %s", cb_exc)
        return _ScrapeExecution(spec=spec, result=result, status=status)

    logger.info(
        "Starting parallel scrape: %s vs %s across %d sources",
        team1,
        team2,
        len(SCRAPER_REGISTRY),
    )

    # Run all registered scrapers + Pinnacle in parallel
    pinnacle_snapshot = None
    pinnacle_task = None
    if getattr(config, "CLV_TRACKING_ENABLED", False):
        from scrapers.pinnacle import fetch_pinnacle_snapshot
        pinnacle_task = asyncio.create_task(
            _safe_pinnacle_fetch(fetch_pinnacle_snapshot, team1, team2)
        )

    executions = await asyncio.gather(
        *[guarded(spec) for spec in SCRAPER_REGISTRY],
    )

    if pinnacle_task is not None:
        pinnacle_snapshot = await pinnacle_task

    payload = {
        "team1": team1,
        "team2": team2,
        "pinnacle_odds": pinnacle_snapshot,
        "source_statuses": [],
    }

    for execution in executions:
        payload["source_statuses"].append(execution.status)
        payload[execution.spec.field_name] = (
            None if isinstance(execution.result, Exception) else execution.result
        )

    return MatchData(**payload)


async def _safe_pinnacle_fetch(fetch_fn, team1: str, team2: str):
    """Fetch Pinnacle odds without crashing the main pipeline."""
    try:
        return await fetch_fn(team1, team2)
    except Exception as exc:
        logger.warning("Pinnacle fetch failed (non-critical): %s", exc)
        return None
