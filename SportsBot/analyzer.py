"""
AI integration — builds data context, sends to the selected backend, parses JSON.

Single-call mode supports:
  1. Direct Anthropic API (claude-sonnet-4-6) — default
  2. OpenClaw local gateway (google-antigravity/claude-opus-4-5-thinking) — USE_OPENCLAW=true

Multi-agent mode uses the in-process agents package:
  SportsBot -> Analyst -> Critic -> Synthesis -> shared post-processing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Optional

import anthropic
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

import config
import database as db
from baseline_model import (
    apply_pick_policy,
    apply_stake_recommendation,
    apply_top_pick_identity,
    build_market_candidates,
    build_top_pick_context,
    build_value_candidate_snapshot,
    build_value_bets,
    blend_report_with_numeric_baseline,
    build_numeric_baseline,
    derive_expanded_market_probabilities,
    extract_feature_snapshot,
)
from models import MatchData, PlayerStatus, PredictionReport
from trainable_numeric_model import fit_trainable_numeric_model, predict_trainable_numeric_model

logger = logging.getLogger(__name__)
_OPENCLAW_FALLBACK_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    ConnectionError,
    TimeoutError,
    ValueError,
    json.JSONDecodeError,
)
ANALYSIS_METHODOLOGY_VERSION = "2026-03-09-production-hardening-v1"
SINGLE_CALL_PROMPT_VERSION = "single-call-2026-03-09"
AGENT_CHAIN_PROMPT_VERSION = "agent-chain-2026-03-09"
PRE_AI_GATE_PROMPT_VERSION = "pre-ai-gate-2026-03-15"


@dataclass(slots=True)
class _AnalysisRuntimeMetrics:
    total_calls: int = 0
    pre_ai_gate_skips: int = 0
    agent_chain_runs: int = 0
    agent_chain_failures: int = 0
    single_call_runs: int = 0
    single_call_failures: int = 0
    fallback_reports: int = 0
    anthropic_requests: int = 0
    openclaw_requests: int = 0
    openclaw_fallbacks: int = 0
    live_hybrid_1x2_applied: int = 0
    ensemble_blend_applied: int = 0
    policy_flag_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "total_calls": self.total_calls,
            "pre_ai_gate_skips": self.pre_ai_gate_skips,
            "agent_chain_runs": self.agent_chain_runs,
            "agent_chain_failures": self.agent_chain_failures,
            "single_call_runs": self.single_call_runs,
            "single_call_failures": self.single_call_failures,
            "fallback_reports": self.fallback_reports,
            "anthropic_requests": self.anthropic_requests,
            "openclaw_requests": self.openclaw_requests,
            "openclaw_fallbacks": self.openclaw_fallbacks,
            "live_hybrid_1x2_applied": self.live_hybrid_1x2_applied,
            "ensemble_blend_applied": self.ensemble_blend_applied,
            "policy_flag_counts": dict(self.policy_flag_counts),
        }


_ANALYSIS_RUNTIME_METRICS = _AnalysisRuntimeMetrics()


def _record_analysis_metric(metric_name: str, delta: int = 1) -> None:
    current = getattr(_ANALYSIS_RUNTIME_METRICS, metric_name)
    setattr(_ANALYSIS_RUNTIME_METRICS, metric_name, current + delta)


def _record_policy_flags(flags: list[str]) -> None:
    for raw_flag in flags:
        flag = str(raw_flag).strip()
        if not flag:
            continue
        current = int(_ANALYSIS_RUNTIME_METRICS.policy_flag_counts.get(flag, 0) or 0)
        _ANALYSIS_RUNTIME_METRICS.policy_flag_counts[flag] = current + 1


def get_analysis_runtime_metrics_snapshot() -> dict[str, object]:
    return _ANALYSIS_RUNTIME_METRICS.as_dict()


def reset_analysis_runtime_metrics() -> None:
    global _ANALYSIS_RUNTIME_METRICS
    _ANALYSIS_RUNTIME_METRICS = _AnalysisRuntimeMetrics()


@dataclass(slots=True)
class _LiveHybrid1X2Runtime:
    model: object | None = None
    alpha: float = 0.0
    trained_rows: int = 0
    loaded_at_monotonic: float = 0.0


_LIVE_HYBRID_1X2_RUNTIME = _LiveHybrid1X2Runtime()
_LIVE_HYBRID_1X2_LOCKS_BY_LOOP: dict[int, asyncio.Lock] = {}


def _get_live_hybrid_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = id(loop)
    lock = _LIVE_HYBRID_1X2_LOCKS_BY_LOOP.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LIVE_HYBRID_1X2_LOCKS_BY_LOOP[key] = lock
    return lock


def reset_live_hybrid_1x2_runtime() -> None:
    global _LIVE_HYBRID_1X2_RUNTIME
    _LIVE_HYBRID_1X2_RUNTIME = _LiveHybrid1X2Runtime()
    _LIVE_HYBRID_1X2_LOCKS_BY_LOOP.clear()

# ── API Clients ────────────────────────────────────────────────────────────────

# Direct Anthropic client (used when USE_OPENCLAW=false)
_anthropic_client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
_openai_client = (
    AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    if config.OPENAI_API_KEY
    else None
)

# OpenAI-compatible client pointing at the local OpenClaw gateway
# (used when USE_OPENCLAW=true). Only built when a gateway token is configured —
# otherwise (e.g. USE_OMNIROUTE) the AsyncOpenAI ctor would raise on missing creds.
_openclaw_client = (
    AsyncOpenAI(
        api_key=config.OPENCLAW_GATEWAY_TOKEN,
        base_url=config.OPENCLAW_GATEWAY_URL,
    )
    if config.OPENCLAW_GATEWAY_TOKEN
    else None
)

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an elite football analyst specialising in statistical modelling, tactical assessment, and betting market analysis across top European and international leagues.

══════════════════════════════════════════
LEAGUE-SPECIFIC BASELINE KNOWLEDGE
══════════════════════════════════════════
Apply these per-league priors when adjusting xG and result probabilities:

• Premier League — avg 2.7 goals/match; BTTS 52%; draw rate 24%
• La Liga       — avg 2.6 goals/match; BTTS 50%; draw rate 26%
• Serie A       — avg 2.5 goals/match; BTTS 50%; draw rate 27%
• Bundesliga    — avg 3.1 goals/match; BTTS 58%; draw rate 22%
• Ligue 1       — avg 2.5 goals/match; BTTS 48%; draw rate 28%
• Champions UCL — avg 2.9 goals/match; BTTS 53%; draw rate 22%
• Conference CL — avg 2.7 goals/match; BTTS 50%; draw rate 24%
• MLS           — avg 2.8 goals/match; BTTS 50%; draw rate 20%
• Bundesliga 2  — avg 2.8 goals/match; BTTS 53%; draw rate 24%
• Unknown league — use avg 2.7 goals/match as neutral prior

Home advantage baseline: +7% win probability vs neutral venue.
If match is a cup final or neutral venue, reduce home advantage to +0%.

══════════════════════════════════════════
WEIGHTED xG FORMULA (Dixon-Coles enhanced)
══════════════════════════════════════════
Use this formula for expected_goals:
  t1_xg = (t1_scored × 0.6 + t2_conceded × 0.4)
  t2_xg = (t2_scored × 0.6 + t1_conceded × 0.4)
  match_xg = t1_xg + t2_xg
  Apply home advantage: t1_xg × 1.10 if team1 is at home.
  Cap between 1.0 and 5.5.

If real xG data is provided (from advanced stats), BLEND it 50/50 with goal averages:
  final_xg = goal_based_xg × 0.5 + real_xg × 0.5

Dixon-Coles correction: low-scoring outcomes (0-0, 1-0, 0-1, 1-1) are MORE likely
than pure Poisson suggests. League-specific ρ parameter controls this:
  Bundesliga ρ=-0.22, Serie A ρ=-0.17, La Liga ρ=-0.13, EPL ρ=-0.05

══════════════════════════════════════════
ASIAN HANDICAP MAPPING TABLE
══════════════════════════════════════════
Map your result probabilities to an AH recommendation:

  prob_w1 ≥ 72%  → team1 -1.5 (confident win by 2+)
  prob_w1 65-71% → team1 -1.0 (win or push on -1)
  prob_w1 57-64% → team1 -0.5 (win market)
  prob_w1 50-56% → team1 0 / Level (slight edge)
  48-50% either  → Level (0) — too close to pick
  prob_w2 50-56% → team2 0 / Level
  prob_w2 57-64% → team2 -0.5
  prob_w2 65-71% → team2 -1.0
  prob_w2 ≥ 72%  → team2 -1.5

If bookmaker AH data is present in Section 4, prioritise that line over the table above (it reflects real money flow). Check whether the bookmaker line agrees with your computed line — a mismatch is a signal of uncertainty: lower asian_handicap_confidence to "MEDIUM".

══════════════════════════════════════════
TRAP MATCH DETECTION
══════════════════════════════════════════
Evaluate BEFORE setting confidence. A match is a TRAP if ANY of:

1. Short-odds favourite (implied > 65%) has:
   - Last 3 form worse than 2W-1D or 1W-2D
   - Away from home AND home H2H record < 40% wins
   - 2+ key positional absences (not filled by equivalent quality)

2. H2H shows ≥ 3 draws in the last 5 meetings AND both teams low scorers

3. One team in strong momentum (4W in last 5) vs short-odds team having a
   "must-win" pressure match (e.g. risk of relegation, trophy in sight)

If a TRAP is detected:
- Add to analysis_summary: "⚠️ Trap match signal: [reason]"
- Pull result probabilities toward centre (max 60% for favourite)
- Set result_confidence to "MEDIUM" or "LOW"
- Set risk_level to "HIGH"

══════════════════════════════════════════
MARKET PRIORITY — O/U FOCUS
══════════════════════════════════════════
Our backtest on 5832 matches shows O/U 2.5 has the STRONGEST edge (57.3% accuracy).
1X2 market is hardest to beat (51.7%). Therefore:

- top_recommendation SHOULD prefer totals (Over/Under) when edge ≥ 2%
- Only recommend 1X2 when probability gap vs bookmaker is ≥ 5%
- For O/U: trust your xG-based model more than market consensus
- If Dixon-Coles matrix and market odds disagree on O/U by > 4%, flag as value bet
- BTTS is secondary (54.2% accuracy) — recommend only with high confidence

══════════════════════════════════════════
ANALYSIS METHODOLOGY (priority order)
══════════════════════════════════════════
1. Trap match check (above) — first, before everything else
2. xG model + Dixon-Coles matrix — compute O/U probability FIRST (priority market)
3. Form momentum — trend of last 3 vs last 5 vs last 10; acceleration matters
4. H2H patterns — psychological advantage, scoring tendency in this fixture
5. Injury/absence impact — rate key absences: Tier1 (starting XI top players) vs Tier2
6. Market calibration — your probability vs bookmaker implied = value gap
7. Asian handicap selection (mapping table above)

══════════════════════════════════════════
OUTPUT RULES
══════════════════════════════════════════
- Respond with ONLY a valid JSON object — no text, no markdown, no code fences
- Use the EXACT schema below — all fields required

JSON SCHEMA:
{
  "prob_w1": <float 0-100, probability team1 wins>,
  "prob_draw": <float 0-100>,
  "prob_w2": <float 0-100, probability team2 wins>,
  "result_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "prob_over_2_5": <float 0-100>,
  "prob_under_2_5": <float 0-100>,
  "expected_goals": <float, xG using weighted formula above>,
  "goals_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "asian_handicap_rec": <string, from mapping table above e.g. "Arsenal -0.5">,
  "asian_handicap_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "prob_btts_yes": <float 0-100>,
  "prob_btts_no": <float 0-100>,
  "btts_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "team1_strengths": [<string max 8 words>, ...],
  "team1_weaknesses": [<string max 8 words>, ...],
  "team2_strengths": [<string max 8 words>, ...],
  "team2_weaknesses": [<string max 8 words>, ...],
  "top_recommendation": <string, single best bet>,
  "top_recommendation_odds": <float|null, best available odds if in data>,
  "top_confidence_pct": <float 0-100>,
  "risk_level": <"HIGH"|"MEDIUM"|"LOW">,
  "analysis_summary": <string, 2-3 sentences narrative, include trap warning if any>,
  "data_warnings": [<string>, ...]
}

CALIBRATION INVARIANTS (CRITICAL):
- prob_w1 + prob_draw + prob_w2 MUST sum to EXACTLY 100.0
- prob_over_2_5 + prob_under_2_5 MUST sum to EXACTLY 100.0
- prob_btts_yes + prob_btts_no MUST sum to EXACTLY 100.0
- ≤ 1 data source available: ALL confidence fields must be "LOW"
- 2 data sources: max "MEDIUM" confidence
- 3+ sources: "HIGH" allowed only if evidence is clear and consistent
- risk_level "LOW": requires HIGH confidence + strong favourite + 3+ sources
- team1_strengths/weaknesses: 2-4 items each, max 8 words per item"""

# ── User prompt template ───────────────────────────────────────────────────────

_USER_PROMPT = """Analyse this upcoming football match and return your prediction JSON:

MATCH: {team1} vs {team2}
(TEAM1 = {team1} is treated as the home team unless context suggests otherwise)

======================================
DATA COLLECTED FROM MULTIPLE SOURCES
======================================
{data_context}

======================================
RAW DATA QUALITY: {raw_completeness_pct}% ({raw_ok}/{raw_total} total registered sources scraped successfully)
APPLICABLE SOURCE QUALITY: {applicable_completeness_pct}% ({applicable_ok}/{applicable_total} provider-covered sources scraped successfully)
{warnings_block}
======================================

Analyse all available data above. Consider:
- Form momentum (last 3 vs last 5 vs last 10 trend direction)
- Impact of key absences on the weaker side's defensive stability
- H2H patterns: does one side consistently dominate this fixture?
- Market efficiency gap: where does YOUR probability differ from bookmaker implied?
- xG model: [(t1_scored + t2_conceded)/2] + [(t2_scored + t1_conceded)/2]
- Asian handicap: which line offers clearest value vs your result probabilities?

Return ONLY the JSON object — nothing else."""


# ── Data context builder ───────────────────────────────────────────────────────

