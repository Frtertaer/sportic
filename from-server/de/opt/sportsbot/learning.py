"""Self-learning module — analyses prediction errors and generates lessons.

After a match result arrives, this module:
  1. Loads the original prediction from DB
  2. Compares predicted vs actual outcomes
  3. Calls Claude Sonnet to analyse the error and generate a Lesson
  4. Checks for duplicate lessons via semantic similarity in ChromaDB
  5. Saves new lessons (or increments existing ones)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import anthropic
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

import config
import database as db
from memory import get_shared_vector_memory
from models import Lesson, LessonCategory

logger = logging.getLogger(__name__)

_OPENCLAW_FALLBACK_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    ConnectionError,
    TimeoutError,
    ValueError,
    json.JSONDecodeError,
)

_anthropic_client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
_openclaw_client = AsyncOpenAI(
    api_key=config.OPENCLAW_GATEWAY_TOKEN,
    base_url=config.OPENCLAW_GATEWAY_URL,
)

# ── Lesson generation prompt ──────────────────────────────────────────────────

_LESSON_SYSTEM = """You are a football prediction error analyst.
Given a prediction and the actual result, identify what went wrong (or right) and extract a structured lesson.

OUTPUT: respond with ONLY a valid JSON object, no text/markdown/fences:
{
  "text": "<one sentence describing the pattern or insight>",
  "category": "<one of: OVERCONFIDENCE, UNDERDOG_MISS, FORM_REVERSAL, H2H_IGNORED, INJURY_IMPACT, MARKET_SIGNAL, CORRECT_PREDICTION, TOTAL_GOALS_ERROR, BTTS_ERROR>",
  "confidence": <float 0.2-0.8, higher if pattern is clear>
}

RULES:
- If the prediction was correct AND confident (top_confidence >= 60%), use CORRECT_PREDICTION with confidence 0.3-0.5
- If the 1X2 result was wrong, identify the most likely error category
- If only goals/BTTS was wrong, use TOTAL_GOALS_ERROR or BTTS_ERROR
- Keep lesson text concise (max 15 words), actionable, and specific
- confidence: 0.2-0.4 for ambiguous errors, 0.5-0.7 for clear errors, 0.7-0.8 for obvious patterns"""

_LESSON_USER = """MATCH: {team1} vs {team2}
ACTUAL RESULT: {actual_result} (Score: {score})

PREDICTION MADE:
- 1X2: {team1} {prob_w1:.0f}% / Draw {prob_draw:.0f}% / {team2} {prob_w2:.0f}%
- Top recommendation: {top_rec} ({top_conf:.0f}% confidence)
- Expected goals: {expected_goals:.1f} (O2.5: {prob_over:.0f}% / U2.5: {prob_under:.0f}%)
- BTTS: Yes {prob_btts_yes:.0f}% / No {prob_btts_no:.0f}%
- Risk level: {risk_level}

OUTCOME ANALYSIS:
- 1X2 prediction: {result_correct}
- Goals O/U 2.5: {goals_correct}
- BTTS: {btts_correct}

