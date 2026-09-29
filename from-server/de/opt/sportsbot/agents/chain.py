"""Orchestrator: Analyst -> Critic -> Synthesis with cascading fallbacks.

Fallback strategy:
  - Analyst (Claude Opus 4.6) fails     -> caller falls back to single-call mode
  - Critic (GPT-5.4) fails              -> Synthesis runs with empty CriticOutput
  - Synthesis (Gemini 3.1 Pro) fails    -> return Analyst output directly
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.analyst import run_analyst
from agents.critic import run_critic
from agents.model_utils import AgentClients, build_backcompat_clients
from agents.synthesis import run_synthesis
from models import AnalystOutput, CriticOutput, MemoryContext
from memory.base_memory import BaseMemory

logger = logging.getLogger(__name__)


async def run_agent_chain(
    *,
    clients: AgentClients | None = None,
    client=None,
    team1: str,
    team2: str,
    data_context: str,
    memory_context: MemoryContext,
    warnings_block: str,
    x_social_context: str = "",
    raw_completeness_pct: int | None = None,
    raw_ok_sources: int | None = None,
    raw_total_sources: int | None = None,
    applicable_completeness_pct: int | None = None,
    applicable_ok_sources: int | None = None,
    applicable_total_sources: int | None = None,
    completeness_pct: int | None = None,
    ok_sources: int | None = None,
    total_sources: int | None = None,
) -> Optional[dict]:
    """Run the three-agent chain and return a PredictionReport-compatible dict.

    Returns ``None`` if the Analyst fails so the caller can use single-call mode.
    """
    # Build memory block for injection into prompts
    memory_parts: list[str] = []
    if memory_context.similar_matches:
        memory_parts.append(
            BaseMemory.format_similar_matches_for_prompt(memory_context.similar_matches)
        )
    if memory_context.relevant_lessons:
        memory_parts.append(
            BaseMemory.format_lessons_for_prompt(memory_context.relevant_lessons)
        )
    if memory_context.calibration:
        memory_parts.append(memory_context.calibration)
    if memory_context.team_memory:
        memory_parts.append(memory_context.team_memory)
    memory_block = "\n".join(memory_parts)

    lessons_block = (
        BaseMemory.format_lessons_for_prompt(memory_context.relevant_lessons)
        if memory_context.relevant_lessons else "(no lessons available)"
    )

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

    resolved_clients = build_backcompat_clients(clients=clients, client=client)

    # ── Agent 1: Analyst (Opus) ──────────────────────────────────────────────
    try:
        analyst_output: AnalystOutput = await run_analyst(
            clients=resolved_clients,
            team1=team1,
            team2=team2,
            data_context=data_context,
            memory_block=memory_block,
            raw_completeness_pct=raw_completeness_pct,
            raw_ok_sources=raw_ok_sources,
            raw_total_sources=raw_total_sources,
            applicable_completeness_pct=applicable_completeness_pct,
            applicable_ok_sources=applicable_ok_sources,
            applicable_total_sources=applicable_total_sources,
            warnings_block=warnings_block,
        )
        analyst_json = analyst_output.model_dump_json(indent=2)
        logger.info("Analyst completed for %s vs %s", team1, team2)
    except Exception as exc:
        logger.error("Analyst (Opus) failed — chain aborted: %s", exc)
        return None  # caller falls back to single-call Sonnet

    # ── Agent 2: Critic (Sonnet) ─────────────────────────────────────────────
    try:
        critic_output: CriticOutput = await run_critic(
            clients=resolved_clients,
            team1=team1,
            team2=team2,
            data_context=data_context,
            analyst_json=analyst_json,
            lessons_block=lessons_block,
        )
        critic_json = critic_output.model_dump_json(indent=2)
        logger.info(
            "Critic completed for %s vs %s — assessment: %s",
            team1, team2, critic_output.overall_assessment,
        )
    except Exception as exc:
        logger.warning("Critic (Sonnet) failed — proceeding with APPROVE: %s", exc)
        critic_output = CriticOutput()  # default: APPROVE, no issues
        critic_json = critic_output.model_dump_json(indent=2)

    # ── Agent 3: Synthesis (Opus) ────────────────────────────────────────────
    try:
        result_dict = await run_synthesis(
            clients=resolved_clients,
            team1=team1,
            team2=team2,
            data_context=data_context,
            analyst_json=analyst_json,
            critic_json=critic_json,
        )
        logger.info("Synthesis completed for %s vs %s", team1, team2)
    except Exception as exc:
        logger.warning("Synthesis (Opus) failed — using Analyst output directly: %s", exc)
        # Strip reasoning fields, keep PredictionReport-compatible fields
        d = analyst_output.model_dump()
        for key in ("reasoning", "key_assumptions", "lesson_applications"):
            d.pop(key, None)
        result_dict = d

    return result_dict
