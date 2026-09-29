"""Base Memory — facade that combines all memory sources into a single MemoryContext.

Runs 5 parallel queries and returns a unified ``MemoryContext`` for the agent chain.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import database as db
from models import Lesson, MemoryContext, SimilarMatch

logger = logging.getLogger(__name__)


class BaseMemory:
    """Aggregator: vector memory + DB calibration + team snapshots → MemoryContext.

    Usage::

        from memory.vector_memory import VectorMemory
        vm = VectorMemory()
        await vm.initialize()
        bm = BaseMemory(vm)
        ctx = await bm.build_context("Arsenal", "Chelsea", match_data)
    """

    def __init__(self, vector_memory) -> None:
        self._vector = vector_memory

    async def build_context(
        self,
        team1: str,
        team2: str,
        context_hint: str = "",
    ) -> MemoryContext:
        """Run parallel queries and return an aggregated MemoryContext.

        Queries (all non-critical — any failure → empty fallback):
          1. Vector: find_similar_matches
          2. Vector: find_relevant_lessons
          3. DB: calibration stats → formatted block
          4. DB: team snapshots → formatted block
        """
        similar_fut = self._safe(
            self._vector.find_similar_matches(team1, team2, context_hint),
            default=[],
        )
        lesson_query = f"{team1} vs {team2} {context_hint}".strip()
        lessons_fut = self._safe(
            self._vector.find_relevant_lessons(lesson_query),
            default=[],
        )
        cal_fut = self._safe(self._build_calibration_block(), default="")
        mem_fut = self._safe(self._build_team_memory_block(team1, team2), default="")

        similar, lessons, calibration, team_mem = await asyncio.gather(
            similar_fut, lessons_fut, cal_fut, mem_fut,
        )

        # Accuracy stats from DB (lightweight dict)
        acc_stats = await self._safe(self._get_accuracy_stats(), default=None)

        return MemoryContext(
            similar_matches=similar,
            relevant_lessons=lessons,
            accuracy_stats=acc_stats,
            calibration=calibration,
            team_memory=team_mem,
        )

    # -- formatting helpers ------------------------------------------------

    @staticmethod
    def format_lessons_for_prompt(lessons: list[Lesson]) -> str:
        """Format lessons into a text block suitable for agent prompts."""
        if not lessons:
            return ""
        lines = ["\n─── LESSONS FROM PAST PREDICTIONS ───"]
        for i, les in enumerate(lessons, 1):
            cat = les.category.value if hasattr(les.category, "value") else str(les.category)
            conf_str = f"{les.confidence:.0%}" if les.confidence else "?"
            confirmed = f" (confirmed {les.times_confirmed}x)" if les.times_confirmed else ""
            lines.append(f"  {i}. [{cat}] {les.text} — confidence {conf_str}{confirmed}")
        lines.append(
            "INSTRUCTION: Review these lessons and explicitly state which ones you applied "
            "in your analysis. If a lesson contradicts the data, explain why you disregarded it."
        )
        return "\n".join(lines)

    @staticmethod
    def format_similar_matches_for_prompt(matches: list[SimilarMatch]) -> str:
        """Format similar past matches into a text block for agent prompts."""
        if not matches:
            return ""
        lines = ["\n─── SIMILAR PAST MATCHES ───"]
        for i, m in enumerate(matches, 1):
            result = f" → {m.actual_result}" if m.actual_result else " (pending)"
            score = f" ({m.actual_score})" if m.actual_score else ""
            sim = f"{m.similarity_score:.0%}" if m.similarity_score else ""
            lines.append(
                f"  {i}. {m.match_description} [{sim} similar]{result}{score}"
            )
            if m.prediction_summary:
                lines.append(f"     Predicted: {m.prediction_summary}")
            if m.lesson_learned:
                lines.append(f"     Lesson: {m.lesson_learned}")
        lines.append(
            "INSTRUCTION: Consider how these similar past matches inform your analysis. "
            "Note any patterns (e.g., consistently overrating one side)."
        )
        return "\n".join(lines)

    # -- internal helpers --------------------------------------------------

    @staticmethod
    async def _build_calibration_block() -> str:
        """Reuse the calibration logic from analyzer (same DB queries)."""
        # Import here to avoid circular imports — analyzer imports config/db
        from analyzer import _calibration_block
        return await _calibration_block()

    @staticmethod
    async def _build_team_memory_block(team1: str, team2: str) -> str:
        """Reuse team memory logic from analyzer."""
        from analyzer import _team_memory_block
        return await _team_memory_block(team1, team2)

    @staticmethod
    async def _get_accuracy_stats() -> Optional[dict]:
        """Load accuracy stats from DB."""
        try:
            stats = await db.get_calibration_stats()
            if stats and stats.get("n_with_result", 0) >= 3:
                return stats
        except Exception:
            pass
        return None

    @staticmethod
    async def _safe(coro, default=None):
        """Run a coroutine; on any error return the default."""
        try:
            return await coro
        except Exception as exc:
            logger.debug("BaseMemory query failed (non-critical): %s", exc)
            return default
