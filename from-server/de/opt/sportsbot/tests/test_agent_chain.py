"""Tests for the multi-agent chain (Analyst → Critic → Synthesis).

All Claude API calls are mocked — no real API keys needed.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.model_utils import AgentClients
from models import AnalystOutput, CriticIssue, CriticOutput, MemoryContext


# --- Fake API response helper ---

def _make_response(text: str):
    """Simulate an Anthropic Messages response."""
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


# --- Sample JSON payloads ---

_ANALYST_JSON = json.dumps({
    "prob_w1": 55.0, "prob_draw": 25.0, "prob_w2": 20.0,
    "result_confidence": "MEDIUM",
    "prob_over_2_5": 60.0, "prob_under_2_5": 40.0,
    "expected_goals": 2.8, "goals_confidence": "MEDIUM",
    "asian_handicap_rec": "Arsenal -0.5", "asian_handicap_confidence": "MEDIUM",
    "prob_btts_yes": 55.0, "prob_btts_no": 45.0, "btts_confidence": "MEDIUM",
    "team1_strengths": ["Strong home form"],
    "team1_weaknesses": ["Defensive lapses"],
    "team2_strengths": ["Counter-attack pace"],
    "team2_weaknesses": ["Away form poor"],
    "top_recommendation": "Arsenal Win",
    "top_recommendation_odds": 1.85,
    "top_confidence_pct": 55.0,
    "risk_level": "MEDIUM",
    "analysis_summary": "Arsenal have edge at home.",
    "data_warnings": [],
    "reasoning": "Arsenal's home form and H2H advantage suggest a win.",
    "key_assumptions": ["Arsenal at home", "No major injuries"],
    "lesson_applications": ["Applied: don't overrate favourites"],
})

_CRITIC_APPROVE_JSON = json.dumps({
    "issues_found": [],
    "adjusted_probabilities": None,
    "lessons_properly_applied": True,
    "bias_detected": None,
    "overall_assessment": "APPROVE",
    "reasoning": "Analysis is sound and well-supported by data.",
})

_CRITIC_REVISE_JSON = json.dumps({
    "issues_found": [
        {"field": "prob_w1", "issue": "Slightly overconfident", "severity": "MEDIUM",
         "suggested_correction": "Reduce to 52%"},
    ],
    "adjusted_probabilities": {"prob_w1": 52.0, "prob_draw": 27.0, "prob_w2": 21.0},
    "lessons_properly_applied": True,
    "bias_detected": "Slight home bias",
    "overall_assessment": "REVISE",
    "reasoning": "Analyst overweights home advantage.",
})

_SYNTHESIS_JSON = json.dumps({
    "prob_w1": 53.0, "prob_draw": 26.0, "prob_w2": 21.0,
    "result_confidence": "MEDIUM",
    "prob_over_2_5": 58.0, "prob_under_2_5": 42.0,
    "expected_goals": 2.7, "goals_confidence": "MEDIUM",
    "asian_handicap_rec": "Arsenal -0.5", "asian_handicap_confidence": "MEDIUM",
    "prob_btts_yes": 54.0, "prob_btts_no": 46.0, "btts_confidence": "MEDIUM",
    "team1_strengths": ["Strong home form"],
    "team1_weaknesses": ["Defensive lapses"],
    "team2_strengths": ["Counter-attack pace"],
    "team2_weaknesses": ["Away form poor"],
    "top_recommendation": "Arsenal Win",
    "top_recommendation_odds": 1.85,
    "top_confidence_pct": 53.0,
    "risk_level": "MEDIUM",
    "analysis_summary": "Arsenal favoured at home, adjusted for slight overconfidence.",
    "data_warnings": [],
})


class AgentChainTest(unittest.IsolatedAsyncioTestCase):
    """Test the full chain with mocked API responses."""

    async def test_full_chain_approve(self):
        """APPROVE path: Analyst → Critic approves → Synthesis outputs final."""
        from agents.chain import run_agent_chain

        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=[
            _make_response(_ANALYST_JSON),
            _make_response(_CRITIC_APPROVE_JSON),
            _make_response(_SYNTHESIS_JSON),
        ])

        result = await run_agent_chain(
            client=client,
            team1="Arsenal", team2="Chelsea",
            data_context="Section 1: form data...",
            memory_context=MemoryContext(),
            completeness_pct=75, ok_sources=3, total_sources=5,
            warnings_block="",
        )

        self.assertIsNotNone(result)
        self.assertIn("prob_w1", result)
        self.assertEqual(client.messages.create.call_count, 3)

    async def test_chain_ignores_x_social_context_without_validator(self):
        from agents.chain import run_agent_chain

        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=[
            _make_response(_ANALYST_JSON),
            _make_response(_CRITIC_APPROVE_JSON),
            _make_response(_SYNTHESIS_JSON),
        ])

        with (
            patch("config.AGENT_CRITIC_PROVIDER", "anthropic"),
            patch("config.AGENT_SYNTHESIS_PROVIDER", "anthropic"),
        ):
            result = await run_agent_chain(
                client=client,
                team1="Arsenal", team2="Chelsea",
                data_context="Section 1: form data...",
                memory_context=MemoryContext(),
                completeness_pct=75, ok_sources=3, total_sources=5,
                warnings_block="",
                x_social_context="late lineup rumour",
            )

        self.assertIsNotNone(result)
        self.assertNotIn("x_assessment", result)
        self.assertEqual(client.messages.create.call_count, 3)

    async def test_full_chain_revise(self):
        """REVISE path: Critic suggests corrections → Synthesis incorporates."""
        from agents.chain import run_agent_chain

        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=[
            _make_response(_ANALYST_JSON),
            _make_response(_CRITIC_REVISE_JSON),
            _make_response(_SYNTHESIS_JSON),
        ])

        result = await run_agent_chain(
            client=client,
            team1="Arsenal", team2="Chelsea",
            data_context="Section 1: form data...",
            memory_context=MemoryContext(),
            completeness_pct=75, ok_sources=3, total_sources=5,
            warnings_block="",
        )

        self.assertIsNotNone(result)
        # Verify 3 API calls were made
        self.assertEqual(client.messages.create.call_count, 3)

    async def test_analyst_failure_returns_none(self):
        """If Analyst (Opus) fails, chain returns None for caller to fall back."""
        from agents.chain import run_agent_chain

        client = AsyncMock()
        client.messages.create = AsyncMock(
            side_effect=Exception("Opus unavailable")
        )

        result = await run_agent_chain(
            client=client,
            team1="Arsenal", team2="Chelsea",
            data_context="data...",
            memory_context=MemoryContext(),
            completeness_pct=50, ok_sources=2, total_sources=5,
            warnings_block="",
        )

        self.assertIsNone(result)

    async def test_critic_failure_uses_default_approve(self):
        """If Critic fails, Synthesis runs with default APPROVE."""
        from agents.chain import run_agent_chain

        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(_ANALYST_JSON)
            elif call_count == 2:
                raise Exception("Sonnet timeout")
            else:
                return _make_response(_SYNTHESIS_JSON)

        client = AsyncMock()
        client.messages.create = mock_create

        result = await run_agent_chain(
            client=client,
            team1="Arsenal", team2="Chelsea",
            data_context="data...",
            memory_context=MemoryContext(),
            completeness_pct=75, ok_sources=3, total_sources=5,
            warnings_block="",
        )

        self.assertIsNotNone(result)
        self.assertIn("prob_w1", result)

    async def test_synthesis_failure_uses_analyst_output(self):
        """If Synthesis fails, chain returns Analyst output (minus reasoning)."""
        from agents.chain import run_agent_chain

        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(_ANALYST_JSON)
            elif call_count == 2:
                return _make_response(_CRITIC_APPROVE_JSON)
            else:
                raise Exception("Opus failed on synthesis")

        client = AsyncMock()
        client.messages.create = mock_create

        result = await run_agent_chain(
            client=client,
            team1="Arsenal", team2="Chelsea",
            data_context="data...",
            memory_context=MemoryContext(),
            completeness_pct=75, ok_sources=3, total_sources=5,
            warnings_block="",
        )

        self.assertIsNotNone(result)
        self.assertIn("prob_w1", result)
        # Reasoning fields should be stripped
        self.assertNotIn("reasoning", result)
        self.assertNotIn("key_assumptions", result)


class IndividualAgentTest(unittest.IsolatedAsyncioTestCase):
    """Test each agent in isolation."""

    async def test_analyst_parses_valid_response(self):
        from agents.analyst import run_analyst

        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=_make_response(_ANALYST_JSON)
        )

        output = await run_analyst(
            client=client, team1="A", team2="B",
            data_context="test", memory_block="",
            completeness_pct=50, ok_sources=2, total_sources=5, warnings_block="",
        )

        self.assertIsInstance(output, AnalystOutput)
        self.assertEqual(output.prob_w1, 55.0)
        self.assertIn("Arsenal", output.reasoning)

    async def test_analyst_falls_back_to_role_fallback_when_primary_model_missing(self):
        from agents.analyst import run_analyst

        seen_models: list[str] = []

        async def mock_create(**kwargs):
            seen_models.append(kwargs["model"])
            if kwargs["model"] == "broken-opus":
                raise Exception(
                    "Error code: 404 - {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'model: broken-opus'}}"
                )
            return _make_response(_ANALYST_JSON)

        client = AsyncMock()
        client.messages.create = mock_create

        with (
            patch("agents.analyst.config.AGENT_ANALYST_PROVIDER", "anthropic"),
            patch("agents.analyst.config.AGENT_ANALYST_MODEL", "broken-opus"),
            patch("agents.analyst.config.AGENT_ANALYST_FALLBACK_PROVIDER", "anthropic"),
            patch("agents.analyst.config.AGENT_ANALYST_FALLBACK_MODEL", "claude-sonnet-4-6"),
        ):
            output = await run_analyst(
                client=client, team1="A", team2="B",
                data_context="test", memory_block="",
                completeness_pct=50, ok_sources=2, total_sources=5, warnings_block="",
            )

        self.assertIsInstance(output, AnalystOutput)
        self.assertEqual(seen_models, ["broken-opus", "claude-sonnet-4-6"])

    async def test_critic_parses_approve(self):
        from agents.critic import run_critic

        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=_make_response(_CRITIC_APPROVE_JSON)
        )

        output = await run_critic(
            client=client, team1="A", team2="B",
            data_context="test", analyst_json=_ANALYST_JSON,
            lessons_block="",
        )

        self.assertIsInstance(output, CriticOutput)
        self.assertEqual(output.overall_assessment, "APPROVE")
        self.assertEqual(len(output.issues_found), 0)

    async def test_critic_parses_revise(self):
        from agents.critic import run_critic

        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=_make_response(_CRITIC_REVISE_JSON)
        )

        output = await run_critic(
            client=client, team1="A", team2="B",
            data_context="test", analyst_json=_ANALYST_JSON,
            lessons_block="",
        )

        self.assertEqual(output.overall_assessment, "REVISE")
        self.assertEqual(len(output.issues_found), 1)
        self.assertIsNotNone(output.adjusted_probabilities)

    async def test_critic_uses_openai_role_configuration(self):
        from agents.critic import run_critic

        with (
            patch("agents.critic.config.AGENT_CRITIC_PROVIDER", "openai"),
            patch("agents.critic.config.AGENT_CRITIC_MODEL", "gpt-5.4"),
            patch("agents.critic.config.AGENT_CRITIC_REASONING_EFFORT", "high"),
            patch(
                "agents.critic.generate_text_with_fallback",
                new=AsyncMock(return_value=_CRITIC_APPROVE_JSON),
            ) as call_mock,
        ):
            output = await run_critic(
                clients=AgentClients(),
                team1="A",
                team2="B",
                data_context="test",
                analyst_json=_ANALYST_JSON,
                lessons_block="",
            )

        self.assertEqual(output.overall_assessment, "APPROVE")
        kwargs = call_mock.await_args.kwargs
        self.assertEqual(kwargs["primary_spec"].provider, "openai")
        self.assertEqual(kwargs["primary_spec"].model, "gpt-5.4")
        self.assertEqual(kwargs["primary_spec"].reasoning_effort, "high")

    async def test_synthesis_returns_dict(self):
        from agents.synthesis import run_synthesis

        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=_make_response(_SYNTHESIS_JSON)
        )

        result = await run_synthesis(
            client=client, team1="A", team2="B",
            data_context="test", analyst_json=_ANALYST_JSON,
            critic_json=_CRITIC_APPROVE_JSON,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["prob_w1"], 53.0)

    async def test_synthesis_falls_back_to_role_fallback_when_primary_model_missing(self):
        from agents.synthesis import run_synthesis

        seen_models: list[str] = []

        async def mock_create(**kwargs):
            seen_models.append(kwargs["model"])
            if kwargs["model"] == "broken-opus":
                raise Exception(
                    "Error code: 404 - {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'model: broken-opus'}}"
                )
            return _make_response(_SYNTHESIS_JSON)

        client = AsyncMock()
        client.messages.create = mock_create

        with (
            patch("agents.synthesis.config.AGENT_SYNTHESIS_PROVIDER", "anthropic"),
            patch("agents.synthesis.config.AGENT_SYNTHESIS_MODEL", "broken-opus"),
            patch("agents.synthesis.config.AGENT_SYNTHESIS_FALLBACK_PROVIDER", "anthropic"),
            patch("agents.synthesis.config.AGENT_SYNTHESIS_FALLBACK_MODEL", "claude-sonnet-4-6"),
        ):
            result = await run_synthesis(
                client=client, team1="A", team2="B",
                data_context="test", analyst_json=_ANALYST_JSON,
                critic_json=_CRITIC_APPROVE_JSON,
            )

        self.assertIsInstance(result, dict)
        self.assertEqual(seen_models, ["broken-opus", "claude-sonnet-4-6"])

    async def test_synthesis_uses_gemini_role_configuration(self):
        from agents.synthesis import run_synthesis

        with (
            patch("agents.synthesis.config.AGENT_SYNTHESIS_PROVIDER", "gemini"),
            patch("agents.synthesis.config.AGENT_SYNTHESIS_MODEL", "gemini-3.1-pro-preview"),
            patch(
                "agents.synthesis.generate_text_with_fallback",
                new=AsyncMock(return_value=_SYNTHESIS_JSON),
            ) as call_mock,
        ):
            result = await run_synthesis(
                clients=AgentClients(),
                team1="A",
                team2="B",
                data_context="test",
                analyst_json=_ANALYST_JSON,
                critic_json=_CRITIC_APPROVE_JSON,
            )

        self.assertEqual(result["prob_w1"], 53.0)
        kwargs = call_mock.await_args.kwargs
        self.assertEqual(kwargs["primary_spec"].provider, "gemini")
        self.assertEqual(kwargs["primary_spec"].model, "gemini-3.1-pro-preview")


if __name__ == "__main__":
    unittest.main()
