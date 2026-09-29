"""Fail-closed Odds API quota accounting shared by council jobs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def new_quota_snapshot() -> dict[str, Any]:
    return {
        "requests_remaining": None,
        "requests_used": None,
        "requests_last": None,
        "safety_floor": None,
        "required_before_fetch": None,
        "safety_paused": False,
    }


def update_quota_from_headers(
    quota: dict[str, Any], headers: Mapping[str, str]
) -> None:
    for header, key in (
        ("x-requests-remaining", "requests_remaining"),
        ("x-requests-used", "requests_used"),
        ("x-requests-last", "requests_last"),
    ):
        try:
            quota[key] = int(headers[header])
        except (KeyError, TypeError, ValueError):
            continue


def apply_quota_gate(
    quota: dict[str, Any], *, safety_floor: int, expected_cost: int
) -> bool:
    """Return True when a paid request must be skipped.

    The free `/v4/sports` probe must supply a remaining-credit header. Missing
    accounting fails closed so a provider/proxy change cannot drain the key.
    """
    floor = max(0, int(safety_floor))
    cost = max(0, int(expected_cost))
    remaining = quota.get("requests_remaining")
    if not isinstance(remaining, int):
        raise RuntimeError("Odds API response omitted quota accounting headers")
    required = floor + cost
    paused = remaining < required
    quota.update(
        {
            "safety_floor": floor,
            "required_before_fetch": required,
            "safety_paused": paused,
        }
    )
    return paused
