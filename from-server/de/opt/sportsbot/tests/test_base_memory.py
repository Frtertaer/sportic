"""Tests for memory.base_memory (BaseMemory facade)."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import config
import database as db
from models import Lesson, LessonCategory, MemoryContext, SimilarMatch


class BaseMemoryTest(unittest.IsolatedAsyncioTestCase):
    """Test BaseMemory with a real VectorMemory (temp ChromaDB) + temp SQLite."""

    _tmp_chroma: str = ""
    _tmp_db: str = ""

    async def asyncSetUp(self):
        # Temp ChromaDB
        self._tmp_chroma = tempfile.mkdtemp(prefix="bm_chroma_")
        config.CHROMADB_PATH = self._tmp_chroma
        self._orig_pinecone = config.PINECONE_ENABLED
        config.PINECONE_ENABLED = False

        # Temp SQLite
        self._tmp_db = tempfile.mktemp(suffix=".db")
        db.DB_PATH = type(db.DB_PATH)(self._tmp_db)
        await db.init_db()

        # Initialize VectorMemory + BaseMemory
        from memory.vector_memory import VectorMemory
        from memory.base_memory import BaseMemory

        self.vm = VectorMemory()
        await self.vm.initialize()
        self.bm = BaseMemory(self.vm)

    async def asyncTearDown(self):
        config.PINECONE_ENABLED = self._orig_pinecone
        shutil.rmtree(self._tmp_chroma, ignore_errors=True)
        try:
            os.unlink(self._tmp_db)
        except OSError:
            pass

    async def test_build_context_returns_memory_context(self):
        ctx = await self.bm.build_context("Arsenal", "Chelsea")
        self.assertIsInstance(ctx, MemoryContext)

    async def test_build_context_includes_similar_matches(self):
        """Store a match, then verify it appears in context."""
        await self.vm.store_match(
            match_id="m_ctx1", team1="Arsenal", team2="Chelsea",
            league="EPL", prediction="Arsenal Win 55%",
        )
        ctx = await self.bm.build_context("Arsenal", "Chelsea")
        self.assertTrue(len(ctx.similar_matches) >= 1)
        self.assertIn("Arsenal", ctx.similar_matches[0].match_description)

    async def test_build_context_includes_lessons(self):
        """Store a lesson, then verify it appears in context."""
        lesson = Lesson(
            text="Favorites overrated after CL midweek",
            category=LessonCategory.OVERCONFIDENCE,
            confidence=0.7,
        )
        await self.vm.store_lesson(lesson, lesson_db_id=1)
        ctx = await self.bm.build_context(
            "Arsenal", "Chelsea", context_hint="overconfidence favorites"
        )
        self.assertTrue(len(ctx.relevant_lessons) >= 1)

    async def test_build_context_graceful_on_empty(self):
        """Empty vector store should return empty lists, not crash."""
        ctx = await self.bm.build_context("Unknown FC", "Mystery United")
        self.assertEqual(ctx.similar_matches, [])
        self.assertEqual(ctx.relevant_lessons, [])
        self.assertEqual(ctx.team_memory, "")

    async def test_build_context_calibration_empty_without_data(self):
        """Calibration block should be empty if <5 resolved predictions."""
        ctx = await self.bm.build_context("A", "B")
        self.assertEqual(ctx.calibration, "")

    async def test_build_context_accuracy_stats_none_without_data(self):
        ctx = await self.bm.build_context("A", "B")
        self.assertIsNone(ctx.accuracy_stats)


class FormatHelpersTest(unittest.TestCase):
    """Test the static formatting methods on BaseMemory."""

    def test_format_lessons_for_prompt_empty(self):
        from memory.base_memory import BaseMemory
        result = BaseMemory.format_lessons_for_prompt([])
        self.assertEqual(result, "")

    def test_format_lessons_for_prompt_with_lessons(self):
        from memory.base_memory import BaseMemory
        lessons = [
            Lesson(
                text="Don't overrate favourites after CL midweek",
                category=LessonCategory.OVERCONFIDENCE,
                confidence=0.8,
                times_confirmed=3,
            ),
            Lesson(
                text="H2H ignored in derbies",
                category=LessonCategory.H2H_IGNORED,
                confidence=0.6,
            ),
        ]
        result = BaseMemory.format_lessons_for_prompt(lessons)
        self.assertIn("LESSONS FROM PAST PREDICTIONS", result)
        self.assertIn("OVERCONFIDENCE", result)
        self.assertIn("confirmed 3x", result)
        self.assertIn("H2H_IGNORED", result)

    def test_format_similar_matches_for_prompt_empty(self):
        from memory.base_memory import BaseMemory
        result = BaseMemory.format_similar_matches_for_prompt([])
        self.assertEqual(result, "")

    def test_format_similar_matches_for_prompt_with_data(self):
        from memory.base_memory import BaseMemory
        matches = [
            SimilarMatch(
                match_description="Arsenal vs Chelsea",
                prediction_summary="Arsenal Win 55%",
                actual_result="W1",
                actual_score="2-1",
                similarity_score=0.87,
            ),
        ]
        result = BaseMemory.format_similar_matches_for_prompt(matches)
        self.assertIn("SIMILAR PAST MATCHES", result)
        self.assertIn("Arsenal vs Chelsea", result)
        self.assertIn("87%", result)
        self.assertIn("W1", result)


if __name__ == "__main__":
    unittest.main()