def _dedupe_preserve_order(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        value = str(item).strip()
        if value and value not in deduped:
            deduped.append(value)
    return deduped


def _build_motivation_filter_notes(data: MatchData) -> list[str]:
    notes: list[str] = []

    if data.match_context and data.match_context.scrape_success:
        context = data.match_context
        stage = str(context.stage or "").strip().lower()
        if context.is_second_leg:
            notes.append("Knockout second leg can distort urgency and late-game variance")
        elif "final" in stage:
            notes.append("Final-stage match usually keeps motivation high on both sides")
        elif context.is_knockout:
            notes.append("Knockout tie can override normal league-style incentives")

    if data.standings_context and data.standings_context.scrape_success:
        standings = data.standings_context
        if (
            standings.point_gap is not None
            and standings.position_gap is not None
            and abs(standings.point_gap) <= 3
            and abs(standings.position_gap) <= 2
        ):
            notes.append("Direct table-rival matchup raises six-pointer intensity")

        for team_name, summary in [
            (data.team1, standings.team1_overall),
            (data.team2, standings.team2_overall),
        ]:
            played = int(summary.played or 0)
            position = summary.position
            if position is None or played < 24:
                continue
            if position <= 2:
                notes.append(f"{team_name} are in a title-race position")
            elif position >= 16:
                notes.append(f"{team_name} are under relegation pressure")

    if data.schedule_load and data.schedule_load.scrape_success:
        schedule = data.schedule_load
        for team_name, summary in [
            (data.team1, schedule.team1),
            (data.team2, schedule.team2),
        ]:
            if summary.matches_next_7d >= 3 or summary.next_short_turnaround:
                notes.append(f"{team_name} may rotate because of schedule congestion")
            elif summary.short_rest:
                notes.append(f"{team_name} come in on short rest")

    return _dedupe_preserve_order(notes)[:5]


def _squad_availability_counts(players: list) -> tuple[int, int]:
    unavailable = 0
    doubtful = 0
    for player in players:
        status = getattr(player, "status", None)
        if status in {PlayerStatus.INJURED, PlayerStatus.SUSPENDED}:
            unavailable += 1
        elif status == PlayerStatus.DOUBTFUL:
            doubtful += 1
    return unavailable, doubtful


def _build_squad_filter_notes(data: MatchData) -> list[str]:
    if not data.sofascore or not data.sofascore.scrape_success:
        return []

    notes: list[str] = []
    team_counts: list[tuple[str, int, int]] = []
    for team_name, absences, lineup in [
        (data.team1, data.sofascore.team1_key_absences, data.sofascore.team1_predicted_lineup),
        (data.team2, data.sofascore.team2_key_absences, data.sofascore.team2_predicted_lineup),
    ]:
        unavailable, doubtful = _squad_availability_counts(absences)
        team_counts.append((team_name, unavailable, doubtful))
        if unavailable >= 3:
            notes.append(f"{team_name} are missing {unavailable} confirmed key players")
        elif unavailable >= 2:
            notes.append(f"{team_name} have multiple confirmed key absences")
        if doubtful >= 2:
            notes.append(f"{team_name} have multiple doubtful starters")
        if lineup and len(lineup) < 11:
            notes.append(f"{team_name} predicted lineup is incomplete")

    if len(team_counts) == 2:
        team1_name, team1_unavailable, _ = team_counts[0]
        team2_name, team2_unavailable, _ = team_counts[1]
        if abs(team1_unavailable - team2_unavailable) >= 2:
            healthier_team = team2_name if team1_unavailable > team2_unavailable else team1_name
            notes.append(f"Squad availability edge favours {healthier_team}")

    return _dedupe_preserve_order(notes)[:5]


def _build_context_filter_warnings(data: MatchData) -> list[str]:
    warnings: list[str] = []
    # Referee context
    if data.match_context and data.match_context.scrape_success and data.match_context.referee_name:
        try:
            from referee import get_referee_profile
            rp = get_referee_profile(data.match_context.referee_name)
            if rp and rp.matches >= 15:
                if rp.goals_vs_avg >= 0.15:
                    warnings.append(
                        f"Referee {rp.name}: high-scoring tendency "
                        f"({rp.avg_goals:.2f} avg goals, O2.5 {rp.over_2_5_pct:.0f}%, "
                        f"{rp.goals_vs_avg:+.2f} vs avg in {rp.matches} matches)"
                    )
                elif rp.goals_vs_avg <= -0.15:
                    warnings.append(
                        f"Referee {rp.name}: low-scoring tendency "
                        f"({rp.avg_goals:.2f} avg goals, O2.5 {rp.over_2_5_pct:.0f}%, "
                        f"{rp.goals_vs_avg:+.2f} vs avg in {rp.matches} matches)"
                    )
        except Exception:
            pass
    # Line movement warnings
    if data.market_signals and data.market_signals.scrape_success:
        ms = data.market_signals
        for label, sig_dict in [("1X2", ms.one_x_two), ("O/U", ms.over_under_2_5)]:
            for sel_name, sig in sig_dict.items():
                if abs(sig.movement_score) >= 3:
                    direction = "shortening" if sig.movement_score > 0 else "drifting"
                    warnings.append(
                        f"Sharp line move: {label} {sel_name} {direction} "
                        f"(movement={sig.movement_score:+d})"
                    )
    if data.weather_context and data.weather_context.scrape_success:
        for warning in data.weather_context.weather_warnings:
            warnings.append(f"Weather filter: {warning}")
    for note in _build_motivation_filter_notes(data):
        warnings.append(f"Motivation filter: {note}")
    for note in _build_squad_filter_notes(data):
        warnings.append(f"Squad filter: {note}")
    return _dedupe_preserve_order(warnings)


def _elevate_risk_level(current_level: str, minimum_level: str) -> str:
    ranks = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    current = str(current_level or "HIGH").upper()
    minimum = str(minimum_level or current).upper()
    if ranks.get(current, 2) >= ranks.get(minimum, 2):
        return current
    return minimum


def _apply_context_risk_adjustments(report_dict: dict, data: MatchData) -> None:
    if not data.sofascore or not data.sofascore.scrape_success:
        return

    squad_notes = _build_squad_filter_notes(data)
    if not squad_notes:
        return

    severe_risk = False
    moderate_risk = False
    for absences, lineup in [
        (data.sofascore.team1_key_absences, data.sofascore.team1_predicted_lineup),
        (data.sofascore.team2_key_absences, data.sofascore.team2_predicted_lineup),
    ]:
        unavailable, doubtful = _squad_availability_counts(absences)
        if unavailable >= 3 or (unavailable >= 2 and doubtful >= 2):
            severe_risk = True
        elif unavailable >= 2 or doubtful >= 2 or (lineup and len(lineup) < 11):
            moderate_risk = True

    if not severe_risk and not moderate_risk:
        return

    minimum_risk = "HIGH" if severe_risk else "MEDIUM"
    report_dict["risk_level"] = _elevate_risk_level(
        str(report_dict.get("risk_level") or "HIGH"),
        minimum_risk,
    )
    confidence_cap = 60.0 if severe_risk else 64.0
    report_dict["top_confidence_pct"] = min(
        float(report_dict.get("top_confidence_pct") or 0.0),
        confidence_cap,
    )

    warnings = list(report_dict.get("data_warnings") or [])
    gate_warning = (
        "Squad risk gate: confirmed absences materially raise lineup uncertainty"
        if severe_risk
        else "Squad risk gate: lineup uncertainty keeps this out of low-risk territory"
    )
    if gate_warning not in warnings:
        warnings.append(gate_warning)
    report_dict["data_warnings"] = warnings

    summary = str(report_dict.get("analysis_summary") or "").strip()
    summary_sentence = (
        "Squad risk filter raised match volatility because confirmed absences materially weaken lineup certainty."
        if severe_risk
        else "Squad risk filter keeps the setup volatile because lineup availability is still uncertain."
    )
    if summary_sentence not in summary:
        summary = f"{summary} {summary_sentence}".strip()
    report_dict["analysis_summary"] = summary


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_triplet_pct(p1: float, pd: float, p2: float) -> tuple[float, float, float]:
    values = [max(float(p1), 5.0), max(float(pd), 8.0), max(float(p2), 5.0)]
    total = sum(values)
    if total <= 0:
        return 33.4, 33.3, 33.3
    normalized = [round((value * 100.0) / total, 1) for value in values]
    diff = round(100.0 - sum(normalized), 1)
    normalized[0] = round(normalized[0] + diff, 1)
    return normalized[0], normalized[1], normalized[2]


def _normalize_pair_pct(px: float, py: float) -> tuple[float, float]:
    values = [max(float(px), 10.0), max(float(py), 10.0)]
    total = sum(values)
    if total <= 0:
        return 50.0, 50.0
    normalized = [round((value * 100.0) / total, 1) for value in values]
    diff = round(100.0 - sum(normalized), 1)
    normalized[0] = round(normalized[0] + diff, 1)
    return normalized[0], normalized[1]


def _normalized_competition_key_text(value: Optional[str]) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower())
    return " ".join(normalized.split())


def _league_style_profile(competition_name: Optional[str]) -> Optional[dict[str, float | str]]:
    key = _normalized_competition_key_text(competition_name)
    if not key:
        return None

    profiles = (
        (
            ("2 bundesliga", "bundesliga 2", "2 bundesliga germany", "zweite bundesliga"),
            {"label": "2. Bundesliga", "avg_goals": 2.8, "btts_yes": 53.0},
        ),
        (
            ("champions league", "uefa champions league", "ucl"),
            {"label": "Champions League", "avg_goals": 2.9, "btts_yes": 53.0},
        ),
        (
            ("europa league", "uefa europa league", "uel"),
            {"label": "Europa League", "avg_goals": 2.8, "btts_yes": 52.0},
        ),
        (
            ("conference league", "uefa conference league", "uecl"),
            {"label": "Conference League", "avg_goals": 2.7, "btts_yes": 50.0},
        ),
        (
            ("premier league",),
            {"label": "Premier League", "avg_goals": 2.7, "btts_yes": 52.0},
        ),
        (
            ("la liga", "laliga"),
            {"label": "La Liga", "avg_goals": 2.6, "btts_yes": 50.0},
        ),
        (
            ("serie a",),
            {"label": "Serie A", "avg_goals": 2.5, "btts_yes": 50.0},
        ),
        (
            ("bundesliga",),
            {"label": "Bundesliga", "avg_goals": 3.1, "btts_yes": 58.0},
        ),
        (
            ("ligue 1", "ligue1"),
            {"label": "Ligue 1", "avg_goals": 2.5, "btts_yes": 48.0},
        ),
        (
            ("mls", "major league soccer"),
            {"label": "MLS", "avg_goals": 2.8, "btts_yes": 51.0},
        ),
        (
            ("eredivisie",),
            {"label": "Eredivisie", "avg_goals": 3.0, "btts_yes": 55.0},
        ),
        (
            ("championship", "efl championship"),
            {"label": "Championship", "avg_goals": 2.5, "btts_yes": 47.0},
        ),
        (
            ("primeira liga",),
            {"label": "Primeira Liga", "avg_goals": 2.5, "btts_yes": 49.0},
        ),
        (
            ("super lig", "superlig"),
            {"label": "Super Lig", "avg_goals": 2.9, "btts_yes": 53.0},
        ),
        (
            ("jupiler pro league", "belgian pro league"),
            {"label": "Jupiler Pro League", "avg_goals": 2.9, "btts_yes": 54.0},
        ),
    )
    for aliases, profile in profiles:
        if any(alias in key for alias in aliases):
            return profile
    return None


def _normalized_position_token(position: str) -> str:
    normalized = re.sub(r"[^A-Z]", "", str(position or "").strip().upper())
    token_map = {
        "G": "GK",
        "GK": "GK",
        "GOALKEEPER": "GK",
        "D": "DEF",
        "DEF": "DEF",
        "DEFENDER": "DEF",
        "CB": "CB",
        "CENTREBACK": "CB",
        "CENTERBACK": "CB",
        "DC": "CB",
        "LB": "FB",
        "RB": "FB",
        "FB": "FB",
        "FULLBACK": "FB",
        "LWB": "WB",
        "RWB": "WB",
        "WB": "WB",
        "WINGBACK": "WB",
        "M": "MID",
        "MID": "MID",
        "MIDFIELDER": "MID",
        "DM": "DM",
        "CDM": "DM",
        "CM": "CM",
        "MC": "CM",
        "AM": "AM",
        "CAM": "AM",
        "LW": "W",
        "RW": "W",
        "LM": "W",
        "RM": "W",
        "W": "W",
        "WINGER": "W",
        "F": "FWD",
        "FW": "FWD",
        "FWD": "FWD",
        "FORWARD": "FWD",
        "ATT": "FWD",
        "ATTACKER": "FWD",
        "ST": "ST",
        "CF": "ST",
        "SS": "ST",
        "STRIKER": "ST",
    }
    return token_map.get(normalized, normalized)


def _position_profile(position: str) -> dict[str, float]:
    token = _normalized_position_token(position)
    if token == "GK":
        return {
            "availability": 1.35,
            "attack": 0.0,
            "defense": 1.7,
            "control": 0.15,
            "keeper": 1.8,
            "finishing": 0.0,
        }
    if token in {"CB", "DEF"}:
        return {
            "availability": 1.15,
            "attack": 0.1,
            "defense": 1.35,
            "control": 0.2,
            "keeper": 0.0,
            "finishing": 0.0,
        }
    if token in {"FB", "WB"}:
        return {
            "availability": 1.0,
            "attack": 0.35,
            "defense": 0.9,
            "control": 0.3,
            "keeper": 0.0,
            "finishing": 0.0,
        }
    if token == "DM":
        return {
            "availability": 1.0,
            "attack": 0.35,
            "defense": 0.4,
            "control": 1.15,
            "keeper": 0.0,
            "finishing": 0.0,
        }
    if token in {"CM", "MID"}:
        return {
            "availability": 1.0,
            "attack": 0.55,
            "defense": 0.2,
            "control": 1.0,
            "keeper": 0.0,
            "finishing": 0.1,
        }
    if token in {"AM", "W"}:
        return {
            "availability": 1.05,
            "attack": 1.0,
            "defense": 0.05,
            "control": 0.55,
            "keeper": 0.0,
            "finishing": 0.45,
        }
    if token in {"ST", "FWD"}:
        return {
            "availability": 1.1,
            "attack": 1.3,
            "defense": 0.0,
            "control": 0.1,
            "keeper": 0.0,
            "finishing": 1.2,
        }
    return {
        "availability": 0.9,
        "attack": 0.3,
        "defense": 0.25,
        "control": 0.25,
        "keeper": 0.0,
        "finishing": 0.15,
    }


def _absence_impact(players: list) -> dict[str, float]:
    impact = {
        "availability_load": 0.0,
        "attack_load": 0.0,
        "defense_load": 0.0,
        "control_load": 0.0,
        "keeper_load": 0.0,
        "finishing_load": 0.0,
    }
    for player in players:
        status = getattr(player, "status", None)
        if status in {PlayerStatus.INJURED, PlayerStatus.SUSPENDED}:
            base_weight = 1.0
        elif status == PlayerStatus.DOUBTFUL:
            base_weight = 0.45
        else:
            continue

        profile = _position_profile(getattr(player, "position", ""))
        impact["availability_load"] += base_weight * profile["availability"]
        impact["attack_load"] += base_weight * profile["attack"]
        impact["defense_load"] += base_weight * profile["defense"]
        impact["control_load"] += base_weight * profile["control"]
        impact["keeper_load"] += base_weight * profile["keeper"]
        impact["finishing_load"] += base_weight * profile["finishing"]
    return impact


