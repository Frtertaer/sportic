"""Tests for memory.vector_memory (ChromaDB local store)."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import config
from models import Lesson, LessonCategory


class VectorMemoryTest(unittest.IsolatedAsyncioTestCase):
    """Test VectorMemory with a disposable ChromaDB directory."""

    _tmp_dir: str = ""

    async def asyncSetUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="chroma_test_")
        # Point config at temp dir so tests don't pollute real data
        config.CHROMADB_PATH = self._tmp_dir
        # Disable Pinecone for unit tests
        self._orig_pinecone = config.PINECONE_ENABLED
        config.PINECONE_ENABLED = False

        from memory.vector_memory import VectorMemory
        self.vm = VectorMemory()
        await self.vm.initialize()

    async def asyncTearDown(self):
        config.PINECONE_ENABLED = self._orig_pinecone
        try:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except OSError:
            pass

    # -- store + retrieve matches ------------------------------------------

    async def test_store_and_find_match(self):
        await self.vm.store_match(
            match_id="m1",
            team1="Arsenal", team2="Chelsea", league="EPL",
            form="Arsenal WWWDL, Chelsea LDWWW",
            prediction="Arsenal Win 55%",
        )
        results = await self.vm.find_similar_matches("Arsenal", "Chelsea")
        self.assertEqual(len(results), 1)
        self.assertIn("Arsenal", results[0].match_description)
        self.assertIn("Chelsea", results[0].match_description)
        self.assertGreater(results[0].similarity_score, 0.0)

    async def test_find_returns_most_similar(self):
        await self.vm.store_match(
            match_id="m_epl", team1="Liverpool", team2="Man City",
            league="EPL", prediction="Draw 35%",
        )
        await self.vm.store_match(
            match_id="m_liga", team1="Barcelona", team2="Real Madrid",
            league="La Liga", prediction="Barcelona Win 48%",
        )
        results = await self.vm.find_similar_matches(
            "Liverpool", "Man City", context="EPL title race",
        )
        # The Liverpool vs Man City match should rank higher
        self.assertTrue(len(results) >= 1)
        self.assertIn("Liverpool", results[0].match_description)

    async def test_update_match_result(self):
        await self.vm.store_match(
            match_id="m_upd", team1="PSG", team2="Marseille",
            league="Ligue 1", prediction="PSG Win 60%",
        )
        await self.vm.update_match_result("m_upd", result="W1", score="3-1")
        # Query should still find it
        results = await self.vm.find_similar_matches("PSG", "Marseille")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].actual_result, "W1")
        self.assertEqual(results[0].actual_score, "3-1")

    async def test_update_nonexistent_match(self):
        """update_match_result on missing ID should not raise."""
        await self.vm.update_match_result("nonexistent_99", result="W2")

    # -- store + retrieve lessons ------------------------------------------

    async def test_store_and_find_lesson(self):
        lesson = Lesson(
            text="Favorites overrated after CL midweek games",
            category=LessonCategory.OVERCONFIDENCE,
            confidence=0.7,
            times_confirmed=2,
        )
        await self.vm.store_lesson(lesson, lesson_db_id=1)
        results = await self.vm.find_relevant_lessons(
            "overconfidence in favorites after European matches",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].category, LessonCategory.OVERCONFIDENCE)

    async def test_find_lessons_empty_collection(self):
        results = await self.vm.find_relevant_lessons("anything")
        self.assertEqual(results, [])

    # -- team patterns -----------------------------------------------------

    async def test_store_and_find_team_pattern(self):
        await self.vm.store_team_pattern(
            team="Bayern Munich",
            pattern_id="tp_bayern_1",
            text="Bayern tends to dominate possession (65%+) in home Bundesliga matches",
        )
        results = await self.vm.find_team_patterns("Bayern Munich")
        self.assertEqual(len(results), 1)
        self.assertIn("Bayern", results[0]["team"])
        self.assertGreater(results[0]["similarity"], 0.0)

    async def test_find_team_patterns_empty(self):
        results = await self.vm.find_team_patterns("Unknown FC")
        self.assertEqual(results, [])

    # -- stats -------------------------------------------------------------

    async def test_stats(self):
        await self.vm.store_match(
            match_id="s1", team1="A", team2="B", league="Test",
        )
        lesson = Lesson(text="test", category=LessonCategory.BTTS_ERROR)
        await self.vm.store_lesson(lesson, lesson_db_id=100)

        stats = await self.vm.get_stats()
        self.assertEqual(stats["matches"], 1)
        self.assertEqual(stats["lessons"], 1)
        self.assertEqual(stats["team_patterns"], 0)

    # -- upsert idempotency ------------------------------------------------

    async def test_upsert_idempotent(self):
        """Storing the same match twice should not duplicate."""
        for _ in range(3):
            await self.vm.store_match(
                match_id="dup1", team1="X", team2="Y", league="Test",
            )
        stats = await self.vm.get_stats()
        self.assertEqual(stats["matches"], 1)

    # -- lifecycle ---------------------------------------------------------

    async def test_ready_flag(self):
        self.assertTrue(self.vm.ready)


if __name__ == "__main__":
    unittest.main()
