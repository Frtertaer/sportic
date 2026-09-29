"""Agent 3: Synthesis — merges analyst + critic using Gemini 3.1 Pro."""
from __future__ import annotations

import json
import logging

import config
from agents.model_utils import (
    AgentClients,
    ProviderModelSpec,
    build_backcompat_clients,
    generate_text_with_fallback,
)
from agents.prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER

logger = logging.getLogger(__name__)


async def run_synthesis(
    *,
    clients: AgentClients | None = None,
    client=None,
    team1: str,
    team2: str,
    data_context: str,
    analyst_json: str,
    critic_json: str,
) -> dict:
    """Call the configured Synthesis model to produce the final report."""
    user_prompt = SYNTHESIS_USER.format(
        team1=team1,
        team2=team2,
        data_context=data_context,
        analyst_json=analyst_json,
        critic_json=critic_json,
    )

    logger.info(
        "Synthesis (%s:%s): sending %d-char prompt for %s vs %s",
        config.AGENT_SYNTHESIS_PROVIDER,
        config.AGENT_SYNTHESIS_MODEL,
        len(user_prompt),
        team1,
        team2,
    )

    resolved_clients = build_backcompat_clients(clients=clients, client=client)

    raw = await generate_text_with_fallback(
        clients=resolved_clients,
        primary_spec=ProviderModelSpec(
            provider=config.AGENT_SYNTHESIS_PROVIDER,
            model=config.AGENT_SYNTHESIS_MODEL,
        ),
        fallback_spec=ProviderModelSpec(
            provider=config.AGENT_SYNTHESIS_FALLBACK_PROVIDER,
            model=config.AGENT_SYNTHESIS_FALLBACK_MODEL,
        ),
        max_tokens=config.AGENT_MAX_TOKENS,
        system=SYNTHESIS_SYSTEM,
        user_prompt=user_prompt,
        logger=logger,
        agent_label="Synthesis",
    )

    from analyzer import _loads_agent_json
    return _loads_agent_json(raw, context="synthesis")
