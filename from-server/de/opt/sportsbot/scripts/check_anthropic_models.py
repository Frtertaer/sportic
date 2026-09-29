#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import anthropic

import config


async def _run() -> int:
    if not config.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set.")
        return 1

    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    response = await client.models.list()
    models = sorted(model.id for model in response.data)

    print(f"Available models: {len(models)}")
    for model_id in models:
        print(f"- {model_id}")

    print("")
    print(f"Configured SONNET_MODEL: {config.SONNET_MODEL} -> {'YES' if config.SONNET_MODEL in models else 'NO'}")
    print(f"Configured OPUS_MODEL: {config.OPUS_MODEL} -> {'YES' if config.OPUS_MODEL in models else 'NO'}")

    opus_models = [model_id for model_id in models if "opus" in model_id.lower()]
    print(f"Any Opus access: {'YES' if opus_models else 'NO'}")
    if opus_models:
        print("Detected Opus models:")
        for model_id in opus_models:
            print(f"  * {model_id}")

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