def _apply_quality_context_adjustments(report_dict: dict, data: MatchData) -> list[str]:
    notes: list[str] = []
    goals_delta = 0.0
    over_shift = 0.0
    btts_shift = 0.0
    win_shift = 0.0
    draw_shift = 0.0

    if data.weather_context and data.weather_context.scrape_success:
        weather = data.weather_context
        weather_drag = 0.0
        weather_reasons: list[str] = []
        if (
            weather.wind_speed_kmh is not None
            and weather.wind_speed_kmh >= config.WEATHER_HIGH_WIND_KMH
        ):
            weather_drag += 0.12
            weather_reasons.append("high wind")
        if (
            weather.wind_gusts_kmh is not None
            and weather.wind_gusts_kmh >= config.WEATHER_HIGH_GUST_KMH
        ):
            weather_drag += 0.08
            weather_reasons.append("strong gusts")
        if (
            weather.precipitation_mm is not None
            and weather.precipitation_mm >= config.WEATHER_WET_PITCH_MM
        ):
            weather_drag += 0.10
            weather_reasons.append("wet pitch")
        if (
            weather.precipitation_probability_pct is not None
            and weather.precipitation_probability_pct >= config.WEATHER_WET_PROBABILITY_PCT
        ):
            weather_drag += 0.06
            if "wet pitch" not in weather_reasons:
                weather_reasons.append("rain risk")
        if weather.temperature_c is not None and weather.temperature_c <= config.WEATHER_FREEZING_C:
            weather_drag += 0.08
            weather_reasons.append("cold temperature")
        if weather.temperature_c is not None and weather.temperature_c >= config.WEATHER_HEAT_C:
            weather_drag += 0.05
            weather_reasons.append("heat")
        if weather.weather_code in {71, 73, 75, 77, 85, 86}:
            weather_drag += 0.10
            weather_reasons.append("snow")
        if weather.weather_code in {95, 96, 99}:
            weather_drag += 0.05
            weather_reasons.append("thunderstorm risk")

        if weather_drag > 0:
            goals_delta -= weather_drag
            over_shift -= weather_drag * 16.0
            btts_shift -= weather_drag * 9.0
            notes.append(
                "totals cooled for " + ", ".join(_dedupe_preserve_order(weather_reasons)[:3])
            )

    if data.sofascore and data.sofascore.scrape_success:
        team1_absence_impact = _absence_impact(data.sofascore.team1_key_absences)
        team2_absence_impact = _absence_impact(data.sofascore.team2_key_absences)

        availability_delta = (
            team2_absence_impact["availability_load"]
            - team1_absence_impact["availability_load"]
        )
        if abs(availability_delta) >= 0.9:
            shift = _clamp(availability_delta * 1.45, -5.0, 5.0)
            win_shift += shift
            notes.append(
                f"1X2 shifted toward {data.team1 if shift > 0 else data.team2} on squad availability"
            )

        control_delta = (
            team2_absence_impact["control_load"]
            - team1_absence_impact["control_load"]
        )
        if abs(control_delta) >= 0.8:
            shift = _clamp(control_delta * 0.85, -2.0, 2.0)
            win_shift += shift
            notes.append(
                f"1X2 nudged toward {data.team1 if shift > 0 else data.team2} on midfield control losses"
            )

        backline_delta = (
            (team2_absence_impact["defense_load"] + team2_absence_impact["keeper_load"])
            - (team1_absence_impact["defense_load"] + team1_absence_impact["keeper_load"])
        )
        if abs(backline_delta) >= 1.0:
            shift = _clamp(backline_delta * 0.55, -2.0, 2.0)
            win_shift += shift
            notes.append(
                f"1X2 leaned toward {data.team1 if shift > 0 else data.team2} on back-line weakness"
            )

        finishing_delta = (
            team2_absence_impact["finishing_load"]
            - team1_absence_impact["finishing_load"]
        )
        if abs(finishing_delta) >= 0.8:
            shift = _clamp(finishing_delta * 0.55, -1.8, 1.8)
            win_shift += shift
            notes.append(
                f"1X2 trimmed toward {data.team1 if shift > 0 else data.team2} on finishing absences"
            )

        goal_environment_delta = (
            (team1_absence_impact["defense_load"] + team2_absence_impact["defense_load"]) * 0.8
            + (team1_absence_impact["keeper_load"] + team2_absence_impact["keeper_load"]) * 0.95
            - (team1_absence_impact["attack_load"] + team2_absence_impact["attack_load"]) * 0.55
            - (team1_absence_impact["finishing_load"] + team2_absence_impact["finishing_load"]) * 0.85
            - (team1_absence_impact["control_load"] + team2_absence_impact["control_load"]) * 0.25
        )
        if abs(goal_environment_delta) >= 0.75:
            delta = _clamp(goal_environment_delta * 0.075, -0.28, 0.28)
            goals_delta += delta
            over_shift += delta * 15.0
            btts_shift += delta * 8.5
            if delta > 0:
                if (
                    team1_absence_impact["keeper_load"] + team2_absence_impact["keeper_load"]
                    >= team1_absence_impact["defense_load"] + team2_absence_impact["defense_load"]
                ):
                    notes.append("goal environment lifted by goalkeeper absences")
                else:
                    notes.append("goal environment lifted by defensive absences")
            else:
                if (
                    team1_absence_impact["finishing_load"] + team2_absence_impact["finishing_load"]
                    >= team1_absence_impact["attack_load"] + team2_absence_impact["attack_load"]
                ):
                    notes.append("goal environment reduced by finishing absences")
                else:
                    notes.append("goal environment reduced by attacking absences")

    if data.schedule_load and data.schedule_load.scrape_success:
        schedule = data.schedule_load
        if schedule.rest_gap_hours is not None and abs(schedule.rest_gap_hours) >= 18.0:
            rest_shift = _clamp(schedule.rest_gap_hours * 0.05, -3.0, 3.0)
            win_shift += rest_shift
            notes.append(
                f"1X2 shifted toward {data.team1 if rest_shift > 0 else data.team2} on rest advantage"
            )

        if schedule.team1.short_rest and schedule.team2.short_rest:
            goals_delta -= 0.08
            over_shift -= 1.6
            btts_shift -= 0.8
            notes.append("totals cooled for both teams on short rest")
        elif schedule.team1.short_rest or schedule.team2.short_rest:
            goals_delta -= 0.05
            over_shift -= 1.0
            if schedule.team1.short_rest and not schedule.team2.short_rest:
                win_shift -= 0.8
                notes.append(f"1X2 trimmed against {data.team1} for short rest")
            elif schedule.team2.short_rest and not schedule.team1.short_rest:
                win_shift += 0.8
                notes.append(f"1X2 shifted toward {data.team1} because {data.team2} are on short rest")

        combined_next_load = int(schedule.team1.matches_next_7d or 0) + int(schedule.team2.matches_next_7d or 0)
        if combined_next_load >= 5:
            goals_delta -= 0.06
            over_shift -= 1.2
            notes.append("totals cooled for heavy next-week schedule density")

    if data.match_context and data.match_context.scrape_success:
        context = data.match_context
        stage = str(context.stage or "").strip().lower()
        if context.is_second_leg:
            goals_delta -= 0.04
            over_shift -= 0.8
            notes.append("tempo trimmed for second-leg caution")
        elif "final" in stage:
            draw_shift += 1.6
            goals_delta -= 0.08
            over_shift -= 1.4
            notes.append("draw bias added for final-stage caution")
        elif context.is_knockout:
            draw_shift += 0.8
            goals_delta -= 0.04
            over_shift -= 0.6
            notes.append("draw bias added for knockout-game caution")

    if data.standings_context and data.standings_context.scrape_success:
        standings = data.standings_context
        if (
            standings.point_gap is not None
            and standings.position_gap is not None
            and abs(standings.point_gap) <= 3
            and abs(standings.position_gap) <= 2
        ):
            draw_shift += 1.2
            goals_delta -= 0.06
            over_shift -= 1.0
            notes.append("draw bias added for direct table-rival tension")

    competition_profile = _league_style_profile(_recalibration_competition_name(data))
    if competition_profile:
        league_goal_bias = float(competition_profile["avg_goals"]) - 2.7
        league_btts_bias = float(competition_profile["btts_yes"]) - 50.0
        league_goals_delta = _clamp(league_goal_bias * 0.32, -0.14, 0.18)
        league_over_shift = _clamp(league_goal_bias * 8.0, -1.8, 3.2)
        league_btts_shift = _clamp(league_btts_bias * 0.45, -1.8, 3.6)

        if abs(league_goals_delta) >= 0.03 or abs(league_btts_shift) >= 0.6:
            goals_delta += league_goals_delta
            over_shift += league_over_shift
            btts_shift += league_btts_shift
            profile_label = str(competition_profile["label"])
            if league_goals_delta >= 0.06 and league_btts_shift >= 1.0:
                notes.append(f"league style prior warmed totals and BTTS for {profile_label}")
            elif league_goals_delta <= -0.06 and league_btts_shift <= -0.8:
                notes.append(f"league style prior cooled totals and BTTS for {profile_label}")
            elif league_goals_delta >= 0.06:
                notes.append(f"league style prior warmed totals for {profile_label}")
            elif league_goals_delta <= -0.06:
                notes.append(f"league style prior cooled totals for {profile_label}")
            elif league_btts_shift >= 1.0:
                notes.append(f"league style prior nudged BTTS upward for {profile_label}")
            elif league_btts_shift <= -0.8:
                notes.append(f"league style prior nudged BTTS downward for {profile_label}")

    if win_shift or draw_shift:
        prob_w1, prob_draw, prob_w2 = _normalize_triplet_pct(
            float(report_dict.get("prob_w1") or 0.0) + win_shift - (draw_shift / 2.0),
            float(report_dict.get("prob_draw") or 0.0) + draw_shift,
            float(report_dict.get("prob_w2") or 0.0) - win_shift - (draw_shift / 2.0),
        )
        report_dict["prob_w1"] = prob_w1
        report_dict["prob_draw"] = prob_draw
        report_dict["prob_w2"] = prob_w2

    if goals_delta:
        current_expected_goals = float(report_dict.get("expected_goals") or 0.0)
        report_dict["expected_goals"] = round(
            _clamp(current_expected_goals + goals_delta, 1.4, 4.6),
            2,
        )

    if over_shift:
        prob_over, prob_under = _normalize_pair_pct(
            float(report_dict.get("prob_over_2_5") or 0.0) + over_shift,
            float(report_dict.get("prob_under_2_5") or 0.0) - over_shift,
        )
        report_dict["prob_over_2_5"] = prob_over
        report_dict["prob_under_2_5"] = prob_under

    if btts_shift:
        prob_btts_yes, prob_btts_no = _normalize_pair_pct(
            float(report_dict.get("prob_btts_yes") or 0.0) + btts_shift,
            float(report_dict.get("prob_btts_no") or 0.0) - btts_shift,
        )
        report_dict["prob_btts_yes"] = prob_btts_yes
        report_dict["prob_btts_no"] = prob_btts_no

    return _dedupe_preserve_order(notes)[:3]


def _append_unique_warning(report_dict: dict, warning_text: str) -> None:
    warnings = list(report_dict.get("data_warnings") or [])
    if warning_text not in warnings:
        warnings.append(warning_text)
    report_dict["data_warnings"] = warnings


def _append_summary_sentence(report_dict: dict, sentence: str) -> None:
    summary = str(report_dict.get("analysis_summary") or "").strip()
    if sentence not in summary:
        summary = f"{summary} {sentence}".strip()
    report_dict["analysis_summary"] = summary


def _stake_label_for_units(stake_units: float) -> str:
    if stake_units >= 1.25:
        return "High"
    if stake_units >= 1.0:
        return "Standard"
    if stake_units >= 0.75:
        return "Medium"
    if stake_units >= 0.5:
        return "Small"
    if stake_units > 0:
        return "Minimal"
    return "No bet"


def _quantize_stake_units_down(max_units: float) -> float:
    for allowed in (1.25, 1.0, 0.75, 0.5, 0.25):
        if max_units + 1e-9 >= allowed:
            return allowed
    return 0.0


def _set_no_bet_due_bankroll(report_dict: dict, *, reason: str, detail: str) -> None:
    report_dict["top_recommendation"] = f"No bet - {reason}"
    report_dict["top_recommendation_odds"] = None
    report_dict["top_market_code"] = "NO_BET"
    report_dict["top_selection_code"] = "NO_BET"
    report_dict["risk_level"] = _elevate_risk_level(
        str(report_dict.get("risk_level") or "HIGH"),
        "HIGH",
    )
    report_dict["stake_units"] = 0.0
    report_dict["stake_label"] = "No bet"
    report_dict["stake_rationale"] = detail
    _append_unique_warning(report_dict, f"Bankroll guardrail: {detail}")
    _append_summary_sentence(report_dict, detail)


def _apply_bankroll_guardrails(report_dict: dict, guardrails: Optional[dict[str, object]]) -> None:
    if not config.BANKROLL_GUARDRAILS_ENABLED:
        return

    recommendation = str(report_dict.get("top_recommendation") or "").strip().lower()
    current_stake = float(report_dict.get("stake_units") or 0.0)
    if not recommendation or recommendation.startswith("no bet") or current_stake <= 0.0:
        return
    if not guardrails:
        return
    if not config.BANKROLL_DAILY_LIMIT_ENABLED:
        return

    settled_profit_today = float(guardrails.get("settled_profit_today_units") or 0.0)
    if settled_profit_today <= -float(config.BANKROLL_DAILY_STOP_LOSS_UNITS):
        detail = (
            f"daily stop-loss reached after {settled_profit_today:+.2f}u settled today"
        )
        _set_no_bet_due_bankroll(
            report_dict,
            reason="daily bankroll stop-loss reached",
            detail=detail,
        )
        return

    recommended_bets_today = int(guardrails.get("recommended_bets_today") or 0)
    if recommended_bets_today >= int(config.BANKROLL_MAX_BETS_PER_DAY):
        detail = (
            f"daily bet limit already reached ({recommended_bets_today}/{int(config.BANKROLL_MAX_BETS_PER_DAY)})"
        )
        _set_no_bet_due_bankroll(
            report_dict,
            reason="daily bet limit reached",
            detail=detail,
        )
        return

    remaining_daily_budget = float(config.BANKROLL_MAX_STAKED_PER_DAY_UNITS) - float(
        guardrails.get("staked_today_units") or 0.0
    )
    current_cap = current_stake
    cap_notes: list[str] = []

    if remaining_daily_budget < 0.25:
        detail = (
            f"daily stake budget exhausted ({float(guardrails.get('staked_today_units') or 0.0):.2f}u/"
            f"{float(config.BANKROLL_MAX_STAKED_PER_DAY_UNITS):.2f}u already allocated today)"
        )
        _set_no_bet_due_bankroll(
            report_dict,
            reason="daily bankroll budget exhausted",
            detail=detail,
        )
        return
    if remaining_daily_budget < current_cap:
        current_cap = min(current_cap, remaining_daily_budget)
        cap_notes.append(
            f"daily budget leaves only {remaining_daily_budget:.2f}u available today"
        )

    recent_loss_streak = int(guardrails.get("recent_loss_streak") or 0)
    if recent_loss_streak >= int(config.BANKROLL_LOSS_STREAK_COOLDOWN):
        current_cap = min(current_cap, float(config.BANKROLL_COOLDOWN_STAKE_CAP_UNITS))
        report_dict["risk_level"] = _elevate_risk_level(
            str(report_dict.get("risk_level") or "HIGH"),
            "HIGH",
        )
        cap_notes.append(
            f"loss-streak cooldown active after L{recent_loss_streak}"
        )

    if not cap_notes:
        return

    capped_stake = _quantize_stake_units_down(current_cap)
    if capped_stake <= 0.0:
        _set_no_bet_due_bankroll(
            report_dict,
            reason="bankroll guardrail cap reached",
            detail=", ".join(cap_notes),
        )
        return

    if capped_stake < current_stake:
        report_dict["stake_units"] = capped_stake
        report_dict["stake_label"] = _stake_label_for_units(capped_stake)
        report_dict["stake_rationale"] = (
            f"Bankroll guardrail capped the stake from {current_stake:.2f}u to {capped_stake:.2f}u "
            f"because {', '.join(cap_notes)}."
        )
    else:
        existing_rationale = str(report_dict.get("stake_rationale") or "").strip()
        extra_rationale = f"Bankroll guardrail active because {', '.join(cap_notes)}."
        report_dict["stake_rationale"] = (
            f"{existing_rationale} {extra_rationale}".strip()
            if existing_rationale
            else extra_rationale
        )

    for note in cap_notes:
        _append_unique_warning(report_dict, f"Bankroll guardrail: {note}")
    _append_summary_sentence(
        report_dict,
        f"Bankroll guardrail tightened position sizing because {', '.join(cap_notes)}.",
    )


