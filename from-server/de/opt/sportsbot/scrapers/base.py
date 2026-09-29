"""
Abstract base class for all site scrapers.

Anti-bot strategy (3 layers):
1. Header fingerprinting — User-Agent rotation, Accept-Language variation,
   site-specific Referer headers
2. Timing — per-domain polite delay + random jitter before each request cluster
3. Graceful failure — HTTP 403/429 → RateLimitError (NOT retried) → scrape_success=False
   Other errors → ScraperError (retried up to 3x with exponential backoff via tenacity)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Any, Optional

import aiohttp
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

import config
from scrapers.network import build_client_session

logger = logging.getLogger(__name__)
_DOMAIN_LIMITERS: dict[tuple[int, str], asyncio.Semaphore] = {}

# Per-domain rate-limit cooldown: domain → monotonic timestamp when cooldown expires
_DOMAIN_COOLDOWNS: dict[str, float] = {}
_RATE_LIMIT_COOLDOWN_SECONDS: float = float(
    os.environ.get("RATE_LIMIT_COOLDOWN_SECONDS", "300")
)


def is_domain_cooled_down(domain: str) -> bool:
    """Return True if domain is in rate-limit cooldown (should be skipped)."""
    expires = _DOMAIN_COOLDOWNS.get(domain)
    if expires is None:
        return False
    from time import monotonic
    if monotonic() >= expires:
        _DOMAIN_COOLDOWNS.pop(domain, None)
        return False
    return True


def _set_domain_cooldown(domain: str) -> None:
    """Mark domain as rate-limited for RATE_LIMIT_COOLDOWN_SECONDS."""
    from time import monotonic
    _DOMAIN_COOLDOWNS[domain] = monotonic() + _RATE_LIMIT_COOLDOWN_SECONDS
    logger.warning(
        "Domain %s entered rate-limit cooldown for %ds",
        domain,
        int(_RATE_LIMIT_COOLDOWN_SECONDS),
    )


def get_domain_cooldown_status() -> dict[str, float]:
    """Return {domain: seconds_remaining} for active cooldowns."""
    from time import monotonic
    now = monotonic()
    active: dict[str, float] = {}
    expired: list[str] = []
    for domain, expires in _DOMAIN_COOLDOWNS.items():
        remaining = expires - now
        if remaining > 0:
            active[domain] = round(remaining, 1)
        else:
            expired.append(domain)
    for d in expired:
        _DOMAIN_COOLDOWNS.pop(d, None)
    return active


def _get_domain_semaphore(domain: str) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = (id(loop), domain or "_default")
    semaphore = _DOMAIN_LIMITERS.get(key)
    if semaphore is None:
        limit = config.DOMAIN_CONCURRENCY_LIMITS.get(domain, config.SEMAPHORE_LIMIT)
        semaphore = asyncio.Semaphore(limit)
        _DOMAIN_LIMITERS[key] = semaphore
    return semaphore


class ScraperError(Exception):
    """Transient error — will be retried by tenacity."""

class RateLimitError(ScraperError):
    """HTTP 403/429 block — NOT retried, returns scrape_success=False immediately."""


class BaseScraper(ABC):
    """
    Contract enforced on all subclasses:
    - DOMAIN class attribute must be set (e.g. "flashscore.com")
    - scrape() must ALWAYS return a Pydantic model, NEVER raise
    - Use _fetch_with_retry() for all HTTP calls
    """

    DOMAIN: str = ""

    def __init__(self, team1: str, team2: str) -> None:
        self.team1 = team1
        self.team2 = team2
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Headers ───────────────────────────────────────────────────────────────

    def _make_headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        """Return randomised headers. Pass extra={} to add site-specific headers."""
        headers = config.BASE_HEADERS.copy()
        headers["User-Agent"] = random.choice(config.USER_AGENTS)
        headers["Accept-Language"] = random.choice([
            "en-US,en;q=0.9",
            "en-GB,en;q=0.9,en-US;q=0.8",
            "en-US,en;q=0.8",
        ])
        if extra:
            headers.update(extra)
        return headers

    # ── Session ───────────────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = build_client_session(
                timeout_total=config.REQUEST_TIMEOUT,
                headers=self._make_headers(),
            )
        return self._session

    async def _close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _write_debug_snapshot(
        self,
        *,
        reason: str,
        url: str,
        params: Optional[dict[str, Any]] = None,
        request_headers: Optional[dict[str, str]] = None,
        response_headers: Optional[dict[str, str]] = None,
        status: Optional[int] = None,
        body: Optional[str] = None,
        exception: Optional[str] = None,
    ) -> None:
        if not config.SCRAPER_DEBUG_SNAPSHOTS:
            return

        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "domain": self.DOMAIN,
            "teams": {"team1": self.team1, "team2": self.team2},
            "reason": reason,
            "url": url,
            "status": status,
            "params": params,
            "request_headers": request_headers,
            "response_headers": response_headers,
            "exception": exception,
            "body": (body or "")[: config.SCRAPER_DEBUG_SNAPSHOT_MAX_BYTES],
        }

        await asyncio.to_thread(self._write_debug_snapshot_sync, payload)

    def _write_debug_snapshot_sync(self, payload: dict[str, Any]) -> None:
        snapshot_dir = Path(config.SCRAPER_DEBUG_SNAPSHOTS_DIR)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        url_hash = sha1(str(payload.get("url", "")).encode("utf-8")).hexdigest()[:10]
        filename = snapshot_dir / f"{stamp}-{self.DOMAIN or 'unknown'}-{url_hash}.json"
        filename.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # ── Fetch ─────────────────────────────────────────────────────────────────

    async def _fetch(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        json_mode: bool = False,
    ) -> str:
        """
        Single HTTP GET with domain delay + jitter.
        Raises RateLimitError on 403/429, ScraperError on other HTTP errors.
        Skips request entirely if domain is in rate-limit cooldown.
        """
        if is_domain_cooled_down(self.DOMAIN):
            raise RateLimitError(
                f"Domain {self.DOMAIN} is in rate-limit cooldown, skipping request"
            )

        session = await self._get_session()

        delay = config.DOMAIN_DELAYS.get(self.DOMAIN, 0.5)
        await asyncio.sleep(delay + random.uniform(0.0, 0.4))

        req_headers = self._make_headers(headers)
        if json_mode:
            req_headers["Accept"] = "application/json"

        try:
            async with _get_domain_semaphore(self.DOMAIN):
                async with session.get(url, params=params, headers=req_headers) as resp:
                    body = await resp.text()
                    if resp.status in (403, 429):
                        await self._write_debug_snapshot(
                            reason="rate_limit",
                            url=url,
                            params=params,
                            request_headers=req_headers,
                            response_headers=dict(resp.headers),
                            status=resp.status,
                            body=body,
                        )
                        _set_domain_cooldown(self.DOMAIN)
                        raise RateLimitError(
                            f"Blocked ({resp.status}) on {self.DOMAIN}: {url}"
                        )
                    if resp.status == 404:
                        await self._write_debug_snapshot(
                            reason="not_found",
                            url=url,
                            params=params,
                            request_headers=req_headers,
                            response_headers=dict(resp.headers),
                            status=resp.status,
                            body=body,
                        )
                        raise ScraperError(f"404 Not Found: {url}")
                    if resp.status >= 500:
                        await self._write_debug_snapshot(
                            reason="server_error",
                            url=url,
                            params=params,
                            request_headers=req_headers,
                            response_headers=dict(resp.headers),
                            status=resp.status,
                            body=body,
                        )
                        raise ScraperError(f"Server error {resp.status}: {url}")
                    if resp.status >= 400:
                        await self._write_debug_snapshot(
                            reason="http_error",
                            url=url,
                            params=params,
                            request_headers=req_headers,
                            response_headers=dict(resp.headers),
                            status=resp.status,
                            body=body,
                        )
                        raise ScraperError(f"HTTP {resp.status}: {url}")
                    return body
        except asyncio.TimeoutError as exc:
            await self._write_debug_snapshot(
                reason="timeout",
                url=url,
                params=params,
                request_headers=req_headers,
                exception=str(exc),
            )
            raise ScraperError(f"Timed out on {self.DOMAIN}: {url}") from exc
        except aiohttp.ClientError as exc:
            await self._write_debug_snapshot(
                reason="client_error",
                url=url,
                params=params,
                request_headers=req_headers,
                exception=str(exc),
            )
            raise ScraperError(f"HTTP client error on {url}: {exc}") from exc

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1.5, min=2, max=12),
        retry=retry_if_exception(
            lambda exc: isinstance(exc, ScraperError) and not isinstance(exc, RateLimitError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _fetch_with_retry(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        json_mode: bool = False,
    ) -> str:
        """
        _fetch() wrapped with tenacity retry.
        Retries ScraperError (network glitches) but NOT RateLimitError.
        RateLimitError is excluded from the retry predicate so it propagates
        immediately to the scrape() error handler.
        """
        return await self._fetch(
            url, params=params, headers=headers, json_mode=json_mode
        )

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def scrape(self) -> Any:
        """
        Entry point. Must:
        - Wrap all logic in try/except
        - Return a Pydantic model with scrape_success=False on any failure
        - NEVER propagate raw exceptions to callers
        """

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "BaseScraper":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._close()
