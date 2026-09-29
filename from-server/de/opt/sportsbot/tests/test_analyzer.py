from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import analyzer
from baseline_model import build_numeric_baseline
from models import (
    AdvancedStatsData,
    MarketSelectionSignal,
    MarketSignalsData,
    MatchContextData,
    MatchData,
    OddsData,
    Player,
    PlayerStatus,
    PredictionReport,
    PredictionFeatureSnapshot,
    ScheduleLoadData,
    SofascoreData,
    SourceStatus,
    StandingsContextData,
    TableEntrySummary,
    TeamScheduleLoadSummary,
    TeamAdvancedMetrics,
    WeatherContextData,
)


def _report_payload() -> str:
    payload = {
        "prob_w1": 45.0,
        "prob_draw": 28.0,
        "prob_w2": 27.0,
        "result_confidence": "MEDIUM",
        "prob_over_2_5": 54.0,
        "prob_under_2_5": 46.0,
        "expected_goals": 2.7,
        "goals_confidence": "MEDIUM",
        "asian_handicap_rec": "Arsenal -0.25",
        "asian_handicap_confidence": "MEDIUM",
        "prob_btts_yes": 57.0,
        "prob_btts_no": 43.0,
        "btts_confidence": "MEDIUM",
        "team1_strengths": ["Home form"],
        "team1_weaknesses": ["Set pieces"],
        "team2_strengths": ["Transition play"],
        "team2_weaknesses": ["Away defending"],
        "top_recommendation": "Arsenal draw no bet",
        "top_recommendation_odds": 1.8,
        "top_confidence_pct": 61.0,
        "risk_level": "MEDIUM",
        "analysis_summary": "Test summary.",
        "data_warnings": [],
    }
    return json.dumps(payload)


class AnalyzerFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._bankroll_guardrails_patcher = patch(
            "analyzer.db.get_bankroll_guardrails",
            new=AsyncMock(return_value=None),
        )
        self._bankroll_guardrails_patcher.start()

    def tearDown(self) -> None:
        self._bankroll_guardrails_patcher.stop()

    async def asyncTearDown(self) -> None:
        analyzer.reset_analysis_runtime_metrics()
        analyzer.reset_live_hybrid_1x2_runtime()

    async def test_pre_ai_gate_skips_expensive_ai_on_low_coverage_no_edge_match(self) -> None:
        analyzer.reset_analysis_runtime_metrics()
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.28,
                implied_prob_w2=0.24,
                over_2_5_odds=1.95,
                under_2_5_odds=1.87,
                btts_yes_odds=1.90,
                btts_no_odds=1.90,
                scrape_success=True,
            ),
            match_context=MatchContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                is_neutral_venue=True,
                is_team1_home=False,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True, applicable=True),
                SourceStatus(key="market_signals", label="Market signals", field_name="market_signals", scrape_success=False, applicable=True),
                SourceStatus(key="form", label="Form/H2H", field_name="flashscore", scrape_success=False, applicable=True),
                SourceStatus(key="squads", label="Squads", field_name="sofascore", scrape_success=False, applicable=True),
            ],
        )

        with (
            patch.object(analyzer.config, "PRE_AI_GATE_ENABLED", True),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_APPLICABLE_COMPLETENESS", 0.56),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_EDGE_PCT", 1.5),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_CANDIDATE_CONFIDENCE", 58.0),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_SUCCESSFUL_SOURCES", 4),
            patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", True),
            patch("analyzer._save_team_memory", new=AsyncMock()),
            patch("analyzer.db.get_numeric_recalibration_profiles", new=AsyncMock(return_value=[])),
            patch("analyzer._enrich_snapshot_with_elo", new=AsyncMock()),
            patch("analyzer._agent_chain_analyze", new=AsyncMock(side_effect=AssertionError("agent chain should not run"))),
            patch("analyzer._single_call_analyze", new=AsyncMock(side_effect=AssertionError("single-call should not run"))),
        ):
            report = await analyzer.analyze_match(data)

        self.assertEqual(report.prompt_version, analyzer.PRE_AI_GATE_PROMPT_VERSION)
        self.assertTrue(any("AI skipped by pre-AI gate" in warning for warning in report.data_warnings))
        self.assertIn("path_pre_ai_gate", report.policy_flags)
        metrics = analyzer.get_analysis_runtime_metrics_snapshot()
        self.assertEqual(metrics["total_calls"], 1)
        self.assertEqual(metrics["pre_ai_gate_skips"], 1)
        self.assertEqual(metrics["agent_chain_runs"], 0)
        self.assertEqual(metrics["single_call_runs"], 0)
        self.assertEqual(metrics["policy_flag_counts"]["path_pre_ai_gate"], 1)

    async def test_pre_ai_gate_allows_normal_path_when_match_has_stronger_edge(self) -> None:
        analyzer.reset_analysis_runtime_metrics()
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.43,
                implied_prob_draw=0.28,
                implied_prob_w2=0.29,
                over_2_5_odds=2.10,
                under_2_5_odds=1.75,
                btts_yes_odds=1.96,
                btts_no_odds=1.84,
                scrape_success=True,
            ),
            advanced_stats=AdvancedStatsData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_metrics=TeamAdvancedMetrics(xg=62.0, xga=25.0, rating=7.4, possession_pct=58.0),
                team2_metrics=TeamAdvancedMetrics(xg=41.0, xga=39.0, rating=6.7, possession_pct=48.0),
                scrape_success=True,
            ),
            standings_context=StandingsContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                league_name="Premier League",
                team1_overall=TableEntrySummary(position=2, points=67),
                team2_overall=TableEntrySummary(position=10, points=41),
                point_gap=26,
                position_gap=8,
                team1_above_team2=True,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True, applicable=True),
                SourceStatus(key="advanced", label="Advanced stats", field_name="advanced_stats", scrape_success=True, applicable=True),
                SourceStatus(key="standings", label="Standings", field_name="standings_context", scrape_success=True, applicable=True),
                SourceStatus(key="form", label="Form/H2H", field_name="flashscore", scrape_success=False, applicable=True),
            ],
        )
        downstream_report = PredictionReport(
            team1="Arsenal",
            team2="Chelsea",
            prob_w1=56.0,
            prob_draw=24.0,
            prob_w2=20.0,
            result_confidence="MEDIUM",
            prob_over_2_5=55.0,
            prob_under_2_5=45.0,
            expected_goals=2.7,
            goals_confidence="MEDIUM",
            asian_handicap_rec="Arsenal -0.5",
            asian_handicap_confidence="MEDIUM",
            prob_btts_yes=53.0,
            prob_btts_no=47.0,
            btts_confidence="MEDIUM",
            team1_strengths=[],
            team1_weaknesses=[],
            team2_strengths=[],
            team2_weaknesses=[],
            top_recommendation="Arsenal",
            top_recommendation_odds=2.25,
            top_confidence_pct=60.0,
            risk_level="MEDIUM",
            analysis_summary="Normal path report.",
        )

        with (
            patch.object(analyzer.config, "PRE_AI_GATE_ENABLED", True),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_APPLICABLE_COMPLETENESS", 0.56),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_EDGE_PCT", 1.5),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_CANDIDATE_CONFIDENCE", 58.0),
            patch.object(analyzer.config, "PRE_AI_GATE_MAX_SUCCESSFUL_SOURCES", 4),
            patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", False),
            patch("analyzer._save_team_memory", new=AsyncMock()),
            patch("analyzer._single_call_analyze", new=AsyncMock(return_value=downstream_report)) as single_call_mock,
        ):
            report = await analyzer.analyze_match(data)

        self.assertEqual(report.analysis_summary, "Normal path report.")
        single_call_mock.assert_awaited_once()
        metrics = analyzer.get_analysis_runtime_metrics_snapshot()
        self.assertEqual(metrics["total_calls"], 1)
        self.assertEqual(metrics["pre_ai_gate_skips"], 0)

    async def test_live_hybrid_1x2_only_adjusts_triplet_and_reuses_model_cache(self) -> None:
        analyzer.reset_analysis_runtime_metrics()
        analyzer.reset_live_hybrid_1x2_runtime()
        data = MatchData(team1="Arsenal", team2="Chelsea")
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            competition_name="Premier League",
            data_completeness=0.82,
            successful_source_count=6,
            total_source_count=8,
            market_prob_w1=49.0,
            market_prob_draw=27.0,
            market_prob_w2=24.0,
            market_prob_over_2_5=53.0,
            market_prob_under_2_5=47.0,
            market_prob_btts_yes=52.0,
            market_prob_btts_no=48.0,
            form_points_last5_diff=4.0,
            xg_diff=6.0,
            standings_point_gap=7.0,
            is_team1_home=True,
        )
        raw = build_numeric_baseline(data, snapshot, recalibration_profiles=[])

        with (
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", True),
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_ALPHA", 0.25),
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_MIN_ROWS", 1500),
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_MIN_COMPLETENESS", 0.55),
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_CACHE_TTL_SECONDS", 3600),
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_TRAIN_LIMIT", 4000),
            patch.object(analyzer.config, "ENSEMBLE_ENABLED", False),
            patch("analyzer.db.get_historical_backtest_rows", new=AsyncMock(return_value=[{}] * 2000)) as rows_mock,
            patch(
                "analyzer.fit_trainable_numeric_model",
                return_value=SimpleNamespace(one_x_two_weights=object(), trained_rows=2000),
            ) as fit_mock,
            patch(
                "analyzer.predict_trainable_numeric_model",
                return_value={
                    "prob_w1": 55.0,
                    "prob_draw": 24.0,
                    "prob_w2": 21.0,
                    "prob_over_2_5": 59.0,
                    "prob_under_2_5": 41.0,
                    "prob_btts_yes": 56.0,
                    "prob_btts_no": 44.0,
                },
            ) as predict_mock,
            patch("analyzer._enrich_snapshot_with_elo", new=AsyncMock()) as _elo_mock,
        ):
            first = await analyzer._build_live_numeric_baseline(data, snapshot, recalibration_profiles=[])
            second = await analyzer._build_live_numeric_baseline(data, snapshot, recalibration_profiles=[])

        self.assertEqual(rows_mock.await_count, 1)
        fit_mock.assert_called_once()
        self.assertEqual(predict_mock.call_count, 2)
        self.assertNotEqual(
            (first["prob_w1"], first["prob_draw"], first["prob_w2"]),
            (raw["prob_w1"], raw["prob_draw"], raw["prob_w2"]),
        )
        self.assertEqual(first["prob_over_2_5"], raw["prob_over_2_5"])
        self.assertEqual(first["prob_under_2_5"], raw["prob_under_2_5"])
        self.assertEqual(first["prob_btts_yes"], raw["prob_btts_yes"])
        self.assertEqual(first["prob_btts_no"], raw["prob_btts_no"])
        self.assertEqual(first, second)
        metrics = analyzer.get_analysis_runtime_metrics_snapshot()
        self.assertEqual(metrics["live_hybrid_1x2_applied"], 2)

    async def test_live_hybrid_1x2_skips_when_completeness_is_too_low(self) -> None:
        analyzer.reset_live_hybrid_1x2_runtime()
        data = MatchData(team1="Arsenal", team2="Chelsea")
        snapshot = PredictionFeatureSnapshot(
            team1="Arsenal",
            team2="Chelsea",
            data_completeness=0.42,
            market_prob_w1=49.0,
            market_prob_draw=27.0,
            market_prob_w2=24.0,
            market_prob_over_2_5=53.0,
            market_prob_under_2_5=47.0,
            market_prob_btts_yes=52.0,
            market_prob_btts_no=48.0,
            is_team1_home=True,
        )

        with (
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", True),
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_MIN_COMPLETENESS", 0.55),
            patch.object(analyzer.config, "ENSEMBLE_ENABLED", False),
            patch("analyzer._enrich_snapshot_with_elo", new=AsyncMock()),
            patch("analyzer.db.get_historical_backtest_rows", new=AsyncMock()) as rows_mock,
        ):
            baseline = await analyzer._build_live_numeric_baseline(data, snapshot, recalibration_profiles=[])

        self.assertEqual(baseline, build_numeric_baseline(data, snapshot, recalibration_profiles=[]))
        rows_mock.assert_not_awaited()

    async def test_openclaw_falls_back_to_anthropic(self) -> None:
        data = MatchData(team1="Arsenal", team2="Chelsea")

        with (
            patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", False),
            patch.object(analyzer.config, "USE_OPENCLAW", True),
            patch("analyzer._call_openclaw", new=AsyncMock(side_effect=ConnectionError("gateway down"))),
            patch("analyzer._call_anthropic", new=AsyncMock(return_value=_report_payload())),
        ):
            report = await analyzer.analyze_match(data)

        self.assertTrue(report.top_recommendation.startswith("No bet -"))
        self.assertEqual(report.team1, "Arsenal")
        self.assertEqual(report.team2, "Chelsea")

    async def test_returns_fallback_report_when_both_backends_fail(self) -> None:
        data = MatchData(team1="Arsenal", team2="Chelsea")

        with (
            patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", False),
            patch.object(analyzer.config, "USE_OPENCLAW", True),
            patch("analyzer._call_openclaw", new=AsyncMock(side_effect=ConnectionError("gateway down"))),
            patch("analyzer._call_anthropic", new=AsyncMock(side_effect=RuntimeError("anthropic down"))),
        ):
            report = await analyzer.analyze_match(data)

        self.assertEqual(report.top_recommendation, "No bet - fallback baseline only")
        self.assertIsNotNone(report.feature_snapshot)
        self.assertTrue(
            any(warning.startswith("Analysis error:") for warning in report.data_warnings)
        )
        self.assertIn("path_fallback_numeric", report.policy_flags)
        self.assertIn("no_bet", report.policy_flags)
        metrics = analyzer.get_analysis_runtime_metrics_snapshot()
        self.assertEqual(metrics["policy_flag_counts"]["path_fallback_numeric"], 1)
        self.assertEqual(metrics["policy_flag_counts"]["no_bet"], 1)

    async def test_single_call_report_injects_methodology_and_prompt_versions(self) -> None:
        data = MatchData(team1="Arsenal", team2="Chelsea")

        with (
            patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", False),
            patch("analyzer._generate_report_dict", new=AsyncMock(return_value=json.loads(_report_payload()))),
            patch("analyzer._save_team_memory", new=AsyncMock()),
            patch("analyzer._calibration_block", new=AsyncMock(return_value="")),
            patch("analyzer._team_memory_block", new=AsyncMock(return_value="")),
        ):
            report = await analyzer.analyze_match(data)

        self.assertEqual(report.methodology_version, analyzer.ANALYSIS_METHODOLOGY_VERSION)
        self.assertEqual(report.prompt_version, analyzer.SINGLE_CALL_PROMPT_VERSION)
        self.assertIn("path_single_call", report.policy_flags)
        metrics = analyzer.get_analysis_runtime_metrics_snapshot()
        self.assertEqual(metrics["policy_flag_counts"]["path_single_call"], 1)


class LocalAgentChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        analyzer.reset_analysis_runtime_metrics()
        analyzer.reset_live_hybrid_1x2_runtime()

    async def test_agent_chain_uses_local_runner_and_postprocesses_result(self) -> None:
        from models import MemoryContext

        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True, applicable=True),
                SourceStatus(key="form", label="Form/H2H", field_name="flashscore", scrape_success=True, applicable=True),
            ],
        )
        local_result = json.loads(_report_payload())
        postprocessed = PredictionReport(
            team1="Arsenal",
            team2="Chelsea",
            prob_w1=45.0,
            prob_draw=28.0,
            prob_w2=27.0,
            result_confidence="MEDIUM",
            prob_over_2_5=54.0,
            prob_under_2_5=46.0,
            expected_goals=2.7,
            goals_confidence="MEDIUM",
            asian_handicap_rec="Arsenal -0.25",
            asian_handicap_confidence="MEDIUM",
            prob_btts_yes=57.0,
            prob_btts_no=43.0,
            btts_confidence="MEDIUM",
            team1_strengths=[],
            team1_weaknesses=[],
            team2_strengths=[],
            team2_weaknesses=[],
            top_recommendation="Arsenal draw no bet",
            top_recommendation_odds=1.8,
            top_confidence_pct=61.0,
            risk_level="MEDIUM",
            analysis_summary="Postprocessed local-chain report.",
        )

        class _FakeBaseMemory:
            def __init__(self, vector_memory) -> None:
                self.vector_memory = vector_memory

            async def build_context(self, team1: str, team2: str) -> MemoryContext:
                return MemoryContext(calibration="calibration block", team_memory="team memory block")

        with (
            patch("memory.get_shared_vector_memory", new=AsyncMock(return_value=object())),
            patch("memory.BaseMemory", new=_FakeBaseMemory),
            patch("analyzer.db.get_numeric_recalibration_profiles", new=AsyncMock(return_value=[])),
            patch("analyzer.db.get_market_family_quality_profiles", new=AsyncMock(return_value=[])),
            patch("analyzer._build_live_numeric_baseline", new=AsyncMock(return_value={
                "prob_w1": 45.0,
                "prob_draw": 28.0,
                "prob_w2": 27.0,
                "prob_over_2_5": 54.0,
                "prob_under_2_5": 46.0,
                "prob_btts_yes": 57.0,
                "prob_btts_no": 43.0,
                "expected_goals": 2.7,
            })),
            patch("analyzer._format_numeric_baseline_block", return_value="numeric baseline block"),
            patch("analyzer._elo_block", new=AsyncMock(return_value="elo block")),
            patch("analyzer._build_data_context", return_value="local data context"),
            patch("agents.chain.run_agent_chain", new=AsyncMock(return_value=local_result)) as chain_mock,
            patch("analyzer._postprocess_report_dict", new=AsyncMock(return_value=postprocessed)) as postprocess_mock,
        ):
            report = await analyzer._agent_chain_analyze(data, warnings=["lineups unavailable"])

        self.assertEqual(report.analysis_summary, "Postprocessed local-chain report.")
        chain_mock.assert_awaited_once()
        chain_kwargs = chain_mock.await_args.kwargs
        self.assertEqual(chain_kwargs["team1"], "Arsenal")
        self.assertEqual(chain_kwargs["team2"], "Chelsea")
        self.assertEqual(chain_kwargs["data_context"], "local data context")
        self.assertIs(chain_kwargs["clients"].anthropic, analyzer._anthropic_client)
        self.assertIs(chain_kwargs["clients"].openai, analyzer._openai_client)
        self.assertEqual(chain_kwargs["memory_context"].calibration, "calibration block")
        self.assertEqual(chain_kwargs["raw_completeness_pct"], 100)
        self.assertIn("lineups unavailable", chain_kwargs["warnings_block"])
        postprocess_mock.assert_awaited_once()
        postprocess_args = postprocess_mock.await_args.args
        postprocess_kwargs = postprocess_mock.await_args.kwargs
        self.assertIs(postprocess_args[0], local_result)
        self.assertIs(postprocess_args[1], data)
        self.assertEqual(postprocess_args[2], ["lineups unavailable"])
        self.assertEqual(postprocess_kwargs["prompt_version"], analyzer.AGENT_CHAIN_PROMPT_VERSION)
        metrics = analyzer.get_analysis_runtime_metrics_snapshot()
        self.assertEqual(metrics["agent_chain_runs"], 1)


