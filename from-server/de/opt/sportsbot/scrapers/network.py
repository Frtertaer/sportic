from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from subprocess import CalledProcessError, TimeoutExpired, run
from time import monotonic
from typing import Optional

import aiohttp
from aiohttp.abc import AbstractResolver

PUBLIC_DNS_NAMESERVERS = ("8.8.8.8", "1.1.1.1")
_NUMERIC_SOCKET_FLAGS = socket.AI_NUMERICHOST | socket.AI_NUMERICSERV
_CACHE_TTL_SECONDS = 300.0


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    results: list[dict]


class PublicDnsFallbackResolver(AbstractResolver):
    """Try the local resolver first, then public DNS via nslookup on failure."""

    def __init__(self, nameservers: tuple[str, ...] = PUBLIC_DNS_NAMESERVERS) -> None:
        self._nameservers = nameservers
        self._default = aiohttp.ThreadedResolver()
        self._cache: dict[tuple[str, int], _CacheEntry] = {}

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[dict]:
        try:
            return await self._default.resolve(host, port, family)
        except OSError:
            cached = self._cache.get((host, port))
            if cached and cached.expires_at > monotonic():
                return [
                    result
                    for result in cached.results
                    if family == socket.AF_UNSPEC or result["family"] == family
                ] or cached.results

            for nameserver in self._nameservers:
                results = await asyncio.to_thread(
                    self._resolve_with_nslookup,
                    host,
                    port,
                    family,
                    nameserver,
                )
                if results:
                    self._cache[(host, port)] = _CacheEntry(
                        expires_at=monotonic() + _CACHE_TTL_SECONDS,
                        results=results,
                    )
                    return results
            raise

    async def close(self) -> None:
        self._cache.clear()
        await self._default.close()

    def _resolve_with_nslookup(
        self,
        host: str,
        port: int,
        family: socket.AddressFamily,
        nameserver: str,
    ) -> list[dict]:
        try:
            completed = run(
                ["nslookup", host, nameserver],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=8,
                check=True,
            )
        except (CalledProcessError, FileNotFoundError, TimeoutExpired):
            return []

        return self._parse_nslookup_output(
            host=host,
            port=port,
            family=family,
            nameserver=nameserver,
            output=completed.stdout,
        )

    def _parse_nslookup_output(
        self,
        *,
        host: str,
        port: int,
        family: socket.AddressFamily,
        nameserver: str,
        output: str,
    ) -> list[dict]:
        records: list[dict] = []
        in_answer = False

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("Name:") or line.startswith("Non-authoritative answer:"):
                in_answer = True
                continue
            if not in_answer:
                continue

            tokens = (
                line.replace("Addresses:", " ")
                .replace("Address:", " ")
                .replace(",", " ")
                .split()
            )
            for token in tokens:
                try:
                    ip = ipaddress.ip_address(token)
                except ValueError:
                    continue
                if token == nameserver:
                    continue

                record_family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
                if family not in (socket.AF_UNSPEC, record_family):
                    continue

                records.append(
                    {
                        "hostname": host,
                        "host": token,
                        "port": port,
                        "family": record_family,
                        "proto": socket.IPPROTO_TCP,
                        "flags": _NUMERIC_SOCKET_FLAGS,
                    }
                )

        deduped: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for record in records:
            key = (record["host"], record["family"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(record)
        deduped.sort(key=lambda record: 0 if record["family"] == socket.AF_INET else 1)
        return deduped


_shared_resolver: Optional[PublicDnsFallbackResolver] = None


def get_shared_resolver() -> PublicDnsFallbackResolver:
    global _shared_resolver
    if _shared_resolver is None:
        _shared_resolver = PublicDnsFallbackResolver()
    return _shared_resolver


def build_tcp_connector(
    *,
    limit: int = 10,
    ttl_dns_cache: int = 300,
    ssl: bool | None = False,
) -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        limit=limit,
        ttl_dns_cache=ttl_dns_cache,
        ssl=ssl,
        resolver=get_shared_resolver(),
    )


def build_client_session(
    *,
    timeout_total: float,
    headers: Optional[dict[str, str]] = None,
    limit: int = 10,
    ttl_dns_cache: int = 300,
    ssl: bool | None = False,
) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=build_tcp_connector(limit=limit, ttl_dns_cache=ttl_dns_cache, ssl=ssl),
        timeout=aiohttp.ClientTimeout(total=timeout_total),
        headers=headers,
    )
