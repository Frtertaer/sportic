"""Agent 1: Analyst — produces initial analysis using Claude Opus 4.6."""
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
from agents.prompts import ANALYST_SYSTEM, ANALYST_USER
from models import AnalystOutput

logger = logging.getLogger(__name__)


async def run_analyst(
    *,
    clients: AgentClients | None = None,
    client=None,
    team1: str,
    team2: str,
    data_context: str,
    memory_block: str,
    warnings_block: str,
    raw_completeness_pct: int | None = None,
    raw_ok_sources: int | None = None,
    raw_total_sources: int | None = None,
    applicable_completeness_pct: int | None = None,
    applicable_ok_sources: int | None = None,
    applicable_total_sources: int | None = None,
    completeness_pct: int | None = None,
    ok_sources: int | None = None,
    total_sources: int | None = None,
) -> AnalystOutput:
    """Call the configured Analyst model to produce transparent reasoning."""
    if raw_completeness_pct is None:
        raw_completeness_pct = completeness_pct or 0
    if raw_ok_sources is None:
        raw_ok_sources = ok_sources or 0
    if raw_total_sources is None:
        raw_total_sources = total_sources or 0
    if applicable_completeness_pct is None:
        applicable_completeness_pct = raw_completeness_pct
    if applicable_ok_sources is None:
        applicable_ok_sources = raw_ok_sources
    if applicable_total_sources is None:
        applicable_total_sources = raw_total_sources

    user_prompt = ANALYST_USER.format(
        team1=team1,
        team2=team2,
        data_context=data_context,
        memory_block=memory_block,
        raw_completeness_pct=raw_completeness_pct,
        raw_ok=raw_ok_sources,
        raw_total=raw_total_sources,
        applicable_completeness_pct=applicable_completeness_pct,
        applicable_ok=applicable_ok_sources,
        applicable_total=applicable_total_sources,
        warnings_block=warnings_block,
    )

    logger.info(
        "Analyst (%s:%s): sending %d-char prompt for %s vs %s",
        config.AGENT_ANALYST_PROVIDER,
        config.AGENT_ANALYST_MODEL,
        len(user_prompt),
        team1,
        team2,
    )

    resolved_clients = build_backcompat_clients(clients=clients, client=client)

    raw = await generate_text_with_fallback(
        clients=resolved_clients,
        primary_spec=ProviderModelSpec(
            provider=config.AGENT_ANALYST_PROVIDER,
            model=config.AGENT_ANALYST_MODEL,
        ),
        fallback_spec=ProviderModelSpec(
            provider=config.AGENT_ANALYST_FALLBACK_PROVIDER,
            model=config.AGENT_ANALYST_FALLBACK_MODEL,
        ),
        max_tokens=config.AGENT_MAX_TOKENS,
        system=ANALYST_SYSTEM,
        user_prompt=user_prompt,
        logger=logger,
        agent_label="Analyst",
    )

    from analyzer import _loads_agent_json
    parsed = _loads_agent_json(raw, context="analyst")

    return AnalystOutput(**parsed)