def _build_data_context(
    data: MatchData,
    calibration_block: str = "",
    team_memory_block: str = "",
    elo_block: str = "",
    numeric_baseline_block: str = "",
) -> str:
    lines: list[str] = []

    # ── Data quality summary ───────────────────────────────────────────────
    pct = int(data.data_completeness * 100)
    apct = int(data.applicable_data_completeness * 100)
    lines.append("─── DATA QUALITY SUMMARY ───")
    lines.append(
        f"Sources OK: {data.successful_source_count}/{data.total_source_count} "
        f"(raw {pct}%, applicable {apct}%)"
    )
    if data.failed_source_messages:
        lines.append("Failed sources:")
        for msg in data.failed_source_messages:
            lines.append(f"  ✗ {msg}")
    if data.unsupported_source_messages:
        for msg in data.unsupported_source_messages:
            lines.append(f"  ⚠ {msg}")
    if pct < 50:
        lines.append(
            "⚠ LOW DATA COVERAGE — reduce confidence in all markets accordingly. "
            "Do not claim high confidence when data is incomplete."
        )

    # ── Elo ratings ──────────────────────────────────────────────────────────
    if elo_block:
        lines.append("\n" + elo_block)

    # ── Section 1: Form & H2H ──────────────────────────────────────────────
    lines.append("\n─── SECTION 1: RECENT FORM & HEAD-TO-HEAD ───")
    if data.match_context and data.match_context.scrape_success:
        mc = data.match_context
        lines.append("\nUpcoming fixture context:")
        if mc.competition_name:
            lines.append(f"Competition: {mc.competition_name}")
        if mc.kickoff_utc:
            lines.append(f"Kickoff (UTC): {mc.kickoff_utc}")
        if mc.venue_name:
            lines.append(f"Venue: {mc.venue_name}")
        if mc.is_neutral_venue is not None:
            lines.append(f"Neutral venue: {'yes' if mc.is_neutral_venue else 'no'}")
        if mc.is_team1_home is not None:
            lines.append(
                f"Expected venue split: {data.team1} is "
                f"{'HOME' if mc.is_team1_home else 'AWAY'}"
            )
        if mc.stage:
            lines.append(f"Stage/Round: {mc.stage}")
        if mc.is_knockout:
            lines.append("Match type: knockout tie")
        if mc.is_second_leg:
            lines.append("Tie state: second leg")

    if data.flashscore and data.flashscore.scrape_success:
        fs = data.flashscore

        def form_str(matches) -> str:
            return "".join(m.result.value for m in matches[:5]) or "N/A"

        lines.append(f"\n{data.team1} last 5 form: {form_str(fs.team1_form)}")
        lines.append(f"{data.team2} last 5 form: {form_str(fs.team2_form)}")

        if fs.team1_form:
            lines.append(f"\n{data.team1} recent matches:")
            for m in fs.team1_form[:8]:
                lines.append(
                    f"  {m.date} | {'HOME' if m.home_away == 'H' else 'AWAY'} "
                    f"vs {m.opponent} | {m.result.value} {m.score}"
                )

        if fs.team2_form:
            lines.append(f"\n{data.team2} recent matches:")
            for m in fs.team2_form[:8]:
                lines.append(
                    f"  {m.date} | {'HOME' if m.home_away == 'H' else 'AWAY'} "
                    f"vs {m.opponent} | {m.result.value} {m.score}"
                )

        if fs.league_name and not (
            data.match_context
            and data.match_context.scrape_success
            and data.match_context.competition_name
        ):
            lines.append(f"\nLeague/Competition: {fs.league_name}")

        lines.append(
            f"\n{data.team1} goals per game: "
            f"{fs.team1_goals_scored_avg} scored, {fs.team1_goals_conceded_avg} conceded"
        )
        lines.append(
            f"{data.team2} goals per game: "
            f"{fs.team2_goals_scored_avg} scored, {fs.team2_goals_conceded_avg} conceded"
        )

        if fs.h2h_matches:
            lines.append(f"\nH2H last {len(fs.h2h_matches)} meetings:")
            t1w = t2w = draws = 0
            for h in fs.h2h_matches:
                lines.append(f"  {h.date} | {h.home_team} {h.score} {h.away_team}")
                try:
                    s = h.score.replace(" ", "")
                    parts = re.split(r'[-:]', s) if re.search(r'[-:]', s) else s.split("-")
                    if len(parts) == 2:
                        hg, ag = int(parts[0]), int(parts[1])
                        if hg > ag:
                            if data.team1.lower() in h.home_team.lower():
                                t1w += 1
                            else:
                                t2w += 1
                        elif hg < ag:
                            if data.team1.lower() in h.away_team.lower():
                                t1w += 1
                            else:
                                t2w += 1
                        else:
                            draws += 1
                except (ValueError, AttributeError):
                    pass
            lines.append(
                f"H2H summary: {data.team1} wins: {t1w}, Draws: {draws}, "
                f"{data.team2} wins: {t2w}"
            )
    else:
        err = (data.flashscore.error_message if data.flashscore else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE — {err}]")

    # ── Section 2: Squad & Injuries ────────────────────────────────────────
    lines.append("\n─── SECTION 2: SQUAD STATUS & INJURIES ───")
    if data.sofascore and data.sofascore.scrape_success:
        ss = data.sofascore
        for team_name, absences, lineup in [
            (data.team1, ss.team1_key_absences, ss.team1_predicted_lineup),
            (data.team2, ss.team2_key_absences, ss.team2_predicted_lineup),
        ]:
            lines.append(f"\n{team_name}:")
            if absences:
                lines.append(f"  Absences ({len(absences)}):")
                for p in absences:
                    detail = f" — {p.injury_detail}" if p.injury_detail else ""
                    lines.append(f"    ✗ {p.name} ({p.position}) [{p.status.value}{detail}]")
            else:
                lines.append("  No significant absences reported")
            if lineup:
                lines.append(f"  Last/predicted XI: {', '.join(lineup[:11])}")
    else:
        err = (data.sofascore.error_message if data.sofascore else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE — {err}]")

    # ── Section 3: Squad Values ────────────────────────────────────────────
    lines.append("\n─── SECTION 3: SQUAD MARKET VALUES ───")
    if data.transfermarkt and data.transfermarkt.scrape_success:
        tm = data.transfermarkt
        v1 = f"€{tm.team1_market_value_eur:.0f}m" if tm.team1_market_value_eur else "N/A"
        v2 = f"€{tm.team2_market_value_eur:.0f}m" if tm.team2_market_value_eur else "N/A"
        lines.append(f"{data.team1} squad value: {v1}")
        lines.append(f"{data.team2} squad value: {v2}")
        if tm.team1_market_value_eur and tm.team2_market_value_eur and tm.team2_market_value_eur > 0:
            ratio = tm.team1_market_value_eur / tm.team2_market_value_eur
            lines.append(f"Quality gap ratio ({data.team1}/{data.team2}): {ratio:.2f}x")
    else:
        err = (data.transfermarkt.error_message if data.transfermarkt else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE — {err}]")

    # ── Section 4: Bookmaker Odds ──────────────────────────────────────────
    lines.append("\n─── SECTION 4: BOOKMAKER ODDS ───")
    if data.odds and data.odds.scrape_success:
        o = data.odds
        lines.append(
            f"1X2: {data.team1}={o.team1_win_odds}, Draw={o.draw_odds}, {data.team2}={o.team2_win_odds}"
        )
        if o.implied_prob_w1:
            lines.append(
                f"Market implied (overround-adjusted): "
                f"{data.team1}={o.implied_prob_w1:.1%}, "
                f"Draw={o.implied_prob_draw:.1%}, "
                f"{data.team2}={o.implied_prob_w2:.1%}"
            )
        if o.over_2_5_odds:
            implied_ov = f" (implied {1/o.over_2_5_odds:.1%})"
            lines.append(f"Over 2.5 goals: {o.over_2_5_odds}{implied_ov}")
        if o.under_2_5_odds:
            implied_un = f" (implied {1/o.under_2_5_odds:.1%})"
            lines.append(f"Under 2.5 goals: {o.under_2_5_odds}{implied_un}")
        if o.asian_handicap:
            lines.append(f"Asian Handicap: {o.asian_handicap}")
        if o.btts_yes_odds:
            lines.append(f"BTTS Yes: {o.btts_yes_odds}, No: {o.btts_no_odds}")
    else:
        err = (data.odds.error_message if data.odds else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE — {err}]")

    # ── Section 5: Market signals ──────────────────────────────────────────
    lines.append("\n─── SECTION 5: MARKET SIGNALS ───")
    if data.market_signals and data.market_signals.scrape_success:
        ms = data.market_signals

        def _fmt_signal(signal, *, include_prob: bool = True) -> str:
            parts: list[str] = []
            if include_prob and signal.implied_prob is not None:
                parts.append(f"{signal.implied_prob:.1%}")
            if signal.avg_odds is not None:
                parts.append(f"avg {signal.avg_odds:.2f}")
            if signal.min_odds is not None and signal.max_odds is not None:
                parts.append(f"range {signal.min_odds:.2f}-{signal.max_odds:.2f}")
            if signal.movement_score:
                parts.append(f"move {signal.movement_score:+d}")
            if signal.bookmaker_count:
                parts.append(f"{signal.bookmaker_count} books")
            return ", ".join(parts)

        if ms.one_x_two:
            one_x_two_parts = []
            for label, display in [
                ("team1", data.team1),
                ("draw", "Draw"),
                ("team2", data.team2),
            ]:
                signal = ms.one_x_two.get(label)
                if signal:
                    one_x_two_parts.append(f"{display}: {_fmt_signal(signal)}")
            if one_x_two_parts:
                lines.append("1X2 consensus: " + " | ".join(one_x_two_parts))
        if ms.favourite_label and ms.favourite_gap_pct is not None:
            favourite_name = {
                "team1": data.team1,
                "draw": "Draw",
                "team2": data.team2,
            }.get(ms.favourite_label, ms.favourite_label)
            lines.append(
                f"Favourite by consensus: {favourite_name} "
                f"(gap vs next outcome {ms.favourite_gap_pct:.1%})"
            )

        if ms.over_under_2_5:
            over = ms.over_under_2_5.get("over")
            under = ms.over_under_2_5.get("under")
            ou_parts = []
            if over:
                ou_parts.append(f"Over 2.5: {_fmt_signal(over)}")
            if under:
                ou_parts.append(f"Under 2.5: {_fmt_signal(under)}")
            if ou_parts:
                lines.append("O/U 2.5 consensus: " + " | ".join(ou_parts))

        if ms.btts:
            yes = ms.btts.get("yes")
            no = ms.btts.get("no")
            btts_parts = []
            if yes:
                btts_parts.append(f"BTTS Yes: {_fmt_signal(yes)}")
            if no:
                btts_parts.append(f"BTTS No: {_fmt_signal(no)}")
            if btts_parts:
                lines.append("BTTS consensus: " + " | ".join(btts_parts))

        if ms.asian_handicap:
            ah_home = ms.asian_handicap.get("team1")
            ah_away = ms.asian_handicap.get("team2")
            ah_parts = []
            if ah_home:
                ah_parts.append(f"{data.team1}: {_fmt_signal(ah_home)}")
            if ah_away:
                ah_parts.append(f"{data.team2}: {_fmt_signal(ah_away)}")
            line_label = (
                f"{data.team1} {ms.asian_handicap_line:+g}"
                if ms.asian_handicap_line is not None
                else "most liquid line"
            )
            if ah_parts:
                lines.append(f"AH consensus on {line_label}: " + " | ".join(ah_parts))

        if ms.market_warnings:
            lines.append("Market warnings: " + "; ".join(ms.market_warnings))
    else:
        err = (data.market_signals.error_message if data.market_signals else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE — {err}]")

    # ── Section 6: Advanced team stats ─────────────────────────────────────
    lines.append("\n─── SECTION 6: ADVANCED TEAM STATS ───")
    if data.advanced_stats and data.advanced_stats.scrape_success:
        stats = data.advanced_stats
        for team_name, metrics in [
            (data.team1, stats.team1_metrics),
            (data.team2, stats.team2_metrics),
        ]:
            lines.append(f"\n{team_name}:")
            if metrics.xg is not None and metrics.xga is not None:
                lines.append(
                    f"  Season xG/xGA: {metrics.xg:.1f} / {metrics.xga:.1f}"
                    + (
                        f" (xG diff {metrics.xg_diff:+.1f})"
                        if metrics.xg_diff is not None
                        else ""
                    )
                )
            if metrics.rating is not None:
                lines.append(
                    f"  FotMob rating: {metrics.rating:.2f}"
                    + (
                        f" (league rank {metrics.rating_rank})"
                        if metrics.rating_rank is not None
                        else ""
                    )
                )
            if metrics.possession_pct is not None:
                lines.append(
                    f"  Possession: {metrics.possession_pct:.1f}%"
                    + (
                        f" (rank {metrics.possession_rank})"
                        if metrics.possession_rank is not None
                        else ""
                    )
                )
            if metrics.shots_on_target_per_match is not None:
                lines.append(
                    f"  Shots on target per match: {metrics.shots_on_target_per_match:.1f}"
                )
            extras: list[str] = []
            if metrics.big_chances is not None:
                extras.append(f"big chances {metrics.big_chances:g}")
            if metrics.touches_in_opp_box is not None:
                extras.append(f"opp-box touches {metrics.touches_in_opp_box:g}")
            if metrics.corners is not None:
                extras.append(f"corners {metrics.corners:g}")
            if metrics.clean_sheets is not None:
                extras.append(f"clean sheets {metrics.clean_sheets:g}")
            if extras:
                lines.append(f"  Supporting volume: {', '.join(extras)}")
    else:
        err = (data.advanced_stats.error_message if data.advanced_stats else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE — {err}]")

    # ── Section 7: Standings context ───────────────────────────────────────
    lines.append("\n─── SECTION 7: STANDINGS CONTEXT ───")
    if data.standings_context and data.standings_context.scrape_success:
        standings = data.standings_context
        if standings.league_name:
            lines.append(f"League table: {standings.league_name}")

        def _fmt_table_summary(label: str, summary) -> str:
            parts = []
            if summary.position is not None:
                parts.append(f"#{summary.position}")
            if summary.points is not None:
                parts.append(f"{summary.points} pts")
            if summary.played is not None:
                parts.append(f"{summary.played} played")
            if summary.goal_difference is not None:
                parts.append(f"GD {summary.goal_difference:+d}")
            if summary.scores_str:
                parts.append(f"goals {summary.scores_str}")
            if summary.qualification_label:
                parts.append(summary.qualification_label)
            return f"{label}: " + ", ".join(parts)

        lines.append(_fmt_table_summary(data.team1, standings.team1_overall))
        lines.append(_fmt_table_summary(data.team2, standings.team2_overall))

        if standings.point_gap is not None or standings.position_gap is not None:
            gap_parts = []
            if standings.point_gap is not None:
                gap_parts.append(f"point gap {standings.point_gap:+d}")
            if standings.position_gap is not None:
                gap_parts.append(f"position gap {standings.position_gap:+d}")
            if gap_parts:
                lines.append("Head-to-head in table: " + ", ".join(gap_parts))

        venue_parts = []
        if standings.team1_home.position is not None:
            venue_parts.append(
                f"{data.team1} home rank #{standings.team1_home.position}"
                + (
                    f" ({standings.team1_home.points} pts)"
                    if standings.team1_home.points is not None
                    else ""
                )
            )
        if standings.team2_away.position is not None:
            venue_parts.append(
                f"{data.team2} away rank #{standings.team2_away.position}"
                + (
                    f" ({standings.team2_away.points} pts)"
                    if standings.team2_away.points is not None
                    else ""
                )
            )
        if venue_parts:
            lines.append("Venue split: " + " | ".join(venue_parts))
    else:
        err = (data.standings_context.error_message if data.standings_context else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE — {err}]")

    # ── Section 8: Team memory (cached from previous scrapes) ──────────────
    lines.append("\nв”Ђв”Ђв”Ђ SECTION 8: SCHEDULE / LOAD SIGNALS в”Ђв”Ђв”Ђ")
    if data.schedule_load and data.schedule_load.scrape_success:
        schedule = data.schedule_load

        def _fmt_schedule_summary(team_name: str, summary) -> None:
            lines.append(f"\n{team_name}:")
            if summary.rest_hours is not None:
                lines.append(f"  Rest since previous match: {summary.rest_hours:.1f}h")
            lines.append(f"  Matches in last 7d: {summary.matches_last_7d}")
            lines.append(f"  Matches in next 7d: {summary.matches_next_7d}")
            if summary.last_opponent or summary.last_match_utc:
                previous_bits = [
                    part for part in [summary.last_opponent, summary.last_match_utc] if part
                ]
                lines.append("  Previous fixture: " + " | ".join(previous_bits))
            if summary.next_opponent or summary.next_match_utc:
                next_bits = [
                    part for part in [summary.next_opponent, summary.next_match_utc] if part
                ]
                lines.append("  Next fixture: " + " | ".join(next_bits))
            if summary.short_rest is not None:
                lines.append(f"  Short rest flag: {'yes' if summary.short_rest else 'no'}")
            if summary.next_short_turnaround is not None:
                lines.append(
                    "  Quick next-fixture turnaround: "
                    + ("yes" if summary.next_short_turnaround else "no")
                )

        _fmt_schedule_summary(data.team1, schedule.team1)
        _fmt_schedule_summary(data.team2, schedule.team2)
        if schedule.rest_gap_hours is not None:
            lines.append(
                f"Rest gap ({data.team1} minus {data.team2}): {schedule.rest_gap_hours:+.1f}h"
            )
        if schedule.load_warnings:
            lines.append("Load warnings: " + "; ".join(schedule.load_warnings))
    else:
        err = (data.schedule_load.error_message if data.schedule_load else "Scraper failed")
        lines.append(f"\n[NOT AVAILABLE вЂ” {err}]")

    lines.append("\n─── SECTION 9: WEATHER / MOTIVATION / SQUAD FILTERS ───")
    if data.weather_context and data.weather_context.scrape_success:
        weather = data.weather_context
        location_bits = [
            part for part in [weather.venue_name, weather.city_name, weather.country_name] if part
        ]
        if location_bits:
            lines.append("Venue weather: " + " | ".join(location_bits))

        weather_bits: list[str] = []
        if weather.kickoff_utc:
            weather_bits.append(f"kickoff {weather.kickoff_utc}")
        if weather.forecast_time_utc:
            weather_bits.append(f"forecast hour {weather.forecast_time_utc}")
        if weather.weather_label:
            weather_bits.append(weather.weather_label)
        if weather.temperature_c is not None:
            weather_bits.append(f"temp {weather.temperature_c:.1f}C")
        if weather.apparent_temperature_c is not None:
            weather_bits.append(f"feels like {weather.apparent_temperature_c:.1f}C")
        if weather.wind_speed_kmh is not None:
            weather_bits.append(f"wind {weather.wind_speed_kmh:.1f} km/h")
        if weather.wind_gusts_kmh is not None:
            weather_bits.append(f"gusts {weather.wind_gusts_kmh:.1f} km/h")
        if weather.precipitation_mm is not None:
            weather_bits.append(f"precip {weather.precipitation_mm:.1f} mm")
        if weather.precipitation_probability_pct is not None:
            weather_bits.append(f"rain risk {weather.precipitation_probability_pct:.0f}%")
        if weather_bits:
            lines.append("Weather snapshot: " + " | ".join(weather_bits))
        if weather.weather_warnings:
            lines.append("Weather warnings: " + "; ".join(weather.weather_warnings))
    else:
        err = (data.weather_context.error_message if data.weather_context else "Scraper failed")
        lines.append(f"[NOT AVAILABLE — {err}]")

    # ── Referee profile ────────────────────────────────────────────────────
    if data.match_context and data.match_context.scrape_success and data.match_context.referee_name:
        try:
            from referee import get_referee_profile
            rp = get_referee_profile(data.match_context.referee_name)
            if rp and rp.matches >= 15:
                lines.append(f"\nReferee: {rp.name} ({rp.matches} matches profiled)")
                lines.append(f"  Avg goals: {rp.avg_goals:.2f} (vs league avg: {rp.goals_vs_avg:+.2f})")
                lines.append(f"  Over 2.5: {rp.over_2_5_pct:.0f}%  |  BTTS: {rp.btts_pct:.0f}%")
                lines.append(f"  Avg yellows: {rp.avg_yellows:.1f} (vs avg: {rp.yellows_vs_avg:+.1f})  |  Avg fouls: {rp.avg_fouls:.0f}")
                if rp.goals_vs_avg >= 0.15:
                    lines.append(f"  ⚠ PERMISSIVE referee — expect higher-scoring game")
                elif rp.goals_vs_avg <= -0.15:
                    lines.append(f"  ⚠ STRICT referee — expect lower-scoring game")
            elif data.match_context.referee_name:
                lines.append(f"\nReferee: {data.match_context.referee_name} (insufficient data for profile)")
        except Exception:
            pass

    motivation_notes = _build_motivation_filter_notes(data)
    if motivation_notes:
        lines.append("Motivation filters: " + "; ".join(motivation_notes))
    else:
        lines.append("Motivation filters: no acute situational flags")

    squad_notes = _build_squad_filter_notes(data)
    if squad_notes:
        lines.append("Squad filters: " + "; ".join(squad_notes))
    else:
        lines.append("Squad filters: no acute lineup availability flags")

    if team_memory_block:
        lines.append(team_memory_block)

    # ── Section 10: Numeric model baseline ─────────────────────────────────
    if numeric_baseline_block:
        lines.append("\n" + numeric_baseline_block)

    # ── Section 11: Calibration history ────────────────────────────────────
    if calibration_block:
        lines.append(calibration_block)

    # ── Section 12: X.com Social Intelligence ────────────────────────────
    if data.x_social and data.x_social.scrape_success and data.x_social.signals:
        lines.append("\n--- SECTION 12: X.COM SOCIAL INTELLIGENCE ---")
        if data.x_social.key_narrative:
            lines.append(f"Dominant narrative: {data.x_social.key_narrative}")
        if data.x_social.sentiment_team1:
            lines.append(f"{data.team1} fan sentiment: {data.x_social.sentiment_team1}")
        if data.x_social.sentiment_team2:
            lines.append(f"{data.team2} fan sentiment: {data.x_social.sentiment_team2}")
        for sig in data.x_social.signals:
            conf = f"[{sig.confidence}]" if sig.confidence else ""
            src = f" (src: {sig.source_hint})" if sig.source_hint else ""
            age = f" -- {sig.recency}" if sig.recency else ""
            lines.append(f"  - [{sig.type}] {conf} {sig.content}{src}{age}")

    return "\n".join(lines)


