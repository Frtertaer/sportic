"""
Async TTL cache for match prediction reports.

Design:
- asyncio.Lock ensures no cache stampede when 2 users request the same match simultaneously
- Key preserves team order because prediction reports are directional
- LRU-style eviction when maxsize is reached (evicts entry closest to expiry)
- Global singleton shared across all bot handlers
"""
from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import Any, Optional

import config


class TTLCache:
    def __init__(
        self,
        ttl: int = config.CACHE_TTL_SECONDS,
        maxsize: int = config.CACHE_MAXSIZE,
    ) -> None:
        self._ttl = ttl
        self._maxsize = maxsize
        self._store: dict[str, tuple[float, Any]] = {}  # key → (expiry_ts, value)
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def make_key(team1: str, team2: str) -> str:
        pair = [team1.strip().lower(), team2.strip().lower()]
        today = date.today().isoformat()   # e.g. "2026-03-02"
        return f"{today}|{pair[0]}|{pair[1]}"

    def _get_unlocked(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        expiry, value = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def _set_unlocked(self, key: str, value: Any) -> None:
        if len(self._store) >= self._maxsize and key not in self._store:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest]
        self._store[key] = (time.monotonic() + self._ttl, value)

    @staticmethod
    def _consume_future_exception(future: asyncio.Future[Any]) -> None:
        if future.cancelled():
            return
        try:
            future.exception()
        except Exception:
            pass

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            return self._get_unlocked(key)

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._set_unlocked(key, value)

    async def claim(self, key: str) -> tuple[str, Any]:
        """
        Returns one of:
          ("cached", value)   — a fresh cached value exists
          ("wait", future)    — another request is already computing this key
          ("compute", future) — caller owns the computation for this key
        """
        async with self._lock:
            cached = self._get_unlocked(key)
            if cached is not None:
                return "cached", cached

            future = self._inflight.get(key)
            if future is not None:
                if future.done():
                    try:
                        return "cached", future.result()
                    except Exception:
                        self._inflight.pop(key, None)
                else:
                    return "wait", future

            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(self._consume_future_exception)
            self._inflight[key] = future
            return "compute", future

    async def resolve(self, key: str, value: Any) -> None:
        async with self._lock:
            self._set_unlocked(key, value)
            future = self._inflight.pop(key, None)
        if future is not None and not future.done():
            future.set_result(value)

    async def fail(self, key: str, exc: Exception) -> None:
        async with self._lock:
            future = self._inflight.pop(key, None)
        if future is not None and not future.done():
            future.set_exception(exc)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear_expired(self) -> int:
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (exp, _) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            return len(expired)

    async def get_stats(self) -> dict[str, int]:
        async with self._lock:
            return {
                "entries": len(self._store),
                "inflight": len(self._inflight),
                "maxsize": self._maxsize,
                "ttl_seconds": self._ttl,
            }

    def __len__(self) -> int:
        return len(self._store)


# Global singleton used by bot.py
match_cache = TTLCache()
