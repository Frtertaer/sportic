"""Agent 2: Critic — reviews analyst output using GPT-5.4."""
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
from agents.prompts import CRITIC_SYSTEM, CRITIC_USER
from models import CriticOutput

logger = logging.getLogger(__name__)


async def run_critic(
    *,
    clients: AgentClients | None = None,
    client=None,
    team1: str,
    team2: str,
    data_context: str,
    analyst_json: str,
    lessons_block: str,
) -> CriticOutput:
    """Call the configured Critic model to review the Analyst's output."""
    user_prompt = CRITIC_USER.format(
        team1=team1,
        team2=team2,
        data_context=data_context,
        analyst_json=analyst_json,
        lessons_block=lessons_block,
    )

    logger.info(
        "Critic (%s:%s): sending %d-char review prompt for %s vs %s",
        config.AGENT_CRITIC_PROVIDER,
        config.AGENT_CRITIC_MODEL,
        len(user_prompt),
        team1,
        team2,
    )

    resolved_clients = build_backcompat_clients(clients=clients, client=client)

    raw = await generate_text_with_fallback(
        clients=resolved_clients,
        primary_spec=ProviderModelSpec(
            provider=config.AGENT_CRITIC_PROVIDER,
            model=config.AGENT_CRITIC_MODEL,
            reasoning_effort=config.AGENT_CRITIC_REASONING_EFFORT,
        ),
        fallback_spec=ProviderModelSpec(
            provider=config.AGENT_CRITIC_FALLBACK_PROVIDER,
            model=config.AGENT_CRITIC_FALLBACK_MODEL,
        ),
        max_tokens=4096,
        system=CRITIC_SYSTEM,
        user_prompt=user_prompt,
        logger=logger,
        agent_label="Critic",
    )

    from analyzer import _loads_agent_json
    parsed = _loads_agent_json(raw, context="critic")

    return CriticOutput(**parsed)