def _source_quality_counts(data: MatchData) -> dict[str, int]:
    return {
        "raw_ok": data.successful_source_count,
        "raw_total": data.total_source_count,
        "raw_pct": int(data.data_completeness * 100),
        "applicable_ok": data.successful_applicable_source_count,
        "applicable_total": data.applicable_source_count,
        "applicable_pct": int(data.applicable_data_completeness * 100),
        "unsupported_count": data.unsupported_source_count,
    }


# ── Elo helper ────────────────────────────────────────────────────────────────

async def _elo_block(data: MatchData) -> str:
    """Build Elo rating context block for Claude prompt."""
    try:
        from elo import get_elo_system, expected_score, HOME_ELO_BONUS
        elo = await get_elo_system()
        if not elo.initialized:
            return ""

        r1, r2 = elo.get_ratings(data.team1, data.team2)
        if r1 == 1500.0 and r2 == 1500.0:
            return ""  # Both default — no useful info

        # Determine home/away from match context
        is_home = True
        neutral = False
        if data.match_context and data.match_context.scrape_success:
            if data.match_context.is_neutral_venue:
                neutral = True
            elif data.match_context.is_team1_home is False:
                is_home = False

        p_w1, p_draw, p_w2 = elo.win_probability(
            data.team1, data.team2, is_home=is_home, neutral=neutral,
        )

        lines = ["─── ELO RATINGS ───"]
        lines.append(f"{data.team1}: {r1:.0f} Elo | {data.team2}: {r2:.0f} Elo")
        diff = r1 - r2
        lines.append(f"Elo difference: {diff:+.0f} ({data.team1} perspective)")
        venue = "neutral" if neutral else ("home" if is_home else "away")
        lines.append(f"Elo-based 1X2: W1={p_w1:.1%} / D={p_draw:.1%} / W2={p_w2:.1%} ({venue})")
        lines.append(
            "NOTE: Use Elo as a prior for team quality. Combine with form, "
            "injuries, and market data for final probabilities."
        )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("Elo block failed (non-critical): %s", exc)
        return ""


def _format_numeric_baseline_block(baseline: dict[str, float]) -> str:
    """Format the numeric model baseline as a prompt section for agents."""
    lines = [
        "─── NUMERIC MODEL BASELINE (Poisson / Dixon-Coles / Ensemble) ───",
        "The following probabilities come from the statistical model.",
        "Use them as an ANCHOR — start from these numbers and adjust based",
        "on qualitative factors (form momentum, injuries, motivation, H2H psychology).",
        "If you deviate significantly (>10pp), explain WHY in your reasoning.",
        "",
        "1X2 Probabilities:",
        f"  W1:   {baseline.get('prob_w1', 0):.1f}%",
        f"  Draw: {baseline.get('prob_draw', 0):.1f}%",
        f"  W2:   {baseline.get('prob_w2', 0):.1f}%",
        "",
        "Goals Market:",
        f"  Expected goals: {baseline.get('expected_goals', 0):.2f}",
        f"  Over 2.5:  {baseline.get('prob_over_2_5', 0):.1f}%",
        f"  Under 2.5: {baseline.get('prob_under_2_5', 0):.1f}%",
        "",
        "Model governance:",
        "  - Treat this block as the primary quantitative prior for the match.",
        "  - If you move more than about 8 percentage points on a major market, justify it explicitly.",
        "  - If you move more than about 0.35 expected goals, justify it explicitly.",
    ]
    if baseline.get("prob_over_1_5") is not None:
        lines.append(f"  Over 1.5:  {baseline['prob_over_1_5']:.1f}%")
    if baseline.get("prob_over_3_5") is not None:
        lines.append(f"  Over 3.5:  {baseline['prob_over_3_5']:.1f}%")
    lines += [
        "",
        "BTTS:",
        f"  Yes: {baseline.get('prob_btts_yes', 0):.1f}%",
        f"  No:  {baseline.get('prob_btts_no', 0):.1f}%",
        "",
        "You may adjust these numbers up or down based on data the model cannot see",
        "(e.g., tactical context, motivation, manager changes, recent form streaks).",
        "Do NOT blindly copy them — apply your analysis.",
    ]
    return "\n".join(lines)


# ── Calibration + team-memory helpers ─────────────────────────────────────────

async def _calibration_block() -> str:
    """
    Build a text block describing the bot's historical prediction accuracy.
    Injected into the user prompt so Claude can self-correct known biases.
    """
    try:
        stats = await db.get_calibration_stats()
    except Exception:
        return ""

    if not stats or stats.get("n_with_result", 0) < 5:
        return ""  # not enough history yet

    n = stats["n_with_result"]
    acc = stats.get("acc_1x2", 0.0)
    brier = stats.get("brier_1x2")
    lines = ["\n─── CALIBRATION HISTORY (SELF-LEARNING) ───"]
    lines.append(f"Based on {n} previous predictions with known results:")
    lines.append(f"  1X2 accuracy: {acc:.1f}% (market baseline ~45-50%)")

    if brier is not None:
        lines.append(f"  Brier score 1X2: {brier:.4f} (0.0=perfect, 0.333=random)")
        if brier > 0.25:
            lines.append(
                "  → BIAS: 1X2 probabilities too extreme. Pull toward 50% in uncertain matches."
            )
        elif brier < 0.18:
            lines.append("  → 1X2 calibration GOOD. Maintain current confidence levels.")

    brier_ou = stats.get("brier_ou")
    if brier_ou is not None:
        lines.append(f"  Brier score O/U 2.5: {brier_ou:.4f} (0.0=perfect, 0.25=random for binary)")
        if brier_ou > 0.20:
            lines.append(
                "  → BIAS: O/U 2.5 probabilities overconfident. Adjust over/under closer to 50%."
            )
        elif brier_ou < 0.12:
            lines.append("  → O/U 2.5 calibration GOOD.")

    brier_btts = stats.get("brier_btts")
    if brier_btts is not None:
        lines.append(f"  Brier score BTTS: {brier_btts:.4f} (0.0=perfect, 0.25=random for binary)")
        if brier_btts > 0.20:
            lines.append(
                "  → BIAS: BTTS probabilities overconfident. Adjust BTTS closer to 50%."
            )
        elif brier_btts < 0.12:
            lines.append("  → BTTS calibration GOOD.")

    lines.append(
        "INSTRUCTION: Use this calibration data to recalibrate your probability outputs. "
        "If a market shows BIAS, explicitly adjust that market's probabilities before outputting JSON."
    )
    return "\n".join(lines)


