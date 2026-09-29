from __future__ import annotations

import pytest

import config


async def _blocked_live_ai_call(*args, **kwargs):
    raise AssertionError(
        "Live AI calls are disabled in pytest. "
        "Mock the backend explicitly or set NO_LIVE_AI_IN_TESTS=false if you intentionally want live calls."
    )


@pytest.fixture(autouse=True)
def block_live_ai_calls_in_pytest(monkeypatch):
    """Fail fast if a test accidentally reaches the real Anthropic/OpenClaw clients."""
    if not config.NO_LIVE_AI_IN_TESTS:
        return

    import analyzer
    import learning

    monkeypatch.setattr(analyzer._anthropic_client.messages, "create", _blocked_live_ai_call)
    monkeypatch.setattr(analyzer._openclaw_client.chat.completions, "create", _blocked_live_ai_call)
    monkeypatch.setattr(learning._anthropic_client.messages, "create", _blocked_live_ai_call)
    monkeypatch.setattr(learning._openclaw_client.chat.completions, "create", _blocked_live_ai_call)
