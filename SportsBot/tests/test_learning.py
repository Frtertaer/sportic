"""Tests for the learning module (LearningModule.process_result).

All Claude API calls are mocked.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
import database as db
from models import PredictionReport
from memory import close_shared_vector_memory


def _make_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


_LESSON_JSON = json.dumps({
    "text": "Don't overrate home teams with poor recent form",
    "category": "OVERCONFIDENCE",
    "confidence": 0.65,
})

_CORRECT_LESSON_JSON = json.dumps({
    "text": "Home advantage reliable for top-4 teams",
    "category": "CORRECT_PREDICTION",
    "confidence": 0.4,
})


def _fake_report(team1="Arsenal", team2="Chelsea", **overrides) -> PredictionReport:
    """Build a minimal PredictionReport for testing."""
    defaults = dict(
        team1=team1, team2=team2,
        prob_w1=60.0, prob_draw=22.0, prob_w2=18.0,
        result_confidence="MEDIUM",
        prob_over_2_5=55.0, prob_under_2_5=45.0,
        expected_goals=2.7, goals_confidence="MEDIUM",
        asian_handicap_rec="Arsenal -0.5", asian_handicap_confidence="MEDIUM",
        prob_btts_yes=52.0, prob_btts_no=48.0, btts_confidence="MEDIUM",
        top_recommendation="Arsenal Win", top_confidence_pct=60.0,
        risk_level="MEDIUM",
        analysis_summary="Test prediction.",
    )
    defaults.update(overrides)
    return PredictionReport(**defaults)


class LearningModuleTest(unittest.IsolatedAsyncioTestCase):
    _tmp_db: str = ""
    _tmp_chroma: str = ""

    async def asyncSetUp(self):
        await close_shared_vector_memory()
        self._tmp_db = tempfile.mktemp(suffix=".db")
        db.DB_PATH = type(db.DB_PATH)(self._tmp_db)
        await db.init_db()

        self._tmp_chroma = tempfile.mkdtemp(prefix="learn_chroma_")
        config.CHROMADB_PATH = self._tmp_chroma
        self._orig_pinecone = config.PINECONE_ENABLED
        config.PINECONE_ENABLED = False

    async def asyncTearDown(self):
        await close_shared_vector_memory()
        config.PINECONE_ENABLED = self._orig_pinecone
        try:
            os.unlink(self._tmp_db)
        except OSError:
            pass
        shutil.rmtree(self._tmp_chroma, ignore_errors=True)

    async def _insert_prediction(self, **report_overrides) -> int:
        """Insert a fake prediction via a PredictionReport and return its ID."""
        report = _fake_report(**report_overrides)
        return await db.save_prediction(report, completeness=0.75, user_id=123)

    async def test_process_wrong_prediction_creates_lesson(self):
        """Wrong 1X2 prediction → lesson should be created."""
        from learning import LearningModule

        pred_id = await self._insert_prediction()
        await db.mark_result("Arsenal", "Chelsea", "0-2")

        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=_make_response(_LESSON_JSON)
        )
        learner = LearningModule(client)

        lesson_id = await learner.process_result(pred_id, "W2", "0-2")

        self.assertIsNotNone(lesson_id)
        self.assertIsInstance(lesson_id, int)
        lessons = await db.get_lessons()
        self.assertTrue(any(l["id"] == lesson_id for l in lessons))

    async def test_process_correct_confident_creates_lesson(self):
        """Correct + confident prediction → CORRECT_PREDICTION lesson."""
        from learning import LearningModule

        pred_id = await self._insert_prediction()
        await db.mark_result("Arsenal", "Chelsea", "2-1")
        resolved = await db.get_resolved_prediction(pred_id)
        self.assertEqual(resolved["top_confidence_pct"], 60.0)
        self.assertEqual(resolved["expected_goals"], 2.7)
        self.assertEqual(resolved["risk_level"], "MEDIUM")

        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=_make_response(_CORRECT_LESSON_JSON)
        )
        learner = LearningModule(client)

        lesson_id = await learner.process_result(pred_id, "W1", "2-1")
        self.assertIsNotNone(lesson_id)
        client.messages.create.assert_called_once()

    async def test_process_correct_low_confidence_skips(self):
        """Correct but low-confidence prediction → skip lesson."""
        from learning import LearningModule

        pred_id = await self._insert_prediction(
            team1="A", team2="B",
            prob_w1=40.0, prob_draw=30.0, prob_w2=30.0,
            prob_over_2_5=50.0, prob_under_2_5=50.0,
            prob_btts_yes=50.0, prob_btts_no=50.0,
            top_recommendation="A Win",
            top_confidence_pct=40.0,
            risk_level="HIGH",
        )
        await db.mark_result("A", "B", "1-0")
        resolved = await db.get_resolved_prediction(pred_id)
        self.assertEqual(resolved["top_confidence_pct"], 40.0)
        self.assertEqual(resolved["expected_goals"], 2.7)
        self.assertEqual(resolved["risk_level"], "HIGH")

        client = AsyncMock()
        learner = LearningModule(client)

        lesson_id = await learner.process_result(pred_id, "W1", "1-0")
        # All markets correct + low confidence → no lesson
        self.assertIsNone(lesson_id)
        client.messages.create.assert_not_called()

    async def test_process_nonexistent_prediction(self):
        """Non-existent prediction ID → returns None."""
        from learning import LearningModule

        client = AsyncMock()
        learner = LearningModule(client)

        result = await learner.process_result(99999, "W1", "2-0")
        self.assertIsNone(result)

    async def test_process_api_failure_returns_none(self):
        """API failure in lesson generation → returns None gracefully."""
        from learning import LearningModule

        pred_id = await self._insert_prediction()
        await db.mark_result("Arsenal", "Chelsea", "0-1")

        client = AsyncMock()
        client.messages.create = AsyncMock(
            side_effect=Exception("API timeout")
        )
        learner = LearningModule(client)

        result = await learner.process_result(pred_id, "W2", "0-1")
        self.assertIsNone(result)

    async def test_process_result_uses_openclaw_path_without_direct_anthropic(self):
        """USE_OPENCLAW path should not require a direct Anthropic client."""
        from learning import LearningModule

        pred_id = await self._insert_prediction()
        await db.mark_result("Arsenal", "Chelsea", "0-2")

        with patch("learning.config.USE_OPENCLAW", True):
            with patch.object(
                LearningModule,
                "_call_openclaw",
                new=AsyncMock(return_value=_LESSON_JSON),
            ):
                with patch.object(
                    LearningModule,
                    "_call_anthropic",
                    new=AsyncMock(side_effect=AssertionError("Anthropic fallback should not be used")),
                ):
                    learner = LearningModule(client=None, openclaw_client=None)
                    lesson_id = await learner.process_result(pred_id, "W2", "0-2")

        self.assertIsNotNone(lesson_id)

    async def test_process_result_prefers_persisted_complement_probabilities(self):
        from learning import LearningModule

        pred_id = await self._insert_prediction(
            prob_over_2_5=53.0,
            prob_under_2_5=47.0,
            prob_btts_yes=41.0,
            prob_btts_no=59.0,
        )
        await db.mark_result("Arsenal", "Chelsea", "1-0")

        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=_make_response(_LESSON_JSON))
        learner = LearningModule(client)

        await learner.process_result(pred_id, "W1", "1-0")

        prompt = client.messages.create.await_args.kwargs["messages"][0]["content"]
        self.assertIn("U2.5: 47%", prompt)
        self.assertIn("No 59%", prompt)


if __name__ == "__main__":
    unittest.main()