async def _team_memory_block(team1: str, team2: str) -> str:
    """
    Load cached team snapshots from previous scrapes.
    Shown as supplementary context (especially useful when a scraper failed today).
    """
    try:
        snap1, snap2 = await asyncio.gather(
            db.get_team_snapshot(team1),
            db.get_team_snapshot(team2),
        )
    except Exception:
        return ""

    if not snap1 and not snap2:
        return ""

    lines = ["\n─── CACHED TEAM DATA (from previous scrapes) ───"]

    def _fmt_snapshot(team: str, snap: dict) -> list[str]:
        if not snap:
            return []
        out = [f"{team}:"]
        for key, (value, saved_at) in sorted(snap.items()):
            try:
                saved_dt = datetime.fromisoformat(saved_at)
                if saved_dt.tzinfo is None:
                    saved_dt = saved_dt.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - saved_dt).total_seconds() / 3600
                age_str = (
                    f"{int(age_h)}h ago" if age_h < 48
                    else f"{int(age_h / 24)}d ago"
                )
            except (ValueError, TypeError):
                age_str = "?"
            out.append(f"  [{age_str}] {key}: {value}")
        return out

    lines += _fmt_snapshot(team1, snap1)
    lines += _fmt_snapshot(team2, snap2)
    lines.append(
        "(Use cached data as supplementary context, especially when current scrape failed)"
    )
    return "\n".join(lines)


async def _save_team_memory(data: MatchData) -> None:
    """Persist key scraped facts to team_memory for future prompts."""
    try:
        tasks = []
        if data.flashscore and data.flashscore.scrape_success:
            fs = data.flashscore
            if fs.team1_form:
                form_str = "".join(m.result.value for m in fs.team1_form[:5])
                tasks.append(db.save_team_snapshot(data.team1, "last5_form", form_str))
            if fs.team1_goals_scored_avg is not None:
                tasks.append(db.save_team_snapshot(
                    data.team1, "goals_avg",
                    f"{fs.team1_goals_scored_avg} scored / {fs.team1_goals_conceded_avg} conceded"
                ))
            if fs.team2_form:
                form_str = "".join(m.result.value for m in fs.team2_form[:5])
                tasks.append(db.save_team_snapshot(data.team2, "last5_form", form_str))
            if fs.team2_goals_scored_avg is not None:
                tasks.append(db.save_team_snapshot(
                    data.team2, "goals_avg",
                    f"{fs.team2_goals_scored_avg} scored / {fs.team2_goals_conceded_avg} conceded"
                ))

        if data.sofascore and data.sofascore.scrape_success:
            ss = data.sofascore
            for team_name, absences in [
                (data.team1, ss.team1_key_absences),
                (data.team2, ss.team2_key_absences),
            ]:
                if absences:
                    absence_str = ", ".join(
                        f"{p.name} ({p.position}, {p.status.value})"
                        for p in absences[:5]
                    )
                    tasks.append(db.save_team_snapshot(team_name, "key_absences", absence_str))
                else:
                    tasks.append(db.save_team_snapshot(team_name, "key_absences", "none reported"))

        if data.transfermarkt and data.transfermarkt.scrape_success:
            tm = data.transfermarkt
            if tm.team1_market_value_eur:
                tasks.append(db.save_team_snapshot(
                    data.team1, "squad_value", f"€{tm.team1_market_value_eur:.0f}m"
                ))
            if tm.team2_market_value_eur:
                tasks.append(db.save_team_snapshot(
                    data.team2, "squad_value", f"€{tm.team2_market_value_eur:.0f}m"
                ))

        if data.advanced_stats and data.advanced_stats.scrape_success:
            stats = data.advanced_stats
            for team_name, metrics in [
                (data.team1, stats.team1_metrics),
                (data.team2, stats.team2_metrics),
            ]:
                if metrics.xg is not None or metrics.xga is not None or metrics.xg_diff is not None:
                    profile_parts = []
                    if metrics.xg is not None:
                        profile_parts.append(f"xG {metrics.xg:.1f}")
                    if metrics.xga is not None:
                        profile_parts.append(f"xGA {metrics.xga:.1f}")
                    if metrics.xg_diff is not None:
                        profile_parts.append(f"xGD {metrics.xg_diff:+.1f}")
                    tasks.append(db.save_team_snapshot(
                        team_name,
                        "xg_profile",
                        " / ".join(profile_parts),
                    ))
                if metrics.possession_pct is not None:
                    tasks.append(db.save_team_snapshot(
                        team_name,
                        "possession_profile",
                        f"{metrics.possession_pct:.1f}% possession",
                    ))

        if data.standings_context and data.standings_context.scrape_success:
            standings = data.standings_context
            for team_name, summary in [
                (data.team1, standings.team1_overall),
                (data.team2, standings.team2_overall),
            ]:
                if summary.position is not None and summary.points is not None:
                    position_text = f"#{summary.position}, {summary.points} pts"
                    if summary.goal_difference is not None:
                        position_text += f", GD {summary.goal_difference:+d}"
                    tasks.append(db.save_team_snapshot(
                        team_name,
                        "table_position",
                        position_text,
                    ))

        if data.schedule_load and data.schedule_load.scrape_success:
            schedule = data.schedule_load
            for team_name, summary in [
                (data.team1, schedule.team1),
                (data.team2, schedule.team2),
            ]:
                snapshot_bits = [
                    f"last7d {summary.matches_last_7d}",
                    f"next7d {summary.matches_next_7d}",
                ]
                if summary.rest_hours is not None:
                    snapshot_bits.insert(0, f"rest {summary.rest_hours:.1f}h")
                if summary.short_rest:
                    snapshot_bits.append("short-rest")
                if summary.next_short_turnaround:
                    snapshot_bits.append("quick-next")
                tasks.append(db.save_team_snapshot(
                    team_name,
                    "schedule_load",
                    ", ".join(snapshot_bits),
                ))

        if tasks:
            await asyncio.gather(*tasks)
            logger.debug(
                "Saved %d team memory entries for %s / %s",
                len(tasks), data.team1, data.team2,
            )
    except Exception as exc:
        logger.warning("Team memory save failed (non-critical): %s", exc)


# ── JSON extraction helper ─────────────────────────────────────────────────────

def _extract_json(raw: str) -> str:
    """Strip markdown fences and extract the first *balanced* JSON object.

    Chat-UI workers (OmniRoute) sometimes append prose after the JSON or wrap it
    in fences. Greedy first-brace-to-last-brace over-captures trailing prose that
    contains a ``}``; a brace-balanced scan (string/escape aware) returns the
    first complete object instead. If the object is truncated (no closing brace),
    return from the first brace to the end so a downstream repair pass can salvage it.
    """
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    if start == -1:
        return raw.strip()
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return raw[start:]


def _loads_agent_json(raw: str, *, context: str = "") -> dict:
    """Parse an agent's JSON response tolerantly.

    Strict ``json.loads`` first; on failure fall back to ``json_repair`` to recover
    the common chat-UI breakages (missing/extra commas, unescaped quotes, trailing
    prose, truncation). Logs a warning when a repair was needed so worker JSON
    quality can be monitored. Downstream pydantic models still validate the result.
    """
    extracted = _extract_json(raw)
    try:
        return json.loads(extracted)
    except json.JSONDecodeError as strict_exc:
        try:
            from json_repair import repair_json
        except ImportError:
            raise strict_exc
        for candidate in (extracted, raw):
            try:
                repaired = repair_json(candidate, return_objects=True)
            except Exception:
                continue
            if isinstance(repaired, dict) and repaired:
                logger.warning(
                    "Recovered malformed agent JSON via repair%s: %s",
                    f" ({context})" if context else "",
                    strict_exc,
                )
                return repaired
        raise strict_exc


def _recalibration_competition_name(data: MatchData) -> Optional[str]:
    if data.match_context and data.match_context.competition_name:
        return data.match_context.competition_name
    if data.flashscore and data.flashscore.league_name:
        return data.flashscore.league_name
    if data.standings_context and data.standings_context.league_name:
        return data.standings_context.league_name
    return None


def _pre_ai_market_signal_count(snapshot) -> int:
    return sum(
        value is not None
        for value in (
            snapshot.market_prob_w1,
            snapshot.market_prob_draw,
            snapshot.market_prob_w2,
            snapshot.market_prob_over_2_5,
            snapshot.market_prob_under_2_5,
            snapshot.market_prob_btts_yes,
            snapshot.market_prob_btts_no,
        )
    )


def _policy_path_flag(prompt_version: str) -> Optional[str]:
    if prompt_version == PRE_AI_GATE_PROMPT_VERSION:
        return "path_pre_ai_gate"
    if prompt_version == AGENT_CHAIN_PROMPT_VERSION:
        return "path_agent_chain"
    if prompt_version == SINGLE_CALL_PROMPT_VERSION:
        return "path_single_call"
    return None


def _is_core_market_family(family: str) -> bool:
    return str(family or "").strip().lower() in {"1x2", "totals", "btts"}


def _match_top_pick_candidate(report_dict: dict, candidates: list[dict[str, object]]) -> Optional[dict[str, object]]:
    top_market_code = str(report_dict.get("top_market_code") or "").strip().upper()
    top_selection_code = str(report_dict.get("top_selection_code") or "").strip().upper()
    if top_market_code and top_selection_code:
        for candidate in candidates:
            if (
                str(candidate.get("market_code") or "").strip().upper() == top_market_code
                and str(candidate.get("selection_code") or "").strip().upper() == top_selection_code
            ):
                return candidate

    top_label = " ".join(str(report_dict.get("top_recommendation") or "").strip().lower().split())
    if not top_label:
        return None
    for candidate in candidates:
        candidate_label = " ".join(str(candidate.get("label") or "").strip().lower().split())
        if candidate_label == top_label:
            return candidate
    return None


def _build_policy_flags(
    report_dict: dict,
    feature_snapshot,
    odds_data,
    family_quality_profiles: Optional[list[dict]],
    *,
    prompt_version: str,
) -> list[str]:
    flags: list[str] = []

    path_flag = _policy_path_flag(prompt_version)
    if path_flag:
        flags.append(path_flag)

    warnings = [str(item) for item in (report_dict.get("data_warnings") or [])]
    if str(report_dict.get("top_market_code") or "").strip().upper() == "NO_BET":
        flags.append("no_bet")
    if any("Action mode pick" in warning for warning in warnings):
        flags.append("action_mode_pick")
    if any("Top pick normalized to stronger core market" in warning for warning in warnings):
        flags.append("top_pick_normalized_core")

    candidates = build_market_candidates(
        report_dict,
        feature_snapshot,
        odds_data,
        family_quality_profiles,
    )
    top_candidate = _match_top_pick_candidate(report_dict, candidates)
    if top_candidate is not None:
        family = str(top_candidate.get("family") or "").strip().lower()
        if _is_core_market_family(family):
            flags.append("top_market_core")
        else:
            flags.append("top_market_derived")

        trust_score = float(top_candidate.get("family_trust_score") or 0.0)
        family_sample_size = int(top_candidate.get("family_sample_size") or 0)
        if family_sample_size >= 250:
            if trust_score > 1.0:
                flags.append("top_family_positive_trust")
            elif trust_score < -1.0:
                flags.append("top_family_negative_trust")

        if float(top_candidate.get("family_borderline_rerank_bonus") or 0.0) > 0.0:
            flags.append("btts_borderline_rerank")

    if prompt_version == PRE_AI_GATE_PROMPT_VERSION and "no_bet" not in flags:
        flags.append("pre_ai_gate_playable_seed")

    deduped: list[str] = []
    for flag in flags:
        if flag and flag not in deduped:
            deduped.append(flag)
    return deduped


def _build_pre_ai_seed_report_dict(
    baseline: dict[str, float],
    best_candidate: Optional[dict[str, object]],
) -> dict:
    top_recommendation = "No bet - pre-AI gate"
    top_odds = None
    top_confidence = 48.0
    if best_candidate is not None:
        top_recommendation = str(best_candidate.get("label") or top_recommendation)
        if best_candidate.get("odds") is not None:
            top_odds = float(best_candidate["odds"])
        if best_candidate.get("confidence_pct") is not None:
            top_confidence = float(best_candidate["confidence_pct"])

    return {
        "prob_w1": baseline["prob_w1"],
        "prob_draw": baseline["prob_draw"],
        "prob_w2": baseline["prob_w2"],
        "result_confidence": "LOW",
        "prob_over_2_5": baseline["prob_over_2_5"],
        "prob_under_2_5": baseline["prob_under_2_5"],
        "expected_goals": baseline["expected_goals"],
        "goals_confidence": "LOW",
        "prob_over_1_5": baseline.get("prob_over_1_5"),
        "prob_under_1_5": baseline.get("prob_under_1_5"),
        "prob_over_3_5": baseline.get("prob_over_3_5"),
        "prob_under_3_5": baseline.get("prob_under_3_5"),
        "asian_handicap_rec": "Level (0)",
        "asian_handicap_confidence": "LOW",
        "prob_btts_yes": baseline["prob_btts_yes"],
        "prob_btts_no": baseline["prob_btts_no"],
        "btts_confidence": "LOW",
        "team1_strengths": [],
        "team1_weaknesses": [],
        "team2_strengths": [],
        "team2_weaknesses": [],
        "top_recommendation": top_recommendation,
        "top_recommendation_odds": top_odds,
        "top_confidence_pct": top_confidence,
        "risk_level": "HIGH",
        "analysis_summary": (
            "Numeric pre-check skipped the expensive AI path because the current match "
            "looks close to market consensus and the available data is too thin for a "
            "meaningful LLM edge."
        ),
        "data_warnings": [],
    }


async def _get_live_hybrid_1x2_runtime() -> _LiveHybrid1X2Runtime:
    if not config.LIVE_1X2_HYBRID_ENABLED:
        return _LiveHybrid1X2Runtime()

    ttl_seconds = max(0, int(config.LIVE_1X2_HYBRID_CACHE_TTL_SECONDS))
    now = monotonic()
    cached = _LIVE_HYBRID_1X2_RUNTIME
    if cached.loaded_at_monotonic and (ttl_seconds <= 0 or (now - cached.loaded_at_monotonic) < ttl_seconds):
        return cached

    async with _get_live_hybrid_lock():
        refreshed = _LIVE_HYBRID_1X2_RUNTIME
        if refreshed.loaded_at_monotonic and (ttl_seconds <= 0 or (monotonic() - refreshed.loaded_at_monotonic) < ttl_seconds):
            return refreshed

        rows = await db.get_historical_backtest_rows(limit=max(1, int(config.LIVE_1X2_HYBRID_TRAIN_LIMIT)))
        model = fit_trainable_numeric_model(rows, min_rows=max(12, int(config.LIVE_1X2_HYBRID_MIN_ROWS)))

        alpha = 0.0
        trained_rows = int(getattr(model, "trained_rows", 0) or 0)
        if model is not None and getattr(model, "one_x_two_weights", None) is not None:
            alpha = round(max(0.0, min(0.40, float(config.LIVE_1X2_HYBRID_ALPHA))), 2)

        runtime = _LiveHybrid1X2Runtime(
            model=model,
            alpha=alpha,
            trained_rows=trained_rows,
            loaded_at_monotonic=monotonic(),
        )
        globals()["_LIVE_HYBRID_1X2_RUNTIME"] = runtime
        return runtime


