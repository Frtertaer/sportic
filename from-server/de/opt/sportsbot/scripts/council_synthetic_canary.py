#!/usr/bin/env python3
"""Non-persistent routing canary for the strict four-AI council."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

os.environ["SPORTSBOT_SKIP_DOTENV"] = "1"
os.environ.pop("COUNCIL_ODDS_API_KEY", None)
os.environ.pop("ODDS_API_KEY", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_council.core import (  # noqa: E402
    COUNCIL_MEMBERS,
    INTENDED_MODEL_ROSTER,
    run_four_ai_council,
)
from scripts.council_forward_test import (  # noqa: E402
    OmniRouteSession,
    preflight_health,
)


async def run_canary() -> dict[str, object]:
    await preflight_health()
    captured = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lines = [
        "[E0001] SYNTHETIC CANARY ONLY. This is not a real fixture or recommendation.",
        "[E0002] Synthetic home expected-goals baseline: 1.35.",
        "[E0003] Synthetic away expected-goals baseline: 1.15.",
        "[E0004] Venue, lineups, injuries and recent form are intentionally unavailable.",
    ]
    envelope = {
        "canary_schema": "four-ai-routing-canary-v1",
        "synthetic_canary": True,
        "official_experiment": False,
        "persist": False,
        "nonce": secrets.token_hex(16),
        "captured_at": captured,
        "evidence": lines,
    }
    context_hash = hashlib.sha256(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    benchmark = {
        "1X2:TEAM1": 33.34,
        "1X2:DRAW": 33.33,
        "1X2:TEAM2": 33.33,
        "TOTALS_2_5:OVER": 50.0,
        "TOTALS_2_5:UNDER": 50.0,
    }
    logical_calls: list[str] = []

    async with OmniRouteSession() as omni:
        if omni.configured_models != INTENDED_MODEL_ROSTER:
            raise RuntimeError("configured roster mismatch")
        if len(set(omni.configured_models.values())) != len(COUNCIL_MEMBERS):
            raise RuntimeError("configured roster does not contain four distinct models")

        async def counted_call(
            model: str, system: str, prompt: str, max_tokens: int
        ) -> dict[str, object]:
            logical_calls.append(model)
            return await omni.call(model, system, prompt, max_tokens)

        result = await run_four_ai_council(
            call_model=counted_call,
            team1="SYNTHETIC_CANARY_HOME",
            team2="SYNTHETIC_CANARY_AWAY",
            as_of_utc=captured,
            evidence="\n".join(lines),
            evidence_ids={"E0001", "E0002", "E0003", "E0004"},
            context_hash=context_hash,
            benchmark_probabilities=benchmark,
            max_tokens=1200,
        )

    if result.get("status") != "COMPLETE":
        raise RuntimeError("four-AI canary quorum failed")
    expected_calls = Counter({member: 2 for member in COUNCIL_MEMBERS})
    if len(logical_calls) != 8 or Counter(logical_calls) != expected_calls:
        raise RuntimeError("four-AI canary did not make exactly two calls per seat")

    request_ids: list[str] = []
    identity_sources: dict[str, set[str]] = {member: set() for member in COUNCIL_MEMBERS}
    for round_name in ("round1", "round2"):
        attempts = result.get(round_name)
        if not isinstance(attempts, dict) or set(attempts) != set(COUNCIL_MEMBERS):
            raise RuntimeError("four-AI canary returned an incomplete round")
        for seat in COUNCIL_MEMBERS:
            item = attempts[seat]
            forecast = item.get("forecast")
            if (
                item.get("status") != "VALID"
                or item.get("model") != seat
                or item.get("served_worker") != seat
                or item.get("served_model") != INTENDED_MODEL_ROSTER[seat]
                or forecast is None
                or forecast.input_hash != context_hash
                or not item.get("request_id")
            ):
                raise RuntimeError("four-AI canary identity or schema validation failed")
            request_ids.append(str(item["request_id"]))
            identity_sources[seat].add(str(item.get("identity_source") or "unknown"))
    if len(set(request_ids)) != 8:
        raise RuntimeError("four-AI canary request identifiers were not unique")

    return {
        "canary": "PASS",
        "official_experiment": False,
        "persisted": False,
        "odds_api_used": False,
        "logical_calls": 8,
        "unique_successful_request_ids": 8,
        "workers": dict(INTENDED_MODEL_ROSTER),
        "identity_sources": {
            member: sorted(sources) for member, sources in identity_sources.items()
        },
    }


def main() -> None:
    try:
        payload = asyncio.run(run_canary())
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "canary": "FAIL",
                    "official_experiment": False,
                    "persisted": False,
                    "error_type": type(exc).__name__,
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