class ValueBetComputationTest(unittest.IsolatedAsyncioTestCase):
    async def test_value_bets_computed_when_edge_exceeds_5pct(self):
        """When Claude says 60% but bookie implies 42%, that's +18% edge -> value bet."""
        mock_odds = OddsData(
            implied_prob_w1=0.42,
            implied_prob_draw=0.28,
            implied_prob_w2=0.30,
            scrape_success=True,
        )
        match_data = MatchData(team1="Arsenal", team2="Chelsea", odds=mock_odds)

        mock_report = {
            "prob_w1": 60.0, "prob_draw": 22.0, "prob_w2": 18.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 55.0, "prob_under_2_5": 45.0, "expected_goals": 2.5,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.5", "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 50.0, "prob_btts_no": 50.0, "btts_confidence": "MEDIUM",
            "top_recommendation": "Arsenal Win", "top_confidence_pct": 65.0,
            "risk_level": "MEDIUM", "analysis_summary": "Arsenal strong.",
            "team1_strengths": [], "team1_weaknesses": [],
            "team2_strengths": [], "team2_weaknesses": [],
        }

        with patch("analyzer._generate_report_dict", new=AsyncMock(return_value=mock_report)):
            with patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", False):
                with patch.object(analyzer.config, "RECOMMENDATION_MODE", "balanced"):
                    with patch.object(analyzer.config, "ENSEMBLE_ENABLED", False):
                        with patch("analyzer._calibration_block", new=AsyncMock(return_value="")):
                            with patch("analyzer._team_memory_block", new=AsyncMock(return_value="")):
                                with patch("analyzer._save_team_memory", new=AsyncMock()):
                                    with patch("analyzer._enrich_snapshot_with_elo", new=AsyncMock()):
                                        report = await analyzer.analyze_match(match_data)

        # 55% vs 42% = +13% edge -> should be flagged
        self.assertTrue(len(report.value_bets) > 0)
        self.assertTrue(len(report.value_candidate_snapshot) > 0)
        self.assertTrue(any("Arsenal" in vb for vb in report.value_bets))
        self.assertTrue(any("edge" in vb.lower() for vb in report.value_bets))
        if report.top_recommendation.startswith("No bet"):
            self.assertEqual(report.top_market_code, "NO_BET")
            self.assertEqual(report.top_selection_code, "NO_BET")
        else:
            self.assertEqual(report.top_market_code, "1X2")
            self.assertEqual(report.top_selection_code, "TEAM1")

    async def test_no_value_bets_when_edge_below_5pct(self):
        """When difference is < 5%, no value bet."""
        mock_odds = OddsData(
            implied_prob_w1=0.50,
            implied_prob_draw=0.28,
            implied_prob_w2=0.22,
            scrape_success=True,
        )
        match_data = MatchData(team1="Arsenal", team2="Chelsea", odds=mock_odds)

        mock_report = {
            "prob_w1": 53.0, "prob_draw": 25.0, "prob_w2": 22.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 55.0, "prob_under_2_5": 45.0, "expected_goals": 2.5,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Level", "asian_handicap_confidence": "LOW",
            "prob_btts_yes": 50.0, "prob_btts_no": 50.0, "btts_confidence": "LOW",
            "top_recommendation": "Draw", "top_confidence_pct": 50.0,
            "risk_level": "HIGH", "analysis_summary": "Tight match.",
            "team1_strengths": [], "team1_weaknesses": [],
            "team2_strengths": [], "team2_weaknesses": [],
        }

        with patch("analyzer._generate_report_dict", new=AsyncMock(return_value=mock_report)):
            with patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", False):
                with patch.object(analyzer.config, "RECOMMENDATION_MODE", "balanced"):
                    with patch.object(analyzer.config, "ENSEMBLE_ENABLED", False):
                        with patch("analyzer._calibration_block", new=AsyncMock(return_value="")):
                            with patch("analyzer._team_memory_block", new=AsyncMock(return_value="")):
                                with patch("analyzer._save_team_memory", new=AsyncMock()):
                                    with patch("analyzer._enrich_snapshot_with_elo", new=AsyncMock()):
                                        report = await analyzer.analyze_match(match_data)

        self.assertEqual(report.value_bets, [])
        if report.top_recommendation.startswith("No bet"):
            self.assertEqual(report.top_market_code, "NO_BET")
            self.assertEqual(report.top_selection_code, "NO_BET")
        else:
            self.assertEqual(report.top_market_code, "1X2")
            self.assertEqual(report.top_selection_code, "DRAW")


class AnalyzerContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._bankroll_guardrails_patcher = patch(
            "analyzer.db.get_bankroll_guardrails",
            new=AsyncMock(return_value=None),
        )
        self._bankroll_guardrails_patcher.start()

    def tearDown(self) -> None:
        self._bankroll_guardrails_patcher.stop()

    def test_build_data_context_includes_match_context(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            match_context=MatchContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                competition_name="Champions League",
                kickoff_utc="2026-03-10T20:00:00Z",
                venue_name="Emirates Stadium",
                is_neutral_venue=False,
                is_team1_home=True,
                stage="Semi-final second leg",
                is_knockout=True,
                is_second_leg=True,
                scrape_success=True,
            ),
        )

        context = analyzer._build_data_context(data)

        self.assertIn("Upcoming fixture context:", context)
        self.assertIn("Competition: Champions League", context)
        self.assertIn("Venue: Emirates Stadium", context)
        self.assertIn("Tie state: second leg", context)

    def test_build_data_context_includes_advanced_stats(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            advanced_stats=AdvancedStatsData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_metrics=TeamAdvancedMetrics(
                    xg=50.0,
                    xga=21.1,
                    xg_diff=28.9,
                    possession_pct=56.4,
                    shots_on_target_per_match=4.9,
                    corners=173,
                    clean_sheets=14,
                ),
                team2_metrics=TeamAdvancedMetrics(
                    xg=41.2,
                    xga=32.4,
                    xg_diff=8.8,
                    possession_pct=52.1,
                    shots_on_target_per_match=4.2,
                    corners=149,
                    clean_sheets=9,
                ),
                scrape_success=True,
            ),
        )

        context = analyzer._build_data_context(data)

        self.assertIn("SECTION 6: ADVANCED TEAM STATS", context)
        self.assertIn("Season xG/xGA: 50.0 / 21.1 (xG diff +28.9)", context)
        self.assertIn("Possession: 56.4%", context)
        self.assertIn("Supporting volume: corners 173, clean sheets 14", context)

    def test_build_data_context_includes_market_signals(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            market_signals=MarketSignalsData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                one_x_two={
                    "team1": MarketSelectionSignal(
                        avg_odds=1.35,
                        min_odds=1.29,
                        max_odds=1.41,
                        implied_prob=0.69,
                        bookmaker_count=18,
                        movement_score=6,
                    ),
                    "draw": MarketSelectionSignal(
                        avg_odds=5.2,
                        min_odds=4.8,
                        max_odds=5.6,
                        implied_prob=0.18,
                        bookmaker_count=18,
                        movement_score=-2,
                    ),
                    "team2": MarketSelectionSignal(
                        avg_odds=9.1,
                        min_odds=8.3,
                        max_odds=10.0,
                        implied_prob=0.13,
                        bookmaker_count=18,
                        movement_score=-4,
                    ),
                },
                over_under_2_5={
                    "over": MarketSelectionSignal(
                        avg_odds=1.83,
                        min_odds=1.75,
                        max_odds=1.89,
                        implied_prob=0.53,
                        bookmaker_count=17,
                        movement_score=-1,
                    ),
                    "under": MarketSelectionSignal(
                        avg_odds=2.01,
                        min_odds=1.95,
                        max_odds=2.08,
                        implied_prob=0.47,
                        bookmaker_count=17,
                        movement_score=2,
                    ),
                },
                asian_handicap_line=-0.5,
                asian_handicap={
                    "team1": MarketSelectionSignal(
                        avg_odds=1.34,
                        min_odds=1.30,
                        max_odds=1.40,
                        implied_prob=0.68,
                        bookmaker_count=12,
                        movement_score=5,
                    ),
                    "team2": MarketSelectionSignal(
                        avg_odds=3.20,
                        min_odds=3.00,
                        max_odds=3.45,
                        implied_prob=0.32,
                        bookmaker_count=12,
                        movement_score=-5,
                    ),
                },
                favourite_label="team1",
                favourite_gap_pct=0.51,
                market_warnings=["Favourite price dispersion is high across bookmakers"],
                scrape_success=True,
            ),
        )

        context = analyzer._build_data_context(data)

        self.assertIn("SECTION 5: MARKET SIGNALS", context)
        self.assertIn("1X2 consensus:", context)
        self.assertIn("Favourite by consensus: Arsenal", context)
        self.assertIn("AH consensus on Arsenal -0.5", context)
        self.assertIn("Market warnings:", context)

    def test_build_data_context_includes_standings_context(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            standings_context=StandingsContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                league_name="Premier League",
                team1_overall=TableEntrySummary(
                    position=1,
                    points=67,
                    played=30,
                    goal_difference=37,
                    scores_str="59-22",
                    qualification_label="Champions League",
                ),
                team2_overall=TableEntrySummary(
                    position=5,
                    points=48,
                    played=29,
                    goal_difference=19,
                    scores_str="53-34",
                    qualification_label="Europa League",
                ),
                team1_home=TableEntrySummary(position=2, points=35),
                team2_away=TableEntrySummary(position=6, points=24),
                point_gap=19,
                position_gap=4,
                team1_above_team2=True,
                scrape_success=True,
            ),
        )

        context = analyzer._build_data_context(data)

        self.assertIn("SECTION 7: STANDINGS CONTEXT", context)
        self.assertIn("League table: Premier League", context)
        self.assertIn("Arsenal: #1, 67 pts, 30 played, GD +37, goals 59-22, Champions League", context)
        self.assertIn("Head-to-head in table: point gap +19, position gap +4", context)
        self.assertIn("Venue split: Arsenal home rank #2 (35 pts) | Chelsea away rank #6 (24 pts)", context)

    def test_build_data_context_includes_schedule_load(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            schedule_load=ScheduleLoadData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1=TeamScheduleLoadSummary(
                    rest_hours=96.0,
                    matches_last_7d=1,
                    matches_next_7d=1,
                    last_opponent="Everton",
                    last_match_utc="2026-03-06T20:00:00+00:00",
                    next_opponent="Tottenham",
                    next_match_utc="2026-03-14T12:30:00+00:00",
                    short_rest=False,
                    next_short_turnaround=True,
                ),
                team2=TeamScheduleLoadSummary(
                    rest_hours=48.0,
                    matches_last_7d=3,
                    matches_next_7d=2,
                    last_opponent="Liverpool",
                    last_match_utc="2026-03-08T20:00:00+00:00",
                    next_opponent="Newcastle",
                    next_match_utc="2026-03-12T19:00:00+00:00",
                    short_rest=True,
                    next_short_turnaround=True,
                ),
                rest_gap_hours=48.0,
                load_warnings=[
                    "Chelsea on short rest (48h since previous match)",
                    "Arsenal has about 48h more rest than Chelsea",
                ],
                scrape_success=True,
            ),
        )

        context = analyzer._build_data_context(data)

        self.assertIn("SECTION 8: SCHEDULE / LOAD SIGNALS", context)
        self.assertIn("Rest since previous match: 96.0h", context)
        self.assertIn("Matches in last 7d: 3", context)
        self.assertIn("Rest gap (Arsenal minus Chelsea): +48.0h", context)
        self.assertIn("Load warnings:", context)

    def test_build_data_context_includes_weather_and_motivation_filters(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            match_context=MatchContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                competition_name="Champions League",
                kickoff_utc="2026-03-10T20:00:00Z",
                venue_name="Emirates Stadium",
                is_neutral_venue=False,
                is_team1_home=True,
                stage="Semi-final second leg",
                is_knockout=True,
                is_second_leg=True,
                scrape_success=True,
            ),
            weather_context=WeatherContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                venue_name="Emirates Stadium",
                city_name="London",
                country_name="England",
                kickoff_utc="2026-03-10T20:00:00Z",
                forecast_time_utc="2026-03-10T20:00:00",
                temperature_c=4.0,
                apparent_temperature_c=1.5,
                wind_speed_kmh=32.0,
                wind_gusts_kmh=47.0,
                precipitation_mm=1.8,
                precipitation_probability_pct=72.0,
                weather_label="Rain showers",
                weather_warnings=[
                    "Windy conditions may distort crossing and long-ball accuracy",
                    "Wet pitch risk can slow tempo and increase errors",
                ],
                scrape_success=True,
            ),
            standings_context=StandingsContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                league_name="Premier League",
                team1_overall=TableEntrySummary(position=2, points=62, played=28),
                team2_overall=TableEntrySummary(position=3, points=60, played=28),
                point_gap=2,
                position_gap=1,
                team1_above_team2=True,
                scrape_success=True,
            ),
            schedule_load=ScheduleLoadData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1=TeamScheduleLoadSummary(matches_next_7d=1, short_rest=False),
                team2=TeamScheduleLoadSummary(matches_next_7d=3, short_rest=True),
                scrape_success=True,
            ),
            sofascore=SofascoreData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_key_absences=[],
                team2_key_absences=[
                    Player(name="Reece James", position="D", status=PlayerStatus.INJURED),
                    Player(name="Wesley Fofana", position="D", status=PlayerStatus.INJURED),
                    Player(name="Romeo Lavia", position="M", status=PlayerStatus.SUSPENDED),
                ],
                team1_predicted_lineup=[f"Arsenal Player {idx}" for idx in range(1, 12)],
                team2_predicted_lineup=[f"Chelsea Player {idx}" for idx in range(1, 10)],
                scrape_success=True,
            ),
        )

        context = analyzer._build_data_context(data)

        self.assertIn("SECTION 9: WEATHER / MOTIVATION / SQUAD FILTERS", context)
        self.assertIn("Venue weather: Emirates Stadium | London | England", context)
        self.assertIn("Weather snapshot:", context)
        self.assertIn("Weather warnings:", context)
        self.assertIn("Motivation filters:", context)
        self.assertIn("Squad filters:", context)
        self.assertIn("Knockout second leg can distort urgency and late-game variance", context)
        self.assertIn("Direct table-rival matchup raises six-pointer intensity", context)
        self.assertIn("Chelsea may rotate because of schedule congestion", context)
        self.assertIn("Chelsea are missing 3 confirmed key players", context)
        self.assertIn("Chelsea predicted lineup is incomplete", context)

    async def test_single_call_prompt_uses_dynamic_source_total(self):
        captured: dict[str, str] = {}
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            source_statuses=[
                SourceStatus(
                    key="fotmob_form",
                    label="Form/H2H",
                    field_name="flashscore",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="fotmob_squad",
                    label="Squads",
                    field_name="sofascore",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="transfermarkt",
                    label="Values",
                    field_name="transfermarkt",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="betexplorer",
                    label="Odds",
                    field_name="odds",
                    scrape_success=True,
                ),
                SourceStatus(
                    key="match_context",
                    label="Match context",
                    field_name="match_context",
                    scrape_success=False,
                ),
            ],
        )

        async def fake_generate(user_prompt: str) -> dict:
            captured["prompt"] = user_prompt
            return json.loads(_report_payload())

        with (
            patch.object(analyzer.config, "AGENT_CHAIN_ENABLED", False),
            patch("analyzer._generate_report_dict", new=fake_generate),
            patch("analyzer._calibration_block", new=AsyncMock(return_value="")),
            patch("analyzer._team_memory_block", new=AsyncMock(return_value="")),
            patch("analyzer._save_team_memory", new=AsyncMock()),
        ):
            report = await analyzer.analyze_match(data)

        self.assertEqual(report.team1, "Arsenal")
        self.assertIn(
            "RAW DATA QUALITY: 80% (4/5 total registered sources scraped successfully)",
            captured["prompt"],
        )
        self.assertIn(
            "APPLICABLE SOURCE QUALITY: 80% (4/5 provider-covered sources scraped successfully)",
            captured["prompt"],
        )


class AnalyzerNumericBaselineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._bankroll_guardrails_patcher = patch(
            "analyzer.db.get_bankroll_guardrails",
            new=AsyncMock(return_value=None),
        )
        self._bankroll_guardrails_patcher.start()

    def tearDown(self) -> None:
        self._bankroll_guardrails_patcher.stop()

    async def test_postprocess_attaches_feature_snapshot_and_blends_probabilities(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            advanced_stats=AdvancedStatsData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_metrics=TeamAdvancedMetrics(xg=55.0, xga=31.0, rating=7.2, possession_pct=58.0),
                team2_metrics=TeamAdvancedMetrics(xg=42.0, xga=39.0, rating=6.9, possession_pct=51.0),
                scrape_success=True,
            ),
            match_context=MatchContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                is_neutral_venue=False,
                is_team1_home=True,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="a", label="A", field_name="odds", scrape_success=True),
                SourceStatus(key="b", label="B", field_name="advanced", scrape_success=True),
                SourceStatus(key="c", label="C", field_name="context", scrape_success=True),
                SourceStatus(key="d", label="D", field_name="form", scrape_success=True),
            ],
        )
        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            report = await analyzer._postprocess_report_dict(
                {
                    "prob_w1": 68.0,
                    "prob_draw": 18.0,
                    "prob_w2": 14.0,
                    "result_confidence": "HIGH",
                    "prob_over_2_5": 61.0,
                    "prob_under_2_5": 39.0,
                    "expected_goals": 3.0,
                    "goals_confidence": "MEDIUM",
                    "asian_handicap_rec": "Arsenal -0.5",
                    "asian_handicap_confidence": "MEDIUM",
                    "prob_btts_yes": 56.0,
                    "prob_btts_no": 44.0,
                    "btts_confidence": "MEDIUM",
                    "team1_strengths": [],
                    "team1_weaknesses": [],
                    "team2_strengths": [],
                    "team2_weaknesses": [],
                    "top_recommendation": "Arsenal Win",
                    "top_recommendation_odds": 1.95,
                    "top_confidence_pct": 66.0,
                    "risk_level": "MEDIUM",
                    "analysis_summary": "Arsenal project stronger underlying numbers.",
                    "data_warnings": [],
                },
                data,
                [],
                prompt_version="test-prompt",
            )

        self.assertIsNotNone(report.feature_snapshot)
        self.assertGreater(report.feature_snapshot.blend_alpha, 0.0)
        self.assertIsNotNone(report.feature_snapshot.model_prob_w1)
        self.assertNotEqual(report.prob_w1, 68.0)
        self.assertEqual(report.methodology_version, analyzer.ANALYSIS_METHODOLOGY_VERSION)

    async def test_postprocess_quality_adjustment_cools_totals_in_severe_weather(self):
        base_data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
                SourceStatus(key="advanced", label="Advanced", field_name="advanced_stats", scrape_success=True),
            ],
        )
        weather_data = base_data.model_copy(
            update={
                "weather_context": WeatherContextData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    temperature_c=0.0,
                    wind_speed_kmh=36.0,
                    wind_gusts_kmh=52.0,
                    precipitation_mm=2.0,
                    precipitation_probability_pct=82.0,
                    weather_code=95,
                    weather_label="Thunderstorm",
                    scrape_success=True,
                )
            }
        )
        input_report = {
            "prob_w1": 55.0,
            "prob_draw": 24.0,
            "prob_w2": 21.0,
            "result_confidence": "HIGH",
            "prob_over_2_5": 60.0,
            "prob_under_2_5": 40.0,
            "expected_goals": 2.95,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.5",
            "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 56.0,
            "prob_btts_no": 44.0,
            "btts_confidence": "MEDIUM",
            "team1_strengths": [],
            "team1_weaknesses": [],
            "team2_strengths": [],
            "team2_weaknesses": [],
            "top_recommendation": "Arsenal Win",
            "top_recommendation_odds": 1.95,
            "top_confidence_pct": 66.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Arsenal project stronger underlying numbers.",
            "data_warnings": [],
        }

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            base_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                base_data,
                [],
                prompt_version="test-prompt",
            )
            weather_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                weather_data,
                [],
                prompt_version="test-prompt",
            )

        self.assertLess(weather_report.expected_goals, base_report.expected_goals)
        self.assertLess(weather_report.prob_over_2_5, base_report.prob_over_2_5)
        self.assertLess(weather_report.prob_btts_yes, base_report.prob_btts_yes)
        self.assertIn("Quality adjustment applied:", weather_report.analysis_summary)

    async def test_postprocess_quality_adjustment_applies_league_style_priors(self):
        base_data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            match_context=MatchContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                competition_name="Unknown League",
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="context", label="Context", field_name="match_context", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
            ],
        )
        bundesliga_data = base_data.model_copy(
            update={
                "match_context": MatchContextData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    competition_name="Bundesliga",
                    scrape_success=True,
                )
            }
        )
        ligue_one_data = base_data.model_copy(
            update={
                "match_context": MatchContextData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    competition_name="Ligue 1",
                    scrape_success=True,
                )
            }
        )
        input_report = {
            "prob_w1": 51.0,
            "prob_draw": 27.0,
            "prob_w2": 22.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 54.0,
            "prob_under_2_5": 46.0,
            "expected_goals": 2.7,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.25",
            "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 53.0,
            "prob_btts_no": 47.0,
            "btts_confidence": "MEDIUM",
            "team1_strengths": [],
            "team1_weaknesses": [],
            "team2_strengths": [],
            "team2_weaknesses": [],
            "top_recommendation": "Arsenal Win",
            "top_recommendation_odds": 1.95,
            "top_confidence_pct": 62.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Arsenal project stronger underlying numbers.",
            "data_warnings": [],
        }

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            base_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                base_data,
                [],
                prompt_version="test-prompt",
            )
            bundesliga_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                bundesliga_data,
                [],
                prompt_version="test-prompt",
            )
            ligue_one_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                ligue_one_data,
                [],
                prompt_version="test-prompt",
            )

        self.assertGreater(bundesliga_report.expected_goals, base_report.expected_goals)
        self.assertGreater(bundesliga_report.prob_over_2_5, base_report.prob_over_2_5)
        self.assertGreater(bundesliga_report.prob_btts_yes, base_report.prob_btts_yes)
        self.assertLess(ligue_one_report.expected_goals, base_report.expected_goals)
        self.assertLess(ligue_one_report.prob_over_2_5, base_report.prob_over_2_5)
        self.assertLess(ligue_one_report.prob_btts_yes, base_report.prob_btts_yes)
        self.assertGreater(bundesliga_report.expected_goals, ligue_one_report.expected_goals)
        self.assertGreater(bundesliga_report.prob_btts_yes, ligue_one_report.prob_btts_yes)
        self.assertIn("Quality adjustment applied:", bundesliga_report.analysis_summary)
        self.assertIn("Quality adjustment applied:", ligue_one_report.analysis_summary)

    async def test_postprocess_quality_adjustment_shifts_1x2_for_asymmetric_absences(self):
        base_data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            sofascore=SofascoreData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_key_absences=[],
                team2_key_absences=[],
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="squad", label="Squad", field_name="sofascore", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
            ],
        )
        absence_data = base_data.model_copy(
            update={
                "sofascore": SofascoreData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    team1_key_absences=[],
                    team2_key_absences=[
                        Player(name="Reece James", position="D", status=PlayerStatus.INJURED),
                        Player(name="Wesley Fofana", position="D", status=PlayerStatus.INJURED),
                        Player(name="Levi Colwill", position="D", status=PlayerStatus.DOUBTFUL),
                    ],
                    team1_predicted_lineup=[f"Arsenal Player {idx}" for idx in range(1, 12)],
                    team2_predicted_lineup=[f"Chelsea Player {idx}" for idx in range(1, 12)],
                    scrape_success=True,
                )
            }
        )
        input_report = {
            "prob_w1": 51.0,
            "prob_draw": 27.0,
            "prob_w2": 22.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 54.0,
            "prob_under_2_5": 46.0,
            "expected_goals": 2.7,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.25",
            "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 53.0,
            "prob_btts_no": 47.0,
            "btts_confidence": "MEDIUM",
            "team1_strengths": [],
            "team1_weaknesses": [],
            "team2_strengths": [],
            "team2_weaknesses": [],
            "top_recommendation": "Arsenal Win",
            "top_recommendation_odds": 1.95,
            "top_confidence_pct": 62.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Arsenal project stronger underlying numbers.",
            "data_warnings": [],
        }

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            base_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                base_data,
                [],
                prompt_version="test-prompt",
            )
            absence_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                absence_data,
                [],
                prompt_version="test-prompt",
            )

        self.assertGreater(absence_report.prob_w1, base_report.prob_w1)
        self.assertLess(absence_report.prob_w2, base_report.prob_w2)
        self.assertGreater(absence_report.expected_goals, base_report.expected_goals)
        self.assertIn("Quality adjustment applied:", absence_report.analysis_summary)

    async def test_postprocess_quality_adjustment_distinguishes_goalkeeper_and_striker_absences(self):
        base_data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            sofascore=SofascoreData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_key_absences=[],
                team2_key_absences=[],
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="squad", label="Squad", field_name="sofascore", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
            ],
        )
        goalkeeper_absence_data = base_data.model_copy(
            update={
                "sofascore": SofascoreData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    team1_key_absences=[],
                    team2_key_absences=[
                        Player(name="Robert Sanchez", position="GK", status=PlayerStatus.INJURED),
                    ],
                    team1_predicted_lineup=[f"Arsenal Player {idx}" for idx in range(1, 12)],
                    team2_predicted_lineup=[f"Chelsea Player {idx}" for idx in range(1, 12)],
                    scrape_success=True,
                )
            }
        )
        striker_absence_data = base_data.model_copy(
            update={
                "sofascore": SofascoreData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    team1_key_absences=[],
                    team2_key_absences=[
                        Player(name="Nicolas Jackson", position="ST", status=PlayerStatus.INJURED),
                    ],
                    team1_predicted_lineup=[f"Arsenal Player {idx}" for idx in range(1, 12)],
                    team2_predicted_lineup=[f"Chelsea Player {idx}" for idx in range(1, 12)],
                    scrape_success=True,
                )
            }
        )
        input_report = {
            "prob_w1": 51.0,
            "prob_draw": 27.0,
            "prob_w2": 22.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 54.0,
            "prob_under_2_5": 46.0,
            "expected_goals": 2.7,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.25",
            "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 53.0,
            "prob_btts_no": 47.0,
            "btts_confidence": "MEDIUM",
            "team1_strengths": [],
            "team1_weaknesses": [],
            "team2_strengths": [],
            "team2_weaknesses": [],
            "top_recommendation": "Arsenal Win",
            "top_recommendation_odds": 1.95,
            "top_confidence_pct": 62.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Arsenal project stronger underlying numbers.",
            "data_warnings": [],
        }

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            base_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                base_data,
                [],
                prompt_version="test-prompt",
            )
            goalkeeper_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                goalkeeper_absence_data,
                [],
                prompt_version="test-prompt",
            )
            striker_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                striker_absence_data,
                [],
                prompt_version="test-prompt",
            )

        self.assertGreater(goalkeeper_report.prob_w1, base_report.prob_w1)
        self.assertGreater(goalkeeper_report.expected_goals, base_report.expected_goals)
        self.assertGreater(goalkeeper_report.prob_over_2_5, base_report.prob_over_2_5)
        self.assertGreater(goalkeeper_report.prob_btts_yes, base_report.prob_btts_yes)
        self.assertGreater(striker_report.prob_w1, base_report.prob_w1)
        self.assertLess(striker_report.expected_goals, base_report.expected_goals)
        self.assertLess(striker_report.prob_over_2_5, base_report.prob_over_2_5)
        self.assertLess(striker_report.prob_btts_yes, base_report.prob_btts_yes)
        self.assertGreater(goalkeeper_report.expected_goals, striker_report.expected_goals)
        self.assertGreater(goalkeeper_report.prob_over_2_5, striker_report.prob_over_2_5)
        self.assertIn("Quality adjustment applied:", goalkeeper_report.analysis_summary)
        self.assertIn("Quality adjustment applied:", striker_report.analysis_summary)

    async def test_postprocess_quality_adjustment_uses_rest_gap_and_short_rest(self):
        base_data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="schedule", label="Schedule", field_name="schedule_load", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
            ],
        )
        schedule_data = base_data.model_copy(
            update={
                "schedule_load": ScheduleLoadData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    team1=TeamScheduleLoadSummary(
                        rest_hours=120.0,
                        matches_last_7d=1,
                        matches_next_7d=1,
                        short_rest=False,
                    ),
                    team2=TeamScheduleLoadSummary(
                        rest_hours=48.0,
                        matches_last_7d=3,
                        matches_next_7d=2,
                        short_rest=True,
                    ),
                    rest_gap_hours=72.0,
                    scrape_success=True,
                )
            }
        )
        input_report = {
            "prob_w1": 51.0,
            "prob_draw": 27.0,
            "prob_w2": 22.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 54.0,
            "prob_under_2_5": 46.0,
            "expected_goals": 2.7,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.25",
            "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 53.0,
            "prob_btts_no": 47.0,
            "btts_confidence": "MEDIUM",
            "team1_strengths": [],
            "team1_weaknesses": [],
            "team2_strengths": [],
            "team2_weaknesses": [],
            "top_recommendation": "Arsenal Win",
            "top_recommendation_odds": 1.95,
            "top_confidence_pct": 62.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Arsenal project stronger underlying numbers.",
            "data_warnings": [],
        }

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            base_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                base_data,
                [],
                prompt_version="test-prompt",
            )
            schedule_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                schedule_data,
                [],
                prompt_version="test-prompt",
            )

        self.assertGreater(schedule_report.prob_w1, base_report.prob_w1)
        self.assertLess(schedule_report.prob_w2, base_report.prob_w2)
        self.assertLess(schedule_report.expected_goals, base_report.expected_goals)
        self.assertLess(schedule_report.prob_over_2_5, base_report.prob_over_2_5)
        self.assertIn("Quality adjustment applied:", schedule_report.analysis_summary)

    async def test_postprocess_quality_adjustment_adds_draw_bias_for_tense_context(self):
        base_data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.80,
                btts_no_odds=2.00,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="context", label="Context", field_name="match_context", scrape_success=True),
                SourceStatus(key="standings", label="Standings", field_name="standings_context", scrape_success=True),
            ],
        )
        tense_data = base_data.model_copy(
            update={
                "match_context": MatchContextData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    stage="Cup Final",
                    is_knockout=True,
                    is_second_leg=False,
                    scrape_success=True,
                ),
                "standings_context": StandingsContextData(
                    team1_name="Arsenal",
                    team2_name="Chelsea",
                    team1_overall=TableEntrySummary(position=2, points=62, played=28),
                    team2_overall=TableEntrySummary(position=3, points=60, played=28),
                    point_gap=2,
                    position_gap=1,
                    team1_above_team2=True,
                    scrape_success=True,
                ),
            }
        )
        input_report = {
            "prob_w1": 51.0,
            "prob_draw": 27.0,
            "prob_w2": 22.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 54.0,
            "prob_under_2_5": 46.0,
            "expected_goals": 2.7,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.25",
            "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 53.0,
            "prob_btts_no": 47.0,
            "btts_confidence": "MEDIUM",
            "team1_strengths": [],
            "team1_weaknesses": [],
            "team2_strengths": [],
            "team2_weaknesses": [],
            "top_recommendation": "Arsenal Win",
            "top_recommendation_odds": 1.95,
            "top_confidence_pct": 62.0,
            "risk_level": "MEDIUM",
            "analysis_summary": "Arsenal project stronger underlying numbers.",
            "data_warnings": [],
        }

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            base_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                base_data,
                [],
                prompt_version="test-prompt",
            )
            tense_report = await analyzer._postprocess_report_dict(
                dict(input_report),
                tense_data,
                [],
                prompt_version="test-prompt",
            )

        self.assertGreater(tense_report.prob_draw, base_report.prob_draw)
        self.assertLess(tense_report.expected_goals, base_report.expected_goals)
        self.assertLess(tense_report.prob_over_2_5, base_report.prob_over_2_5)
        self.assertIn("Quality adjustment applied:", tense_report.analysis_summary)

    async def test_postprocess_triggers_no_bet_filter_when_signals_are_weak(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.50,
                implied_prob_draw=0.27,
                implied_prob_w2=0.23,
                over_2_5_odds=1.91,
                under_2_5_odds=1.91,
                btts_yes_odds=1.95,
                btts_no_odds=1.95,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=False),
                SourceStatus(key="squad", label="Squad", field_name="sofascore", scrape_success=False),
                SourceStatus(key="values", label="Values", field_name="transfermarkt", scrape_success=False),
            ],
        )
        with patch.object(analyzer.config, "RECOMMENDATION_MODE", "balanced"):
            with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
                report = await analyzer._postprocess_report_dict(
                    {
                        "prob_w1": 50.5,
                        "prob_draw": 27.0,
                        "prob_w2": 22.5,
                        "result_confidence": "LOW",
                        "prob_over_2_5": 51.0,
                        "prob_under_2_5": 49.0,
                        "expected_goals": 2.55,
                        "goals_confidence": "LOW",
                        "asian_handicap_rec": "Level (0)",
                        "asian_handicap_confidence": "LOW",
                        "prob_btts_yes": 50.0,
                        "prob_btts_no": 50.0,
                        "btts_confidence": "LOW",
                        "team1_strengths": [],
                        "team1_weaknesses": [],
                        "team2_strengths": [],
                        "team2_weaknesses": [],
                        "top_recommendation": "Over 2.5",
                        "top_recommendation_odds": 1.91,
                        "top_confidence_pct": 54.0,
                        "risk_level": "MEDIUM",
                        "analysis_summary": "Signals are thin and mostly market-shaped.",
                        "data_warnings": [],
                    },
                    data,
                    ["source 1 missing", "source 2 missing", "source 3 missing"],
                    prompt_version="test-prompt",
                )

        self.assertTrue(report.top_recommendation.startswith("No bet -"))
        self.assertIsNone(report.top_recommendation_odds)
        self.assertTrue(any("No-bet filter triggered" in warning for warning in report.data_warnings))
        self.assertIsNotNone(report.feature_snapshot)
        self.assertTrue(report.feature_snapshot.no_bet_triggered)

    async def test_postprocess_surfaces_market_signal_alerts_in_report_warnings(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.50,
                implied_prob_draw=0.27,
                implied_prob_w2=0.23,
                over_2_5_odds=1.91,
                under_2_5_odds=1.91,
                btts_yes_odds=1.95,
                btts_no_odds=1.95,
                scrape_success=True,
            ),
            market_signals=MarketSignalsData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                market_warnings=[
                    "Favourite price dispersion is high across bookmakers",
                    "Asian handicap movement is mixed",
                ],
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="market_signals", label="Market signals", field_name="market_signals", scrape_success=True),
            ],
        )

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            report = await analyzer._postprocess_report_dict(
                {
                    "prob_w1": 54.0,
                    "prob_draw": 25.0,
                    "prob_w2": 21.0,
                    "result_confidence": "MEDIUM",
                    "prob_over_2_5": 55.0,
                    "prob_under_2_5": 45.0,
                    "expected_goals": 2.7,
                    "goals_confidence": "MEDIUM",
                    "asian_handicap_rec": "Arsenal -0.25",
                    "asian_handicap_confidence": "MEDIUM",
                    "prob_btts_yes": 54.0,
                    "prob_btts_no": 46.0,
                    "btts_confidence": "MEDIUM",
                    "team1_strengths": [],
                    "team1_weaknesses": [],
                    "team2_strengths": [],
                    "team2_weaknesses": [],
                    "top_recommendation": "Arsenal draw no bet",
                    "top_recommendation_odds": 1.85,
                    "top_confidence_pct": 58.0,
                    "risk_level": "MEDIUM",
                    "analysis_summary": "Market is playable but noisy.",
                    "data_warnings": [],
                },
                data,
                [],
                prompt_version="test-prompt",
            )

        self.assertIn(
            "Market alert: Favourite price dispersion is high across bookmakers",
            report.data_warnings,
        )
        self.assertIn(
            "Market alert: Asian handicap movement is mixed",
            report.data_warnings,
        )

    async def test_postprocess_surfaces_weather_and_motivation_filters_in_report_warnings(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.50,
                implied_prob_draw=0.27,
                implied_prob_w2=0.23,
                over_2_5_odds=1.91,
                under_2_5_odds=1.91,
                btts_yes_odds=1.95,
                btts_no_odds=1.95,
                scrape_success=True,
            ),
            match_context=MatchContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                stage="Semi-final second leg",
                is_knockout=True,
                is_second_leg=True,
                scrape_success=True,
            ),
            weather_context=WeatherContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                weather_warnings=[
                    "Windy conditions may distort crossing and long-ball accuracy",
                ],
                scrape_success=True,
            ),
            standings_context=StandingsContextData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_overall=TableEntrySummary(position=2, points=62, played=28),
                team2_overall=TableEntrySummary(position=3, points=60, played=28),
                point_gap=2,
                position_gap=1,
                team1_above_team2=True,
                scrape_success=True,
            ),
            schedule_load=ScheduleLoadData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1=TeamScheduleLoadSummary(matches_next_7d=1, short_rest=False),
                team2=TeamScheduleLoadSummary(matches_next_7d=3, short_rest=True),
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="weather_context", label="Weather", field_name="weather_context", scrape_success=True),
                SourceStatus(key="match_context", label="Match context", field_name="match_context", scrape_success=True),
                SourceStatus(key="standings_context", label="Standings", field_name="standings_context", scrape_success=True),
                SourceStatus(key="schedule_load", label="Schedule/load", field_name="schedule_load", scrape_success=True),
            ],
        )

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            report = await analyzer._postprocess_report_dict(
                {
                    "prob_w1": 54.0,
                    "prob_draw": 25.0,
                    "prob_w2": 21.0,
                    "result_confidence": "MEDIUM",
                    "prob_over_2_5": 55.0,
                    "prob_under_2_5": 45.0,
                    "expected_goals": 2.7,
                    "goals_confidence": "MEDIUM",
                    "asian_handicap_rec": "Arsenal -0.25",
                    "asian_handicap_confidence": "MEDIUM",
                    "prob_btts_yes": 54.0,
                    "prob_btts_no": 46.0,
                    "btts_confidence": "MEDIUM",
                    "team1_strengths": [],
                    "team1_weaknesses": [],
                    "team2_strengths": [],
                    "team2_weaknesses": [],
                    "top_recommendation": "Arsenal draw no bet",
                    "top_recommendation_odds": 1.85,
                    "top_confidence_pct": 58.0,
                    "risk_level": "MEDIUM",
                    "analysis_summary": "Context filters point to a volatile match.",
                    "data_warnings": [],
                },
                data,
                [],
                prompt_version="test-prompt",
            )

        self.assertIn(
            "Weather filter: Windy conditions may distort crossing and long-ball accuracy",
            report.data_warnings,
        )
        self.assertIn(
            "Motivation filter: Knockout second leg can distort urgency and late-game variance",
            report.data_warnings,
        )
        self.assertIn(
            "Motivation filter: Direct table-rival matchup raises six-pointer intensity",
            report.data_warnings,
        )

    async def test_postprocess_raises_risk_when_squad_filter_flags_severe_absences(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.50,
                implied_prob_draw=0.27,
                implied_prob_w2=0.23,
                over_2_5_odds=1.91,
                under_2_5_odds=1.91,
                btts_yes_odds=1.95,
                btts_no_odds=1.95,
                scrape_success=True,
            ),
            sofascore=SofascoreData(
                team1_name="Arsenal",
                team2_name="Chelsea",
                team1_key_absences=[],
                team2_key_absences=[
                    Player(name="Reece James", position="D", status=PlayerStatus.INJURED),
                    Player(name="Wesley Fofana", position="D", status=PlayerStatus.INJURED),
                    Player(name="Romeo Lavia", position="M", status=PlayerStatus.SUSPENDED),
                    Player(name="Christopher Nkunku", position="F", status=PlayerStatus.DOUBTFUL),
                ],
                team1_predicted_lineup=[f"Arsenal Player {idx}" for idx in range(1, 12)],
                team2_predicted_lineup=[f"Chelsea Player {idx}" for idx in range(1, 10)],
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="fotmob_squad", label="Squads", field_name="sofascore", scrape_success=True),
            ],
        )

        with patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False):
            report = await analyzer._postprocess_report_dict(
                {
                    "prob_w1": 57.0,
                    "prob_draw": 24.0,
                    "prob_w2": 19.0,
                    "result_confidence": "HIGH",
                    "prob_over_2_5": 55.0,
                    "prob_under_2_5": 45.0,
                    "expected_goals": 2.7,
                    "goals_confidence": "MEDIUM",
                    "asian_handicap_rec": "Arsenal -0.5",
                    "asian_handicap_confidence": "MEDIUM",
                    "prob_btts_yes": 54.0,
                    "prob_btts_no": 46.0,
                    "btts_confidence": "MEDIUM",
                    "team1_strengths": [],
                    "team1_weaknesses": [],
                    "team2_strengths": [],
                    "team2_weaknesses": [],
                    "top_recommendation": "Arsenal win",
                    "top_recommendation_odds": 1.85,
                    "top_confidence_pct": 68.0,
                    "risk_level": "LOW",
                    "analysis_summary": "Arsenal are favoured on core numbers.",
                    "data_warnings": [],
                },
                data,
                [],
                prompt_version="test-prompt",
            )

        self.assertEqual(report.risk_level, "HIGH")
        self.assertLessEqual(report.top_confidence_pct, 60.0)
        self.assertIn(
            "Squad risk gate: confirmed absences materially raise lineup uncertainty",
            report.data_warnings,
        )
        self.assertIn(
            "Squad filter: Chelsea are missing 3 confirmed key players",
            report.data_warnings,
        )
        self.assertIn(
            "Squad filter: Squad availability edge favours Arsenal",
            report.data_warnings,
        )

    async def test_postprocess_blocks_pick_when_daily_bankroll_stop_loss_is_hit(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.84,
                btts_no_odds=1.96,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
                SourceStatus(key="advanced", label="Advanced", field_name="advanced_stats", scrape_success=True),
            ],
        )

        with (
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False),
            patch.object(analyzer.config, "BANKROLL_DAILY_LIMIT_ENABLED", True),
            patch.object(analyzer.config, "BANKROLL_DAILY_STOP_LOSS_UNITS", 1.5),
            patch(
                "analyzer.db.get_bankroll_guardrails",
                new=AsyncMock(
                    return_value={
                        "recommended_bets_today": 1,
                        "staked_today_units": 0.75,
                        "settled_bets_today": 2,
                        "settled_profit_today_units": -1.6,
                        "recent_loss_streak": 2,
                    }
                ),
            ),
        ):
            report = await analyzer._postprocess_report_dict(
                {
                    "prob_w1": 58.0,
                    "prob_draw": 23.0,
                    "prob_w2": 19.0,
                    "result_confidence": "HIGH",
                    "prob_over_2_5": 55.0,
                    "prob_under_2_5": 45.0,
                    "expected_goals": 2.8,
                    "goals_confidence": "MEDIUM",
                    "asian_handicap_rec": "Arsenal -0.5",
                    "asian_handicap_confidence": "MEDIUM",
                    "prob_btts_yes": 54.0,
                    "prob_btts_no": 46.0,
                    "btts_confidence": "MEDIUM",
                    "team1_strengths": [],
                    "team1_weaknesses": [],
                    "team2_strengths": [],
                    "team2_weaknesses": [],
                    "top_recommendation": "Arsenal win",
                    "top_recommendation_odds": 2.05,
                    "top_confidence_pct": 71.0,
                    "risk_level": "LOW",
                    "analysis_summary": "Arsenal hold the stronger profile.",
                    "data_warnings": [],
                },
                data,
                [],
                prompt_version="test-prompt",
            )

        self.assertEqual(report.top_recommendation, "No bet - daily bankroll stop-loss reached")
        self.assertEqual(report.top_market_code, "NO_BET")
        self.assertEqual(report.top_selection_code, "NO_BET")
        self.assertEqual(report.stake_units, 0.0)
        self.assertIn(
            "Bankroll guardrail: daily stop-loss reached after -1.60u settled today",
            report.data_warnings,
        )

    async def test_postprocess_caps_stake_when_bankroll_cooldown_is_active(self):
        data = MatchData(
            team1="Arsenal",
            team2="Chelsea",
            odds=OddsData(
                implied_prob_w1=0.48,
                implied_prob_draw=0.27,
                implied_prob_w2=0.25,
                over_2_5_odds=1.90,
                under_2_5_odds=1.95,
                btts_yes_odds=1.84,
                btts_no_odds=1.96,
                scrape_success=True,
            ),
            source_statuses=[
                SourceStatus(key="odds", label="Odds", field_name="odds", scrape_success=True),
                SourceStatus(key="form", label="Form", field_name="flashscore", scrape_success=True),
                SourceStatus(key="advanced", label="Advanced", field_name="advanced_stats", scrape_success=True),
            ],
        )

        with (
            patch.object(analyzer.config, "LIVE_1X2_HYBRID_ENABLED", False),
            patch.object(analyzer.config, "BANKROLL_DAILY_LIMIT_ENABLED", True),
            patch.object(analyzer.config, "BANKROLL_MAX_STAKED_PER_DAY_UNITS", 2.5),
            patch.object(analyzer.config, "BANKROLL_LOSS_STREAK_COOLDOWN", 3),
            patch.object(analyzer.config, "BANKROLL_COOLDOWN_STAKE_CAP_UNITS", 0.25),
            patch(
                "analyzer.db.get_bankroll_guardrails",
                new=AsyncMock(
                    return_value={
                        "recommended_bets_today": 2,
                        "staked_today_units": 1.9,
                        "settled_bets_today": 3,
                        "settled_profit_today_units": -0.9,
                        "recent_loss_streak": 3,
                    }
                ),
            ),
        ):
            report = await analyzer._postprocess_report_dict(
                {
                    "prob_w1": 58.0,
                    "prob_draw": 23.0,
                    "prob_w2": 19.0,
                    "result_confidence": "HIGH",
                    "prob_over_2_5": 55.0,
                    "prob_under_2_5": 45.0,
                    "expected_goals": 2.8,
                    "goals_confidence": "MEDIUM",
                    "asian_handicap_rec": "Arsenal -0.5",
                    "asian_handicap_confidence": "MEDIUM",
                    "prob_btts_yes": 54.0,
                    "prob_btts_no": 46.0,
                    "btts_confidence": "MEDIUM",
                    "team1_strengths": [],
                    "team1_weaknesses": [],
                    "team2_strengths": [],
                    "team2_weaknesses": [],
                    "top_recommendation": "Arsenal win",
                    "top_recommendation_odds": 2.05,
                    "top_confidence_pct": 71.0,
                    "risk_level": "LOW",
                    "analysis_summary": "Arsenal hold the stronger profile.",
                    "data_warnings": [],
                },
                data,
                [],
                prompt_version="test-prompt",
            )

        self.assertEqual(report.stake_units, 0.25)
        self.assertEqual(report.stake_label, "Minimal")
        self.assertEqual(report.risk_level, "HIGH")
        self.assertTrue(report.top_recommendation.lower().startswith("arsenal"))
        self.assertIn(
            "Bankroll guardrail: daily budget leaves only 0.60u available today",
            report.data_warnings,
        )
        self.assertIn(
            "Bankroll guardrail: loss-streak cooldown active after L3",
            report.data_warnings,
        )


if __name__ == "__main__":
    unittest.main()