def _blend_live_hybrid_1x2_triplet(
    recalibrated_probs: dict[str, float],
    trainable_probs: dict[str, float],
    alpha: float,
) -> dict[str, float]:
    bounded_alpha = max(0.0, min(1.0, float(alpha)))
    if bounded_alpha <= 0.0:
        return {
            "prob_w1": float(recalibrated_probs["prob_w1"]),
            "prob_draw": float(recalibrated_probs["prob_draw"]),
            "prob_w2": float(recalibrated_probs["prob_w2"]),
        }

    blended = [
        ((1.0 - bounded_alpha) * float(recalibrated_probs["prob_w1"])) + (bounded_alpha * float(trainable_probs["prob_w1"])),
        ((1.0 - bounded_alpha) * float(recalibrated_probs["prob_draw"])) + (bounded_alpha * float(trainable_probs["prob_draw"])),
        ((1.0 - bounded_alpha) * float(recalibrated_probs["prob_w2"])) + (bounded_alpha * float(trainable_probs["prob_w2"])),
    ]
    total = sum(blended)
    if total <= 0.0:
        return {
            "prob_w1": float(recalibrated_probs["prob_w1"]),
            "prob_draw": float(recalibrated_probs["prob_draw"]),
            "prob_w2": float(recalibrated_probs["prob_w2"]),
        }

    prob_w1 = round((blended[0] * 100.0) / total, 1)
    prob_draw = round((blended[1] * 100.0) / total, 1)
    prob_w2 = round((blended[2] * 100.0) / total, 1)
    triplet_total = prob_w1 + prob_draw + prob_w2
    if triplet_total != 100.0:
        prob_w1 = round(prob_w1 + (100.0 - triplet_total), 1)
    return {
        "prob_w1": prob_w1,
        "prob_draw": prob_draw,
        "prob_w2": prob_w2,
    }


async def _enrich_snapshot_with_elo(snapshot, team1: str, team2: str) -> None:
    """Populate Elo fields on snapshot from the global EloSystem (non-blocking)."""
    try:
        from elo import get_elo_system
        elo = await get_elo_system()
        r1 = elo.get_rating(team1)
        r2 = elo.get_rating(team2)
        snapshot.elo_team1 = r1.rating
        snapshot.elo_team2 = r2.rating

        is_home = snapshot.is_team1_home is not False
        neutral = snapshot.is_neutral_venue is True

        pw1, pd, pw2 = elo.win_probability(team1, team2, is_home=is_home, neutral=neutral)
        snapshot.elo_prob_w1 = round(pw1 * 100.0, 1)
        snapshot.elo_prob_draw = round(pd * 100.0, 1)
        snapshot.elo_prob_w2 = round(pw2 * 100.0, 1)

        snapshot.elo_xg_adjustment = elo.elo_xg_adjustment(
            team1, team2, is_home=is_home, neutral=neutral,
        )

        # Home/away split ratings for per-team lambda
        if not neutral and is_home:
            hr, ar = elo.get_home_away_ratings(team1, team2)
            snapshot.elo_home_rating = hr
            snapshot.elo_away_rating = ar
        elif not neutral:
            hr, ar = elo.get_home_away_ratings(team2, team1)
            snapshot.elo_home_rating = hr
            snapshot.elo_away_rating = ar
    except Exception as exc:
        logger.debug("Elo enrichment failed (non-critical): %s", exc)


async def _build_live_numeric_baseline(
    data: MatchData,
    snapshot,
    recalibration_profiles: Optional[list[dict]] = None,
) -> dict[str, float]:
    # Enrich snapshot with Elo data before building baseline
    if snapshot.elo_team1 is None:
        await _enrich_snapshot_with_elo(snapshot, data.team1, data.team2)

    baseline = build_numeric_baseline(
        data,
        snapshot,
        recalibration_profiles=recalibration_profiles,
    )

    # ── LightGBM Ensemble blend ───────────────────────────────────────
    if config.ENSEMBLE_ENABLED:
        try:
            from ensemble import predict_ensemble
            ens = predict_ensemble(snapshot, baseline)
            if ens is not None:
                baseline["prob_over_2_5"] = ens["prob_over_2_5"]
                baseline["prob_under_2_5"] = ens["prob_under_2_5"]
                baseline["prob_w1"] = ens["prob_w1"]
                baseline["prob_draw"] = ens["prob_draw"]
                baseline["prob_w2"] = ens["prob_w2"]
                _record_analysis_metric("ensemble_blend_applied")
                logger.debug("Ensemble blend applied: O/U %.1f/%.1f, 1X2 %.1f/%.1f/%.1f",
                             ens["prob_over_2_5"], ens["prob_under_2_5"],
                             ens["prob_w1"], ens["prob_draw"], ens["prob_w2"])
        except Exception as exc:
            logger.debug("Ensemble blend skipped: %s", exc)

    if not config.LIVE_1X2_HYBRID_ENABLED:
        return baseline
    if snapshot.data_completeness < float(config.LIVE_1X2_HYBRID_MIN_COMPLETENESS):
        return baseline
    if None in (snapshot.market_prob_w1, snapshot.market_prob_draw, snapshot.market_prob_w2):
        return baseline

    runtime = await _get_live_hybrid_1x2_runtime()
    if runtime.model is None or runtime.alpha <= 0.0:
        return baseline

    trainable_probs = predict_trainable_numeric_model(runtime.model, snapshot)
    if trainable_probs is None:
        return baseline

    blended_triplet = _blend_live_hybrid_1x2_triplet(baseline, trainable_probs, runtime.alpha)
    baseline["prob_w1"] = blended_triplet["prob_w1"]
    baseline["prob_draw"] = blended_triplet["prob_draw"]
    baseline["prob_w2"] = blended_triplet["prob_w2"]
    _record_analysis_metric("live_hybrid_1x2_applied")
    return baseline


async def _pre_ai_gate_report_or_none(
    data: MatchData,
    warnings: list[str],
) -> Optional[PredictionReport]:
    if not config.PRE_AI_GATE_ENABLED:
        return None
    if not (data.odds and data.odds.scrape_success):
        return None
    if data.applicable_data_completeness > config.PRE_AI_GATE_MAX_APPLICABLE_COMPLETENESS:
        return None
    if data.successful_applicable_source_count > config.PRE_AI_GATE_MAX_SUCCESSFUL_SOURCES:
        return None

    feature_snapshot = extract_feature_snapshot(data)
    if _pre_ai_market_signal_count(feature_snapshot) < 5:
        return None

    recalibration_profiles = await db.get_numeric_recalibration_profiles(
        _recalibration_competition_name(data)
    )
    family_quality_profiles = await db.get_market_family_quality_profiles(
        _recalibration_competition_name(data)
    )
    baseline = await _build_live_numeric_baseline(
        data,
        feature_snapshot,
        recalibration_profiles=recalibration_profiles,
    )
    precheck_report = {
        "prob_w1": baseline["prob_w1"],
        "prob_draw": baseline["prob_draw"],
        "prob_w2": baseline["prob_w2"],
        "prob_over_2_5": baseline["prob_over_2_5"],
        "prob_under_2_5": baseline["prob_under_2_5"],
        "prob_btts_yes": baseline["prob_btts_yes"],
        "prob_btts_no": baseline["prob_btts_no"],
        "expected_goals": baseline["expected_goals"],
        **derive_expanded_market_probabilities(baseline),
    }
    candidates = build_market_candidates(
        precheck_report,
        feature_snapshot,
        data.odds,
        family_quality_profiles,
    )
    if not candidates:
        return None
    primary_candidates = [
        candidate for candidate in candidates
        if str(candidate.get("family") or "") in {"1x2", "totals", "btts"}
    ]
    if not primary_candidates:
        return None

    best_candidate = primary_candidates[0]
    best_edge = max(float(candidate.get("edge") or -999.0) for candidate in primary_candidates)
    best_confidence = max(
        float(candidate.get("confidence_pct") or 0.0)
        for candidate in primary_candidates
    )

    if best_edge > config.PRE_AI_GATE_MAX_EDGE_PCT:
        return None
    if best_confidence > config.PRE_AI_GATE_MAX_CANDIDATE_CONFIDENCE:
        return None

    gate_warning = (
        "AI skipped by pre-AI gate: low coverage and no actionable primary-market edge "
        f"(applicable {data.applicable_data_completeness * 100:.0f}%, best edge {best_edge:+.1f}%)."
    )
    logger.info(
        "Pre-AI gate skipped expensive analysis for %s vs %s (applicable=%d%%, best_edge=%+.1f, best_confidence=%.1f)",
        data.team1,
        data.team2,
        round(data.applicable_data_completeness * 100),
        best_edge,
        best_confidence,
    )
    _record_analysis_metric("pre_ai_gate_skips")
    seed_report = _build_pre_ai_seed_report_dict(baseline, best_candidate)
    return await _postprocess_report_dict(
        seed_report,
        data,
        warnings + [gate_warning],
        prompt_version=PRE_AI_GATE_PROMPT_VERSION,
        recalibration_profiles=recalibration_profiles,
        family_quality_profiles=family_quality_profiles,
    )


# ── Shared post-processing ─────────────────────────────────────────────────────

