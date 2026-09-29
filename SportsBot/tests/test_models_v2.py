"""Tests for multi-agent models: Lesson, AnalystOutput, CriticOutput, MemoryContext."""
from __future__ import annotations

import unittest

from models import (
    AnalystOutput,
    CriticIssue,
    CriticOutput,
    Lesson,
    LessonCategory,
    MemoryContext,
    SimilarMatch,
)


class LessonModelTest(unittest.TestCase):
    def test_lesson_defaults(self):
        lesson = Lesson(text="Test lesson", category=LessonCategory.OVERCONFIDENCE)
        self.assertEqual(lesson.confidence, 0.5)
        self.assertEqual(lesson.times_confirmed, 0)
        self.assertIsNone(lesson.id)
        self.assertIsNone(lesson.source_match)

    def test_lesson_category_values(self):
        categories = [e.value for e in LessonCategory]
        self.assertIn("OVERCONFIDENCE", categories)
        self.assertIn("UNDERDOG_MISS", categories)
        self.assertIn("CORRECT_PREDICTION", categories)
        self.assertEqual(len(categories), 9)

    def test_lesson_confidence_bounds(self):
        lesson = Lesson(text="t", category=LessonCategory.H2H_IGNORED, confidence=0.0)
        self.assertEqual(lesson.confidence, 0.0)
        lesson2 = Lesson(text="t", category=LessonCategory.H2H_IGNORED, confidence=1.0)
        self.assertEqual(lesson2.confidence, 1.0)

    def test_lesson_confidence_out_of_range(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Lesson(text="t", category=LessonCategory.H2H_IGNORED, confidence=1.5)


class AnalystOutputTest(unittest.TestCase):
    def _make_analyst(self, **kwargs):
        defaults = dict(
            prob_w1=50.0, prob_draw=25.0, prob_w2=25.0, result_confidence="MEDIUM",
            prob_over_2_5=55.0, prob_under_2_5=45.0, expected_goals=2.5,
            goals_confidence="MEDIUM", asian_handicap_rec="A -0.5",
            asian_handicap_confidence="MEDIUM", prob_btts_yes=50.0,
            prob_btts_no=50.0, btts_confidence="MEDIUM",
            top_recommendation="A Win", top_confidence_pct=55.0,
            risk_level="MEDIUM", analysis_summary="Test",
        )
        defaults.update(kwargs)
        return AnalystOutput(**defaults)

    def test_analyst_defaults(self):
        a = self._make_analyst()
        self.assertEqual(a.reasoning, "")
        self.assertEqual(a.key_assumptions, [])
        self.assertEqual(a.lesson_applications, [])
        self.assertEqual(a.data_warnings, [])

    def test_analyst_with_reasoning(self):
        a = self._make_analyst(
            reasoning="Strong form advantage",
            key_assumptions=["Home advantage +7%"],
            lesson_applications=["Applied lesson: don't overrate favorites"],
        )
        self.assertEqual(len(a.key_assumptions), 1)
        self.assertEqual(len(a.lesson_applications), 1)

    def test_analyst_to_dict_compatible_with_prediction_report(self):
        """AnalystOutput dict should be usable to construct PredictionReport."""
        from models import PredictionReport
        a = self._make_analyst(team1_strengths=["good"], team2_weaknesses=["bad"])
        d = a.model_dump()
        # Remove analyst-only fields
        for key in ("reasoning", "key_assumptions", "lesson_applications"):
            d.pop(key, None)
        d["team1"] = "A"
        d["team2"] = "B"
        report = PredictionReport(**d)
        self.assertEqual(report.prob_w1, 50.0)


class CriticOutputTest(unittest.TestCase):
    def test_critic_defaults(self):
        c = CriticOutput()
        self.assertEqual(c.overall_assessment, "APPROVE")
        self.assertTrue(c.lessons_properly_applied)
        self.assertEqual(c.issues_found, [])
        self.assertIsNone(c.adjusted_probabilities)

    def test_critic_with_issues(self):
        c = CriticOutput(
            issues_found=[
                CriticIssue(field="prob_w1", issue="Overconfident", severity="HIGH"),
            ],
            overall_assessment="REVISE",
            adjusted_probabilities={"prob_w1": 45.0, "prob_draw": 28.0, "prob_w2": 27.0},
        )
        self.assertEqual(len(c.issues_found), 1)
        self.assertEqual(c.issues_found[0].severity, "HIGH")
        self.assertIn("prob_w1", c.adjusted_probabilities)

    def test_critic_serialization_roundtrip(self):
        c = CriticOutput(
            issues_found=[CriticIssue(field="risk", issue="Too low")],
            overall_assessment="REJECT",
            reasoning="Analyst ignored H2H data",
        )
        d = c.model_dump()
        c2 = CriticOutput(**d)
        self.assertEqual(c2.overall_assessment, "REJECT")
        self.assertEqual(len(c2.issues_found), 1)


class MemoryContextTest(unittest.TestCase):
    def test_memory_context_empty(self):
        ctx = MemoryContext()
        self.assertEqual(ctx.similar_matches, [])
        self.assertEqual(ctx.relevant_lessons, [])
        self.assertIsNone(ctx.accuracy_stats)
        self.assertEqual(ctx.team_memory, "")

    def test_memory_context_with_data(self):
        ctx = MemoryContext(
            similar_matches=[
                SimilarMatch(
                    match_description="Arsenal vs Chelsea",
                    prediction_summary="Arsenal Win 52%",
                    actual_result="W1",
                    similarity_score=0.87,
                ),
            ],
            relevant_lessons=[
                Lesson(
                    text="Don't overrate favorites",
                    category=LessonCategory.OVERCONFIDENCE,
                    confidence=0.8,
                    times_confirmed=3,
                ),
            ],
            accuracy_stats={"acc_1x2": 62.5, "n_with_result": 10},
        )
        self.assertEqual(len(ctx.similar_matches), 1)
        self.assertAlmostEqual(ctx.similar_matches[0].similarity_score, 0.87)
        self.assertEqual(ctx.relevant_lessons[0].times_confirmed, 3)


if __name__ == "__main__":
    unittest.main()