What lesson should be learned? Return ONLY JSON."""


# ── LearningModule ────────────────────────────────────────────────────────────

class LearningModule:
    """Analyses prediction errors and generates self-learning lessons."""

    def __init__(
        self,
        client: Optional[anthropic.AsyncAnthropic] = None,
        openclaw_client: Optional[AsyncOpenAI] = None,
    ) -> None:
        self._client = client or _anthropic_client
        self._openclaw_client = openclaw_client or _openclaw_client

    async def process_result(
        self,
        prediction_id: int,
        actual_result: str,
        score: str,
    ) -> Optional[int]:
        """Analyse a resolved prediction and generate/update a lesson.

        Returns the lesson DB ID if created/updated, or None.
        """
        # 1. Load prediction from DB
        pred = await db.get_resolved_prediction(prediction_id)
        if not pred:
            logger.debug("Prediction %d not found — skipping learning", prediction_id)
            return None

        # 2. Compute error metrics
        team1 = pred["team1"]
        team2 = pred["team2"]
        prob_w1 = pred.get("prob_w1", 33.3)
        prob_draw = pred.get("prob_draw", 33.3)
        prob_w2 = pred.get("prob_w2", 33.3)

        # Was 1X2 correct?
        predicted_result = "W1" if prob_w1 >= prob_draw and prob_w1 >= prob_w2 else (
            "DRAW" if prob_draw >= prob_w2 else "W2"
        )
        result_correct = "CORRECT" if predicted_result == actual_result else "WRONG"

        # Was O/U 2.5 correct?
        try:
            goals = sum(int(g) for g in score.replace(" ", "").split("-"))
        except (ValueError, AttributeError):
            goals = 0
        prob_over = pred.get("prob_over_2_5", 50.0) or 50.0
        prob_under = pred.get("prob_under_2_5")
        if prob_under is None:
            prob_under = 100.0 - prob_over
        predicted_over = prob_over > 50.0
        actual_over = goals > 2.5
        goals_correct = "CORRECT" if predicted_over == actual_over else "WRONG"

        # Was BTTS correct?
        prob_btts_yes = pred.get("prob_btts_yes", 50.0) or 50.0
        prob_btts_no = pred.get("prob_btts_no")
        if prob_btts_no is None:
            prob_btts_no = 100.0 - prob_btts_yes
        predicted_btts = prob_btts_yes > 50.0
        try:
            parts = score.replace(" ", "").split("-")
            actual_btts = int(parts[0]) > 0 and int(parts[1]) > 0
        except (ValueError, IndexError, AttributeError):
            actual_btts = False
        btts_correct = "CORRECT" if predicted_btts == actual_btts else "WRONG"

        # If everything was correct and confidence was low, skip
        if result_correct == "CORRECT" and goals_correct == "CORRECT" and btts_correct == "CORRECT":
            top_conf = pred.get("top_confidence_pct", 50.0)
            if top_conf < 60.0:
                logger.debug("All correct but low confidence — no lesson needed")
                return None

        # 3. Call Sonnet to generate lesson
        try:
            lesson_data = await self._generate_lesson(
                team1=team1, team2=team2,
                actual_result=actual_result, score=score,
                prob_w1=prob_w1, prob_draw=prob_draw, prob_w2=prob_w2,
                top_rec=pred.get("top_rec") or "?",
                top_conf=pred.get("top_confidence_pct") or 50.0,
                expected_goals=pred.get("expected_goals") or 2.5,
                prob_over=prob_over, prob_under=prob_under,
                prob_btts_yes=prob_btts_yes, prob_btts_no=prob_btts_no,
                risk_level=pred.get("risk_level") or "MEDIUM",
                result_correct=result_correct,
                goals_correct=goals_correct,
                btts_correct=btts_correct,
            )
        except Exception as exc:
            logger.warning("Lesson generation failed: %s", exc)
            return None

        if not lesson_data:
            return None

        # 4. Deduplicate via vector similarity
        lesson_id = await self._deduplicate_and_save(
            lesson_data, team1, team2, prediction_id,
        )
        return lesson_id

    async def _generate_lesson(self, **kwargs) -> Optional[dict]:
        """Call Claude Sonnet to generate a lesson from prediction error."""
        user_prompt = _LESSON_USER.format(**kwargs)
        raw = await self._call_backend(user_prompt)
        from analyzer import _extract_json
        parsed = json.loads(_extract_json(raw))

        # Validate category
        try:
            LessonCategory(parsed["category"])
        except (ValueError, KeyError):
            logger.warning("Invalid lesson category: %s", parsed.get("category"))
            return None

        return parsed

    async def _call_backend(self, user_prompt: str) -> str:
        if not config.USE_OPENCLAW:
            return await self._call_anthropic(user_prompt)

        try:
            return await self._call_openclaw(user_prompt)
        except _OPENCLAW_FALLBACK_EXCEPTIONS as exc:
            logger.warning("Learning OpenClaw failed, falling back to Anthropic: %s", exc)
            return await self._call_anthropic(user_prompt)

    async def _call_anthropic(self, user_prompt: str) -> str:
        response = await self._client.messages.create(
            model=config.SONNET_MODEL,
            max_tokens=512,
            system=_LESSON_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text.strip()

    async def _call_openclaw(self, user_prompt: str) -> str:
        response = await self._openclaw_client.chat.completions.create(
            model=config.OPENCLAW_MODEL,
            max_tokens=512,
            messages=[
                {"role": "system", "content": _LESSON_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("OpenClaw lesson response is empty")
        return content.strip()

    async def _deduplicate_and_save(
        self,
        lesson_data: dict,
        team1: str, team2: str,
        prediction_id: int,
    ) -> Optional[int]:
        """Check for duplicate lessons, then save or increment."""
        text = lesson_data["text"]
        category = lesson_data["category"]
        confidence = float(lesson_data.get("confidence", 0.5))
        vm = None

        # Try semantic dedup via ChromaDB
        try:
            vm = await get_shared_vector_memory()
            existing = await vm.find_relevant_lessons(
                f"{category}: {text}", top_k=3,
            )
            for ex_lesson in existing:
                # Check if same category and high semantic similarity
                ex_cat = ex_lesson.category.value if hasattr(ex_lesson.category, "value") else str(ex_lesson.category)
                if ex_cat == category and ex_lesson.id:
                    logger.info(
                        "Duplicate lesson found (id=%d), incrementing confirmed count",
                        ex_lesson.id,
                    )
                    await db.increment_lesson_confirmed(ex_lesson.id)
                    return ex_lesson.id
        except Exception as exc:
            logger.debug("Dedup check failed (non-critical): %s", exc)

        # Save new lesson
        source_match = f"{team1} vs {team2}"
        lesson_id = await db.save_lesson(
            text=text,
            category=category,
            confidence=confidence,
            source_match=source_match,
            source_prediction_id=prediction_id,
        )
        logger.info("New lesson saved (id=%d): [%s] %s", lesson_id, category, text)

        # Embed in ChromaDB
        try:
            lesson = Lesson(
                id=lesson_id,
                text=text,
                category=LessonCategory(category),
                confidence=confidence,
            )
            if vm is None:
                vm = await get_shared_vector_memory()
            await vm.store_lesson(lesson, lesson_db_id=lesson_id)
        except Exception as exc:
            logger.debug("Lesson embedding failed (non-critical): %s", exc)

        return lesson_id