async def _postprocess_report_dict(
    report_dict: dict,
    data: MatchData,
    warnings: list[str],
    *,
    prompt_version: str,
    recalibration_profiles: Optional[list[dict]] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> PredictionReport:
    """Blend LLM output with a numeric baseline, then normalize and enrich the report."""
    feature_snapshot = extract_feature_snapshot(data)
    numeric_baseline = await _build_live_numeric_baseline(
        data,
        feature_snapshot,
        recalibration_profiles=recalibration_profiles,
    )
    numeric_baseline = blend_report_with_numeric_baseline(
        report_dict,
        data,
        feature_snapshot,
        recalibration_profiles=recalibration_profiles,
        numeric_baseline_override=numeric_baseline,
    )

    # Normalise probability sums
    for keys in [
        ("prob_w1", "prob_draw", "prob_w2"),
        ("prob_over_2_5", "prob_under_2_5"),
        ("prob_btts_yes", "prob_btts_no"),
    ]:
        total = sum(report_dict.get(k, 0) for k in keys)
        if total > 0 and abs(total - 100.0) > 0.5:
            factor = 100.0 / total
            for k in keys:
                if k in report_dict:
                    report_dict[k] = round(report_dict[k] * factor, 1)

    quality_adjustment_notes = _apply_quality_context_adjustments(report_dict, data)
    if quality_adjustment_notes:
        _append_summary_sentence(
            report_dict,
            "Quality adjustment applied: " + "; ".join(quality_adjustment_notes) + ".",
        )

    for key, value in derive_expanded_market_probabilities(report_dict).items():
        report_dict.setdefault(key, value)
    for key in ("prob_over_1_5", "prob_under_1_5", "prob_over_3_5", "prob_under_3_5"):
        report_dict.setdefault(key, numeric_baseline.get(key))

    odds_data = data.odds if data.odds and data.odds.scrape_success else None
    apply_pick_policy(report_dict, feature_snapshot, odds_data, family_quality_profiles)
    apply_top_pick_identity(report_dict, feature_snapshot, odds_data)
    _apply_context_risk_adjustments(report_dict, data)
    # Inject current bankroll for Kelly-proportional mode
    if getattr(config, "BANKROLL_KELLY_PROPORTIONAL", False):
        try:
            bstats = await db.get_paper_bankroll_stats(
                getattr(config, "BANKROLL_STARTING_UNITS", 100.0)
            )
            if bstats:
                report_dict["_current_bankroll_units"] = bstats["current_bankroll_units"]
        except Exception:
            pass

    apply_stake_recommendation(report_dict, feature_snapshot)
    try:
        bankroll_guardrails = await db.get_bankroll_guardrails()
    except Exception:
        bankroll_guardrails = None
    _apply_bankroll_guardrails(report_dict, bankroll_guardrails)
    report_dict["top_pick_context"] = build_top_pick_context(
        report_dict,
        feature_snapshot,
        odds_data,
        family_quality_profiles,
    )

    # Compute value bets: flag markets where our edge >= 5% vs bookmaker
    _value_bets: list[str] = []
    if data.odds and data.odds.scrape_success:
        _markets = [
            (report_dict.get("prob_w1", 0),   data.odds.implied_prob_w1,   data.team1),
            (report_dict.get("prob_draw", 0),  data.odds.implied_prob_draw, "Ничья"),
            (report_dict.get("prob_w2", 0),    data.odds.implied_prob_w2,   data.team2),
        ]
        for _claude_pct, _implied, _label in _markets:
            if _implied is None:
                continue
            _implied_pct = _implied * 100.0
            _edge = _claude_pct - _implied_pct
            if _edge >= 5.0:
                _value_bets.append(
                    f"{_label}: {_claude_pct:.0f}% vs {_implied_pct:.0f}% бук."
                    f" (+{_edge:.0f}% edge)"
                )
    report_dict["value_candidate_snapshot"] = build_value_candidate_snapshot(
        report_dict,
        feature_snapshot,
        odds_data,
        family_quality_profiles,
    )
    report_dict["value_bets"] = build_value_bets(
        report_dict,
        feature_snapshot,
        odds_data,
        family_quality_profiles,
    )

    report_dict["team1"] = data.team1
    report_dict["team2"] = data.team2
    merged_warnings = list(report_dict.get("data_warnings") or [])
    market_warnings: list[str] = []
    context_filter_warnings = _build_context_filter_warnings(data)
    if data.market_signals and data.market_signals.scrape_success:
        for warning in data.market_signals.market_warnings:
            warning_text = str(warning).strip()
            if warning_text:
                market_warnings.append(f"Market alert: {warning_text}")
    for warning in [*warnings, *market_warnings, *context_filter_warnings]:
        if warning not in merged_warnings:
            merged_warnings.append(warning)
    report_dict["data_warnings"] = merged_warnings
    report_dict["policy_flags"] = _build_policy_flags(
        report_dict,
        feature_snapshot,
        odds_data,
        family_quality_profiles,
        prompt_version=prompt_version,
    )
    _record_policy_flags(list(report_dict.get("policy_flags") or []))

    # Strip agent-chain-only fields that PredictionReport doesn't have
    for key in ("reasoning", "key_assumptions", "lesson_applications"):
        report_dict.pop(key, None)

    # Inject scraped AH market line into report (atomic pair)
    if data.odds and data.odds.scrape_success and data.odds.asian_handicap_line is not None:
        if "market_ah_line" not in report_dict:
            sign = "+" if data.odds.asian_handicap_line > 0 else ""
            report_dict["market_ah_line"] = f"{data.team1} {sign}{data.odds.asian_handicap_line:g}"
            report_dict["market_ah_odds"] = data.odds.asian_handicap_home_odds

    report_dict.setdefault("methodology_version", ANALYSIS_METHODOLOGY_VERSION)
    report_dict.setdefault("prompt_version", prompt_version)
    report_dict["feature_snapshot"] = feature_snapshot

    # Attach Pinnacle odds snapshot for CLV tracking
    if data.pinnacle_odds is not None:
        report_dict["pinnacle_odds_snapshot"] = data.pinnacle_odds

    return PredictionReport(**report_dict)


# ── Main analysis function ─────────────────────────────────────────────────────

def _merge_live_absences(data: MatchData) -> Optional[str]:
    """Gap-fill the squad scraper's key_absences from live-search injuries.

    Live search (grok) often surfaces injuries/suspensions the squad scraper
    missed — especially national teams the squad provider doesn't cover. When the
    scraper produced NO absences for a team but live search did, inject the live
    ``Player`` list so the numeric model (``key_absence_diff`` + ``_absence_impact``)
    and the ensemble feature see this (often unpriced) information. Scraper data is
    primary: a non-empty scraped list is never overwritten. Returns a note if merged.
    """
    xs = data.x_social
    if not xs or not getattr(xs, "scrape_success", False):
        return None
    live1 = list(getattr(xs, "team1_absences", None) or [])
    live2 = list(getattr(xs, "team2_absences", None) or [])
    if not live1 and not live2:
        return None

    if data.sofascore is None:
        from models import SofascoreData
        data.sofascore = SofascoreData(
            team1_name=data.team1, team2_name=data.team2, scrape_success=False,
        )
    sofa = data.sofascore

    filled: list[str] = []
    if live1 and not sofa.team1_key_absences:
        sofa.team1_key_absences = live1
        filled.append(f"{data.team1} (+{len(live1)})")
    if live2 and not sofa.team2_key_absences:
        sofa.team2_key_absences = live2
        filled.append(f"{data.team2} (+{len(live2)})")
    if not filled:
        return None

    # Activate the absence-consumption guards (feature snapshot + _absence_impact),
    # which require sofascore.scrape_success, so the injected live data influences
    # the numeric model rather than being ignored.
    sofa.scrape_success = True
    note = "Live-search absences merged: " + ", ".join(filled)
    logger.info(note)
    return note


async def analyze_match(data: MatchData) -> PredictionReport:
    """Analyze a match using the local agent chain or the single-call path.

    Flow:
      1. If AGENT_CHAIN_ENABLED: build match context and run the in-process agent chain.
         On chain failure, fall back to single-call.
      2. If AGENT_CHAIN_ENABLED is false, use the single-call path.
    """
    # Save team snapshots from this scrape (fire-and-forget)
    task = asyncio.create_task(_save_team_memory(data))
    task.add_done_callback(lambda t: logger.warning("Background task failed: %s", t.exception()) if not t.cancelled() and t.exception() else None)
    _record_analysis_metric("total_calls")

    # Overlay live-search injuries onto the squad data BEFORE any gate/model runs,
    # so the numeric baseline, ensemble feature, and agents all see them.
    _merge_live_absences(data)

    warnings = list(data.failed_source_messages) + list(data.unsupported_source_messages)
    pre_ai_gate_report = await _pre_ai_gate_report_or_none(data, warnings)
    if pre_ai_gate_report is not None:
        return pre_ai_gate_report

    if config.AGENT_CHAIN_ENABLED:
        try:
            return await _agent_chain_analyze(data, warnings)
        except Exception as exc:
            _record_analysis_metric("agent_chain_failures")
            logger.warning("Agent chain failed, falling back to single-call: %s", exc)

    return await _single_call_analyze(data, warnings)


async def _agent_chain_analyze(data: MatchData, warnings: list[str]) -> PredictionReport:
    """Analyze the match through the local in-process agent chain."""
    from agents.chain import run_agent_chain
    from agents.model_utils import AgentClients
    from memory import BaseMemory, get_shared_vector_memory

    _record_analysis_metric("agent_chain_runs")

    # Build memory context (non-critical — empty context on failure)
    try:
        vm = await get_shared_vector_memory()
        bm = BaseMemory(vm)
        memory_ctx = await bm.build_context(data.team1, data.team2)
    except Exception as exc:
        logger.warning("Memory init failed (non-critical): %s", exc)
        from models import MemoryContext
        memory_ctx = MemoryContext()

    # ── Build numeric baseline BEFORE agents so they can use it as anchor ──
    recalibration_profiles = await db.get_numeric_recalibration_profiles(
        _recalibration_competition_name(data)
    )
    feature_snapshot = extract_feature_snapshot(data)
    pre_baseline = await _build_live_numeric_baseline(
        data,
        feature_snapshot,
        recalibration_profiles=recalibration_profiles,
    )
    baseline_block = _format_numeric_baseline_block(pre_baseline)

    # Build data context with Elo ratings + numeric baseline
    elo_ctx = await _elo_block(data)
    data_context = _build_data_context(
        data,
        memory_ctx.calibration,
        memory_ctx.team_memory,
        elo_block=elo_ctx,
        numeric_baseline_block=baseline_block,
    )
    quality = _source_quality_counts(data)
    warnings_block = ""
    if warnings:
        warnings_block = "DATA GAPS / COVERAGE:\n" + "\n".join(f"  - {w}" for w in warnings)

    x_social_context = ""
    if data.x_social and data.x_social.scrape_success and data.x_social.signals:
        x_parts = []
        if data.x_social.key_narrative:
            x_parts.append(f"Key narrative: {data.x_social.key_narrative}")
        for sig in data.x_social.signals:
            x_parts.append(f"[{sig.type}][{sig.confidence}] {sig.content}")
        x_social_context = "\n".join(x_parts)

    logger.info(
        "Running local agent chain for %s vs %s (raw=%d%% applicable=%d%%)",
        data.team1,
        data.team2,
        quality["raw_pct"],
        quality["applicable_pct"],
    )

    result_dict = await run_agent_chain(
        clients=AgentClients(anthropic=_anthropic_client, openai=_openai_client),
        team1=data.team1,
        team2=data.team2,
        data_context=data_context,
        memory_context=memory_ctx,
        warnings_block=warnings_block,
        x_social_context=x_social_context,
        raw_completeness_pct=quality["raw_pct"],
        raw_ok_sources=quality["raw_ok"],
        raw_total_sources=quality["raw_total"],
        applicable_completeness_pct=quality["applicable_pct"],
        applicable_ok_sources=quality["applicable_ok"],
        applicable_total_sources=quality["applicable_total"],
    )
    if result_dict is None:
        raise RuntimeError("Local agent chain returned no report")

    family_quality_profiles = await db.get_market_family_quality_profiles(
        _recalibration_competition_name(data)
    )
    return await _postprocess_report_dict(
        result_dict,
        data,
        warnings,
        prompt_version=AGENT_CHAIN_PROMPT_VERSION,
        recalibration_profiles=recalibration_profiles,
        family_quality_profiles=family_quality_profiles,
    )


async def _single_call_analyze(data: MatchData, warnings: list[str]) -> PredictionReport:
    """Legacy single-call path: one Claude Sonnet request."""
    _record_analysis_metric("single_call_runs")

    # Build memory context (vector memory + calibration + team memory)
    from memory import BaseMemory, get_shared_vector_memory
    try:
        vm = await get_shared_vector_memory()
        bm = BaseMemory(vm)
        memory_ctx = await bm.build_context(data.team1, data.team2)
    except Exception as exc:
        logger.warning("Memory init failed in single-call (non-critical): %s", exc)
        from models import MemoryContext
        memory_ctx = MemoryContext()

    # ── Build numeric baseline BEFORE AI call ──
    recalibration_profiles_sc = await db.get_numeric_recalibration_profiles(
        _recalibration_competition_name(data)
    )
    feature_snapshot_sc = extract_feature_snapshot(data)
    pre_baseline_sc = await _build_live_numeric_baseline(
        data, feature_snapshot_sc, recalibration_profiles=recalibration_profiles_sc,
    )
    baseline_block_sc = _format_numeric_baseline_block(pre_baseline_sc)

    elo_ctx = await _elo_block(data)
    data_context = _build_data_context(
        data, memory_ctx.calibration, memory_ctx.team_memory,
        elo_block=elo_ctx,
        numeric_baseline_block=baseline_block_sc,
    )

    # Inject similar matches and lessons into data_context
    similar_block = BaseMemory.format_similar_matches_for_prompt(memory_ctx.similar_matches)
    lessons_block = BaseMemory.format_lessons_for_prompt(memory_ctx.relevant_lessons)
    if similar_block:
        data_context += "\n" + similar_block
    if lessons_block:
        data_context += "\n" + lessons_block
    quality = _source_quality_counts(data)

    warnings_block = ""
    if warnings:
        warnings_block = "DATA GAPS / COVERAGE:\n" + "\n".join(f"  • {w}" for w in warnings)

    user_prompt = _USER_PROMPT.format(
        team1=data.team1,
        team2=data.team2,
        data_context=data_context,
        raw_completeness_pct=quality["raw_pct"],
        raw_ok=quality["raw_ok"],
        raw_total=quality["raw_total"],
        applicable_completeness_pct=quality["applicable_pct"],
        applicable_ok=quality["applicable_ok"],
        applicable_total=quality["applicable_total"],
        warnings_block=warnings_block,
    )

    backend = (
        "OpenClaw -> Anthropic fallback" if config.USE_OPENCLAW
        else "Anthropic (claude-sonnet-4-6)"
    )
    logger.info(
        "Single-call: %d-char prompt via %s for %s vs %s (raw=%d%% applicable=%d%%)",
        len(user_prompt), backend, data.team1, data.team2, quality["raw_pct"], quality["applicable_pct"],
    )

    try:
        report_dict = await _generate_report_dict(user_prompt)
        recalibration_profiles = await db.get_numeric_recalibration_profiles(
            _recalibration_competition_name(data)
        )
        family_quality_profiles = await db.get_market_family_quality_profiles(
            _recalibration_competition_name(data)
        )
        return await _postprocess_report_dict(
            report_dict,
            data,
            warnings,
            prompt_version=SINGLE_CALL_PROMPT_VERSION,
            recalibration_profiles=recalibration_profiles,
            family_quality_profiles=family_quality_profiles,
        )
    except Exception as exc:
        _record_analysis_metric("single_call_failures")
        logger.error("Single-call analyze failed: %s", exc)
        return await _fallback_report(
            data,
            warnings,
            str(exc),
            prompt_version=SINGLE_CALL_PROMPT_VERSION,
        )


async def _generate_report_dict(user_prompt: str) -> dict:
    if not config.USE_OPENCLAW:
        _record_analysis_metric("anthropic_requests")
        logger.info("Using Anthropic backend directly")
        return await _call_and_parse_anthropic(user_prompt)

    _record_analysis_metric("openclaw_requests")
    logger.info("Using OpenClaw backend first")
    try:
        return await _call_and_parse_openclaw(user_prompt)
    except _OPENCLAW_FALLBACK_EXCEPTIONS as exc:
        _record_analysis_metric("openclaw_fallbacks")
        _record_analysis_metric("anthropic_requests")
        logger.warning("OpenClaw failed, falling back to Anthropic: %s", exc)
        return await _call_and_parse_anthropic(user_prompt)


async def _call_and_parse_openclaw(user_prompt: str) -> dict:
    raw = await _call_openclaw(user_prompt)
    return _loads_agent_json(raw, context="single-call openclaw")


async def _call_and_parse_anthropic(user_prompt: str) -> dict:
    raw = await _call_anthropic(user_prompt)
    return _loads_agent_json(raw, context="single-call anthropic")


async def _call_anthropic(user_prompt: str) -> str:
    """Call the Anthropic backend directly."""
    response = await _anthropic_client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.CLAUDE_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    content_blocks = [b for b in response.content if getattr(b, "type", "") == "text"]
    if not content_blocks:
        raise ValueError(f"Anthropic returned no text block (stop_reason={response.stop_reason})")
    return content_blocks[0].text.strip()


async def _call_openclaw(user_prompt: str) -> str:
    """Call claude-opus-4-5-thinking via the local OpenClaw gateway.

    OpenClaw exposes an OpenAI-compatible /v1/chat/completions endpoint.
    The main agent uses google-antigravity/claude-opus-4-5-thinking.
    Extended thinking blocks are filtered by OpenClaw — only text arrives.
    """
    response = await _openclaw_client.chat.completions.create(
        model=config.OPENCLAW_MODEL,
        max_tokens=config.CLAUDE_MAX_TOKENS,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content
    if isinstance(content, list):
        text_parts = [
            item.text for item in content
            if getattr(item, "type", "") == "text" and getattr(item, "text", "")
        ]
        content = "\n".join(text_parts)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenClaw returned an empty response")
    return content.strip()


# ── Fallback ───────────────────────────────────────────────────────────────────

async def _fallback_report(
    data: MatchData,
    warnings: list[str],
    error: str,
    *,
    prompt_version: str,
) -> PredictionReport:
    """Used when AI output is unavailable or invalid."""
    _record_analysis_metric("fallback_reports")
    feature_snapshot = extract_feature_snapshot(data)
    recalibration_profiles = await db.get_numeric_recalibration_profiles(feature_snapshot.competition_name)
    baseline = await _build_live_numeric_baseline(
        data,
        feature_snapshot,
        recalibration_profiles=recalibration_profiles,
    )
    feature_snapshot.model_prob_w1 = baseline["prob_w1"]
    feature_snapshot.model_prob_draw = baseline["prob_draw"]
    feature_snapshot.model_prob_w2 = baseline["prob_w2"]
    feature_snapshot.model_prob_over_2_5 = baseline["prob_over_2_5"]
    feature_snapshot.model_prob_under_2_5 = baseline["prob_under_2_5"]
    feature_snapshot.model_prob_btts_yes = baseline["prob_btts_yes"]
    feature_snapshot.model_prob_btts_no = baseline["prob_btts_no"]
    feature_snapshot.model_expected_goals = baseline["expected_goals"]
    feature_snapshot.no_bet_triggered = True
    feature_snapshot.top_edge_pct = 0.0

    policy_flags = [
        flag
        for flag in (_policy_path_flag(prompt_version), "path_fallback_numeric", "no_bet")
        if flag
    ]
    _record_policy_flags(policy_flags)

    return PredictionReport(
        team1=data.team1,
        team2=data.team2,
        prob_w1=baseline["prob_w1"],
        prob_draw=baseline["prob_draw"],
        prob_w2=baseline["prob_w2"],
        result_confidence="LOW",
        prob_over_2_5=baseline["prob_over_2_5"],
        prob_under_2_5=baseline["prob_under_2_5"],
        expected_goals=baseline["expected_goals"],
        goals_confidence="LOW",
        asian_handicap_rec="Level (0)", asian_handicap_confidence="LOW",
        prob_btts_yes=baseline["prob_btts_yes"],
        prob_btts_no=baseline["prob_btts_no"],
        btts_confidence="LOW",
        team1_strengths=[], team1_weaknesses=[],
        team2_strengths=[], team2_weaknesses=[],
        top_recommendation="No bet - fallback baseline only",
        top_recommendation_odds=None,
        top_confidence_pct=35.0,
        risk_level="HIGH",
        top_pick_context="AI path failed, so the report falls back to the numeric baseline only.",
        top_market_code="NO_BET",
        top_selection_code="NO_BET",
        stake_units=0.0,
        stake_label="No bet",
        stake_rationale="AI analysis failed, so the report stays at 0u and should not be played.",
        policy_flags=policy_flags,
        analysis_summary="AI analysis failed, so the report uses the numeric fallback baseline only.",
        data_warnings=warnings + [f"Analysis error: {error}"],
        value_candidate_snapshot=[],
        methodology_version=ANALYSIS_METHODOLOGY_VERSION,
        prompt_version=prompt_version,
        feature_snapshot=feature_snapshot,
        pinnacle_odds_snapshot=data.pinnacle_odds,
    )
