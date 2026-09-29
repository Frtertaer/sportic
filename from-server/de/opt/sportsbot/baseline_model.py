from __future__ import annotations

import math
from typing import Optional

import config
from models import (
    H2HRecord,
    MatchData,
    OddsData,
    PredictionFeatureSnapshot,
    RecentMatch,
    ValueCandidateSnapshot,
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _parse_score(score: Optional[str]) -> Optional[tuple[int, int]]:
    if not score:
        return None
    parts = str(score).replace(" ", "").split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _normalize(values: list[float], floors: list[float]) -> list[float]:
    adjusted = [max(value, floor) for value, floor in zip(values, floors)]
    total = sum(adjusted)
    if total <= 0:
        return [round(100.0 / len(adjusted), 1) for _ in adjusted]
    return [round(value * 100.0 / total, 1) for value in adjusted]


def _normalize_triplet(p1: float, pd: float, p2: float) -> tuple[float, float, float]:
    values = _normalize([p1, pd, p2], [5.0, 8.0, 5.0])
    diff = round(100.0 - sum(values), 1)
    values[0] = round(values[0] + diff, 1)
    return values[0], values[1], values[2]


def _normalize_pair(px: float, py: float) -> tuple[float, float]:
    values = _normalize([px, py], [10.0, 10.0])
    diff = round(100.0 - sum(values), 1)
    values[0] = round(values[0] + diff, 1)
    return values[0], values[1]


def _market_pair(odds_a: Optional[float], odds_b: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    if not odds_a or not odds_b or odds_a <= 1.0 or odds_b <= 1.0:
        return None, None
    inv_a = 1.0 / odds_a
    inv_b = 1.0 / odds_b
    total = inv_a + inv_b
    if total <= 0:
        return None, None
    return round(inv_a * 100.0 / total, 1), round(inv_b * 100.0 / total, 1)


def _implied_odds_from_pct(probability_pct: Optional[float]) -> Optional[float]:
    if probability_pct is None or probability_pct <= 0:
        return None
    return round(100.0 / probability_pct, 2)


def _poisson_exact(lambda_goals: float, goals: int) -> float:
    return math.exp(-lambda_goals) * (lambda_goals ** goals) / math.factorial(goals)


def _poisson_over_probability(lambda_goals: float, line: float) -> float:
    cutoff = max(0, int(math.floor(line)))
    cumulative = sum(_poisson_exact(lambda_goals, goals) for goals in range(cutoff + 1))
    return round(_clamp((1.0 - cumulative) * 100.0, 1.0, 99.0), 1)


def _dixon_coles_tau(h: int, a: int, lam1: float, lam2: float, rho: float) -> float:
    """Dixon-Coles (1997) correction factor for low-scoring outcomes."""
    if h == 0 and a == 0:
        return 1.0 - lam1 * lam2 * rho
    elif h == 0 and a == 1:
        return 1.0 + lam1 * rho
    elif h == 1 and a == 0:
        return 1.0 + lam2 * rho
    elif h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _build_dixon_coles_matrix(
    lam1: float, lam2: float, rho: float, max_goals: int = 8,
) -> list[list[float]]:
    """Build score probability matrix with Dixon-Coles correction."""
    matrix = []
    total = 0.0
    for h in range(max_goals + 1):
        row = []
        for a in range(max_goals + 1):
            p = (_poisson_exact(lam1, h) * _poisson_exact(lam2, a)
                 * _dixon_coles_tau(h, a, lam1, lam2, rho))
            p = max(p, 0.0)
            row.append(p)
            total += p
        matrix.append(row)
    # Renormalize
    if total > 0 and abs(total - 1.0) > 1e-6:
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                matrix[h][a] /= total
    return matrix


def _matrix_over_probability(matrix: list[list[float]], line: float) -> float:
    """P(total goals > line) from score probability matrix."""
    cutoff = int(math.floor(line))
    under_prob = 0.0
    max_g = len(matrix)
    for h in range(max_g):
        for a in range(max_g):
            if h + a <= cutoff:
                under_prob += matrix[h][a]
    return round(_clamp((1.0 - under_prob) * 100.0, 1.0, 99.0), 1)


def _matrix_btts_probability(matrix: list[list[float]]) -> float:
    """P(both teams score) from score probability matrix."""
    btts = 0.0
    max_g = len(matrix)
    for h in range(1, max_g):
        for a in range(1, max_g):
            btts += matrix[h][a]
    return round(_clamp(btts * 100.0, 1.0, 99.0), 1)


def _get_league_rho(league_name: str | None) -> float:
    """Get Dixon-Coles rho for a league, falling back to global config."""
    if league_name:
        overrides = getattr(config, "LEAGUE_RHO_OVERRIDES", {})
        key = league_name.strip().lower()
        for league_key, rho in overrides.items():
            if league_key in key or key in league_key:
                return rho
    return getattr(config, "DIXON_COLES_RHO", -0.10)


def _competition_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = " ".join(str(value).strip().lower().split())
    return normalized or None


def _probability_bucket_label(probability_pct: Optional[float]) -> Optional[str]:
    if probability_pct is None:
        return None
    bounded = _clamp(float(probability_pct), 0.0, 99.9)
    low = int(math.floor(bounded / 5.0) * 5.0)
    high = int(min(100.0, low + 5.0))
    return f"{low}-{high}"


def _recommendation_mode() -> str:
    mode = str(getattr(config, "RECOMMENDATION_MODE", "balanced") or "balanced").lower()
    if mode not in {"strict", "balanced", "action"}:
        return "balanced"
    return mode


def _bankroll_mode() -> str:
    mode = str(getattr(config, "BANKROLL_MODE", "balanced") or "balanced").lower()
    if mode not in {"conservative", "balanced", "aggressive"}:
        return "balanced"
    return mode


def _policy_thresholds() -> dict[str, float]:
    mode = _recommendation_mode()
    thresholds = {
        "strict": {
            "min_confidence": 60.0,
            "weak_market_edge": 4.0,
            "severe_market_edge": 2.0,
            "low_data": 0.58,
            "severe_low_data": 0.42,
            "high_disagreement": 16.0,
            "warning_count": 2.0,
            "reason_count": 2.0,
            "value_bet_edge": 5.0,
        },
        "balanced": {
            "min_confidence": 55.0,
            "weak_market_edge": 2.25,
            "severe_market_edge": 1.0,
            "low_data": 0.45,
            "severe_low_data": 0.30,
            "high_disagreement": 18.0,
            "warning_count": 3.0,
            "reason_count": 3.0,
            "value_bet_edge": 4.0,
        },
        "action": {
            "min_confidence": 50.0,
            "weak_market_edge": 1.0,
            "severe_market_edge": 0.35,
            "low_data": 0.32,
            "severe_low_data": 0.18,
            "high_disagreement": 20.0,
            "warning_count": 5.0,
            "reason_count": 4.0,
            "value_bet_edge": 3.0,
        },
    }
    return thresholds[mode]


def _market_family_policy(family: str) -> dict[str, float]:
    ou_priority = getattr(config, "OU_MARKET_PRIORITY", True)
    return {
        "1x2": {
            # 1X2 deprioritized: backtest shows only 51.7% accuracy
            "score_bias": 0.3 if ou_priority else 0.8,
            "top_pick_edge_floor": 1.5 if ou_priority else 0.0,
            "top_pick_prob_floor": 55.0 if ou_priority else 0.0,
            "top_pick_data_floor": 0.0,
            "action_edge_floor": 0.5 if ou_priority else -0.25,
            "action_prob_floor": 56.0 if ou_priority else 54.0,
            "action_data_floor": 0.0,
            "value_edge_bonus": 0.0,
        },
        "totals": {
            # TOTALS prioritized: backtest shows 57.3% accuracy (strongest edge)
            "score_bias": 1.5 if ou_priority else 0.3,
            "top_pick_edge_floor": 0.25 if ou_priority else 0.75,
            "top_pick_prob_floor": 50.0 if ou_priority else 51.0,
            "top_pick_data_floor": 0.25 if ou_priority else 0.35,
            "action_edge_floor": -0.5 if ou_priority else 0.0,
            "action_prob_floor": 51.0 if ou_priority else 54.0,
            "action_data_floor": 0.20 if ou_priority else 0.30,
            "value_edge_bonus": 1.0 if ou_priority else 0.5,
        },
        "btts": {
            # BTTS decent: 54.2% accuracy
            "score_bias": 0.2 if ou_priority else -0.2,
            "top_pick_edge_floor": 1.5 if ou_priority else 2.0,
            "top_pick_prob_floor": 52.0 if ou_priority else 53.0,
            "top_pick_data_floor": 0.40 if ou_priority else 0.48,
            "action_edge_floor": 0.75 if ou_priority else 1.25,
            "action_prob_floor": 53.0 if ou_priority else 55.0,
            "action_data_floor": 0.35 if ou_priority else 0.42,
            "value_edge_bonus": 0.8 if ou_priority else 1.0,
        },
        "dnb": {
            "score_bias": -1.0,
            "top_pick_edge_floor": 2.75,
            "top_pick_prob_floor": 60.0,
            "top_pick_data_floor": 0.58,
            "action_edge_floor": 2.0,
            "action_prob_floor": 60.0,
            "action_data_floor": 0.52,
            "value_edge_bonus": 1.75,
        },
        "double_chance": {
            "score_bias": -1.2,
            "top_pick_edge_floor": 3.25,
            "top_pick_prob_floor": 64.0,
            "top_pick_data_floor": 0.6,
            "action_edge_floor": 2.5,
            "action_prob_floor": 64.0,
            "action_data_floor": 0.55,
            "value_edge_bonus": 2.0,
        },
        "total_1_5": {
            "score_bias": -0.85,
            "top_pick_edge_floor": 2.25,
            "top_pick_prob_floor": 66.0,
            "top_pick_data_floor": 0.55,
            "action_edge_floor": 1.5,
            "action_prob_floor": 66.0,
            "action_data_floor": 0.5,
            "value_edge_bonus": 1.5,
        },
        "total_3_5": {
            "score_bias": -0.7,
            "top_pick_edge_floor": 2.0,
            "top_pick_prob_floor": 60.0,
            "top_pick_data_floor": 0.55,
            "action_edge_floor": 1.25,
            "action_prob_floor": 62.0,
            "action_data_floor": 0.5,
            "value_edge_bonus": 1.25,
        },
    }.get(
        family,
        {
            "score_bias": 0.0,
            "top_pick_edge_floor": 1.5,
            "top_pick_prob_floor": 54.0,
            "top_pick_data_floor": 0.5,
            "action_edge_floor": 1.0,
            "action_prob_floor": 55.0,
            "action_data_floor": 0.45,
            "value_edge_bonus": 1.0,
        },
    )


def _core_market_family(family: str) -> str:
    normalized = str(family or "").strip().lower()
    if normalized in {"1x2", "dnb", "double_chance"}:
        return "1x2"
    if normalized in {"totals", "total_1_5", "total_3_5"}:
        return "totals"
    if normalized == "btts":
        return "btts"
    return normalized or "unknown"


def _market_family_quality_profile(
    family: str,
    profiles: Optional[list[dict]],
) -> Optional[dict]:
    if not profiles:
        return None
    core_family = _core_market_family(family)
    for profile in profiles:
        if str(profile.get("family") or "").strip().lower() == core_family:
            return profile
    return None


def _candidate_passes_family_gate(
    candidate: dict[str, object],
    snapshot: PredictionFeatureSnapshot,
    *,
    purpose: str,
    family_quality_profiles: Optional[list[dict]] = None,
) -> bool:
    family = str(candidate.get("family") or "")
    policy = _market_family_policy(family)
    edge = float(candidate.get("edge") or -999.0)
    probability = float(candidate.get("probability") or 0.0)
    data_completeness = float(snapshot.data_completeness or 0.0)
    profile = _market_family_quality_profile(family, family_quality_profiles)
    core_family = _core_market_family(family)
    is_derived_family = core_family != str(family or "").strip().lower()

    edge_floor_bonus = 0.0
    prob_floor_bonus = 0.0
    data_floor_bonus = 0.0
    if profile is not None:
        sample_size = int(profile.get("sample_size") or 0)
        trust_score = float(profile.get("trust_score") or 0.0)
        if sample_size < 120 and is_derived_family:
            edge_floor_bonus += 0.5
        if trust_score < -1.5:
            edge_floor_bonus += 1.5 if is_derived_family else 0.85
            prob_floor_bonus += 2.0 if is_derived_family else 1.0
            data_floor_bonus += 0.06 if is_derived_family else 0.03
        elif trust_score < -0.5:
            edge_floor_bonus += 0.8 if is_derived_family else 0.4
            prob_floor_bonus += 1.0 if is_derived_family else 0.5
            data_floor_bonus += 0.03 if is_derived_family else 0.01
        elif trust_score > 1.0 and sample_size >= 250 and not is_derived_family:
            edge_floor_bonus -= 0.25

        if core_family == "btts" and not is_derived_family:
            if trust_score >= 2.0 and sample_size >= 500:
                edge_floor_bonus -= 0.45
                prob_floor_bonus -= 1.0
                data_floor_bonus -= 0.04
            elif trust_score >= 1.25 and sample_size >= 250:
                edge_floor_bonus -= 0.2
                prob_floor_bonus -= 0.5
                data_floor_bonus -= 0.02
            elif trust_score <= -1.75 and sample_size >= 250:
                edge_floor_bonus += 0.5
                prob_floor_bonus += 1.0
                data_floor_bonus += 0.03

    if purpose == "top_pick":
        return (
            edge >= float(policy["top_pick_edge_floor"]) + edge_floor_bonus
            and probability >= float(policy["top_pick_prob_floor"]) + prob_floor_bonus
            and data_completeness >= float(policy["top_pick_data_floor"]) + data_floor_bonus
        )
    if purpose == "action":
        return (
            edge >= float(policy["action_edge_floor"]) + edge_floor_bonus
            and probability >= float(policy["action_prob_floor"]) + prob_floor_bonus
            and data_completeness >= float(policy["action_data_floor"]) + data_floor_bonus
        )
    if purpose == "value":
        threshold = float(_policy_thresholds()["value_bet_edge"]) + float(policy["value_edge_bonus"])
        return (
            edge >= threshold + max(0.0, edge_floor_bonus)
            and probability >= max(50.0, float(policy["top_pick_prob_floor"]) - 2.0 + prob_floor_bonus)
            and data_completeness >= max(0.0, float(policy["top_pick_data_floor"]) - 0.05 + data_floor_bonus)
        )
    return True


def _candidate_family_reason(candidate: Optional[dict[str, object]]) -> Optional[str]:
    if not candidate:
        return None
    family = str(candidate.get("family") or "")
    return {
        "btts": "BTTS signal not strong enough",
        "dnb": "draw-no-bet edge not strong enough",
        "double_chance": "double-chance edge not strong enough",
        "total_1_5": "alternative totals edge not strong enough",
        "total_3_5": "alternative totals edge not strong enough",
    }.get(family, None)


def _apply_btts_borderline_rerank(
    candidates: list[dict[str, object]],
    snapshot: PredictionFeatureSnapshot,
) -> None:
    if snapshot.data_completeness < 0.55:
        return

    core_candidates = [
        candidate
        for candidate in candidates
        if _core_market_family(str(candidate.get("family") or ""))
        == str(candidate.get("family") or "").strip().lower()
    ]
    if len(core_candidates) < 2:
        return

    btts_candidates = [
        candidate
        for candidate in core_candidates
        if str(candidate.get("family") or "") == "btts"
        and bool(candidate.get("eligible_for_top_pick"))
    ]
    if not btts_candidates:
        return

    for candidate in btts_candidates:
        trust_score = float(candidate.get("family_trust_score") or 0.0)
        sample_size = int(candidate.get("family_sample_size") or 0)
        if trust_score < 1.75 or sample_size < 400:
            continue

        best_other = max(
            (
                other
                for other in core_candidates
                if other is not candidate and bool(other.get("eligible_for_top_pick"))
            ),
            key=lambda item: float(item.get("score") or 0.0),
            default=None,
        )
        if best_other is None:
            continue

        score_gap = float(best_other.get("score") or 0.0) - float(candidate.get("score") or 0.0)
        if score_gap <= 0.0 or score_gap > 0.85:
            continue

        edge_gap = float(best_other.get("edge") or 0.0) - float(candidate.get("edge") or 0.0)
        if edge_gap > 0.4:
            continue

        confidence_gap = float(best_other.get("confidence_pct") or 0.0) - float(candidate.get("confidence_pct") or 0.0)
        if confidence_gap > 3.0:
            continue

        bonus = 0.35 + min(0.35, max(0.0, trust_score - 1.75) * 0.18)
        other_trust = best_other.get("family_trust_score")
        if other_trust is not None:
            other_trust_value = float(other_trust)
            other_family = _core_market_family(str(best_other.get("family") or ""))
            if other_trust_value < -0.25:
                if other_family == "1x2":
                    bonus += min(0.25, abs(other_trust_value) * 0.16)
                elif other_family == "totals":
                    bonus += min(0.18, abs(other_trust_value) * 0.10)

        bonus = round(min(0.85, bonus), 2)
        candidate["score"] = round(float(candidate.get("score") or 0.0) + bonus, 2)
        candidate["family_borderline_rerank_bonus"] = bonus


def _recent_match_team_goals(match: RecentMatch) -> Optional[tuple[int, int]]:
    parsed = _parse_score(match.score)
    if parsed is None:
        return None
    home_goals, away_goals = parsed
    if match.home_away == "H":
        return home_goals, away_goals
    return away_goals, home_goals


def _form_points(matches: list[RecentMatch]) -> int:
    mapping = {"W": 3, "D": 1, "L": 0}
    return sum(mapping.get(match.result.value, 0) for match in matches[:5])


def _form_goal_diff(matches: list[RecentMatch]) -> Optional[int]:
    values: list[int] = []
    for match in matches[:5]:
        goals = _recent_match_team_goals(match)
        if goals is None:
            continue
        scored, conceded = goals
        values.append(scored - conceded)
    if not values:
        return None
    return sum(values)


def _safe_log_ratio(value: Optional[float]) -> float:
    if value is None or value <= 0:
        return 0.0
    return math.log(value, 2)


def _parse_scores_str(scores_str: Optional[str], played: Optional[int]) -> tuple[Optional[float], Optional[float]]:
    """Parse standings scores_str like '45:20' into (scored_avg, conceded_avg)."""
    if not scores_str or not played or played <= 0:
        return None, None
    parts = str(scores_str).split(":")
    if len(parts) != 2:
        return None, None
    try:
        scored = int(parts[0].strip())
        conceded = int(parts[1].strip())
        return round(scored / played, 3), round(conceded / played, 3)
    except (ValueError, ZeroDivisionError):
        return None, None


def _form_home_away_goals(matches: list[RecentMatch], venue: str) -> tuple[Optional[float], Optional[float]]:
    """Calculate avg scored/conceded from recent form filtered by home_away ('H' or 'A')."""
    scored_list: list[int] = []
    conceded_list: list[int] = []
    for m in matches:
        if m.home_away != venue:
            continue
        goals = _recent_match_team_goals(m)
        if goals is None:
            continue
        team_scored, team_conceded = goals
        scored_list.append(team_scored)
        conceded_list.append(team_conceded)
    if len(scored_list) < 3:  # need at least 3 matches for stability
        return None, None
    return round(sum(scored_list) / len(scored_list), 3), round(sum(conceded_list) / len(conceded_list), 3)


def _resolve_competition_name(data: MatchData) -> Optional[str]:
    if data.match_context and data.match_context.competition_name:
        return data.match_context.competition_name
    if data.flashscore and data.flashscore.league_name:
        return data.flashscore.league_name
    if data.standings_context and data.standings_context.league_name:
        return data.standings_context.league_name
    return None


def _extract_h2h_features(
    team1: str,
    team2: str,
    records: list[H2HRecord],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    team1_key = team1.strip().lower()
    team2_key = team2.strip().lower()
    wins = 0
    draws = 0
    goals: list[int] = []

    for record in records[:5]:
        parsed = _parse_score(record.score)
        if parsed is None:
            continue
        home_goals, away_goals = parsed
        home_key = record.home_team.strip().lower()
        away_key = record.away_team.strip().lower()
        if home_key == team1_key and away_key == team2_key:
            team1_goals, team2_goals = home_goals, away_goals
        elif home_key == team2_key and away_key == team1_key:
            team1_goals, team2_goals = away_goals, home_goals
        else:
            continue
        goals.append(team1_goals + team2_goals)
        if team1_goals > team2_goals:
            wins += 1
        elif team1_goals == team2_goals:
            draws += 1

    total = len(goals)
    if total == 0:
        return None, None, None
    return (
        round(wins * 100.0 / total, 1),
        round(draws * 100.0 / total, 1),
        round(sum(goals) / total, 2),
    )


def extract_feature_snapshot(data: MatchData) -> PredictionFeatureSnapshot:
    market_over, market_under = (None, None)
    market_btts_yes, market_btts_no = (None, None)
    market_prob_w1 = None
    market_prob_draw = None
    market_prob_w2 = None
    is_neutral_venue = None
    is_team1_home = None

    if data.match_context and data.match_context.scrape_success:
        is_neutral_venue = data.match_context.is_neutral_venue
        is_team1_home = data.match_context.is_team1_home

    form_points_last5_diff = None
    form_goal_diff_last5_diff = None
    form_scored_avg_diff = None
    form_conceded_avg_diff = None
    h2h_team1_win_rate = None
    h2h_draw_rate = None
    h2h_avg_goals = None

    if data.flashscore and data.flashscore.scrape_success:
        fs = data.flashscore
        form_points_last5_diff = float(_form_points(fs.team1_form) - _form_points(fs.team2_form))

        t1_goal_diff = _form_goal_diff(fs.team1_form)
        t2_goal_diff = _form_goal_diff(fs.team2_form)
        if t1_goal_diff is not None and t2_goal_diff is not None:
            form_goal_diff_last5_diff = float(t1_goal_diff - t2_goal_diff)

        if fs.team1_goals_scored_avg is not None and fs.team2_goals_scored_avg is not None:
            form_scored_avg_diff = round(fs.team1_goals_scored_avg - fs.team2_goals_scored_avg, 3)
        if fs.team1_goals_conceded_avg is not None and fs.team2_goals_conceded_avg is not None:
            form_conceded_avg_diff = round(fs.team1_goals_conceded_avg - fs.team2_goals_conceded_avg, 3)

        h2h_team1_win_rate, h2h_draw_rate, h2h_avg_goals = _extract_h2h_features(
            data.team1,
            data.team2,
            fs.h2h_matches,
        )

    xg_diff = None
    xga_edge = None
    rating_diff = None
    possession_diff = None
    shots_on_target_avg_diff = None
    corners_avg_diff = None
    if data.advanced_stats and data.advanced_stats.scrape_success:
        stats = data.advanced_stats
        if stats.team1_metrics.xg is not None and stats.team2_metrics.xg is not None:
            xg_diff = round(stats.team1_metrics.xg - stats.team2_metrics.xg, 3)
        if stats.team1_metrics.xga is not None and stats.team2_metrics.xga is not None:
            xga_edge = round(stats.team2_metrics.xga - stats.team1_metrics.xga, 3)
        if stats.team1_metrics.rating is not None and stats.team2_metrics.rating is not None:
            rating_diff = round(stats.team1_metrics.rating - stats.team2_metrics.rating, 3)
        if (
            stats.team1_metrics.possession_pct is not None
            and stats.team2_metrics.possession_pct is not None
        ):
            possession_diff = round(
                stats.team1_metrics.possession_pct - stats.team2_metrics.possession_pct,
                3,
            )
        if (
            stats.team1_metrics.shots_on_target_per_match is not None
            and stats.team2_metrics.shots_on_target_per_match is not None
        ):
            shots_on_target_avg_diff = round(
                stats.team1_metrics.shots_on_target_per_match - stats.team2_metrics.shots_on_target_per_match,
                3,
            )
        if stats.team1_metrics.corners is not None and stats.team2_metrics.corners is not None:
            corners_avg_diff = round(
                stats.team1_metrics.corners - stats.team2_metrics.corners,
                3,
            )

    standings_point_gap = None
    standings_position_gap = None
    if data.standings_context and data.standings_context.scrape_success:
        standings_point_gap = data.standings_context.point_gap
        standings_position_gap = data.standings_context.position_gap

    rest_gap_hours = None
    team1_short_rest = None
    team2_short_rest = None
    if data.schedule_load and data.schedule_load.scrape_success:
        rest_gap_hours = data.schedule_load.rest_gap_hours
        team1_short_rest = data.schedule_load.team1.short_rest
        team2_short_rest = data.schedule_load.team2.short_rest

    market_value_ratio = None
    if (
        data.transfermarkt
        and data.transfermarkt.scrape_success
        and data.transfermarkt.team1_market_value_eur
        and data.transfermarkt.team2_market_value_eur
        and data.transfermarkt.team2_market_value_eur > 0
    ):
        market_value_ratio = round(
            data.transfermarkt.team1_market_value_eur / data.transfermarkt.team2_market_value_eur,
            4,
        )

    key_absence_diff = None
    if data.sofascore and data.sofascore.scrape_success:
        key_absence_diff = len(data.sofascore.team1_key_absences) - len(data.sofascore.team2_key_absences)

    if data.odds and data.odds.scrape_success:
        if data.odds.implied_prob_w1 is not None:
            market_prob_w1 = round(data.odds.implied_prob_w1 * 100.0, 1)
        if data.odds.implied_prob_draw is not None:
            market_prob_draw = round(data.odds.implied_prob_draw * 100.0, 1)
        if data.odds.implied_prob_w2 is not None:
            market_prob_w2 = round(data.odds.implied_prob_w2 * 100.0, 1)
        market_over, market_under = _market_pair(data.odds.over_2_5_odds, data.odds.under_2_5_odds)
        market_btts_yes, market_btts_no = _market_pair(data.odds.btts_yes_odds, data.odds.btts_no_odds)

    # ── Home/Away goal splits ──────────────────────────────────────────────
    # Priority: standings (season-level, larger sample) > form (recent 10, filtered)
    t1_hs, t1_hc, t1_as, t1_ac = None, None, None, None
    t2_hs, t2_hc, t2_as, t2_ac = None, None, None, None

    if data.standings_context and data.standings_context.scrape_success:
        sc = data.standings_context
        t1_hs, t1_hc = _parse_scores_str(sc.team1_home.scores_str, sc.team1_home.played)
        t1_as, t1_ac = _parse_scores_str(sc.team1_away.scores_str, sc.team1_away.played)
        t2_hs, t2_hc = _parse_scores_str(sc.team2_home.scores_str, sc.team2_home.played)
        t2_as, t2_ac = _parse_scores_str(sc.team2_away.scores_str, sc.team2_away.played)

    # Fallback: extract from form data (last 10 matches, filtered by venue)
    if data.flashscore and data.flashscore.scrape_success:
        fs = data.flashscore
        if t1_hs is None:
            t1_hs, t1_hc = _form_home_away_goals(fs.team1_form, "H")
        if t1_as is None:
            t1_as, t1_ac = _form_home_away_goals(fs.team1_form, "A")
        if t2_hs is None:
            t2_hs, t2_hc = _form_home_away_goals(fs.team2_form, "H")
        if t2_as is None:
            t2_as, t2_ac = _form_home_away_goals(fs.team2_form, "A")

    # ── Referee context ────────────────────────────────────────────────
    referee_name = None
    referee_goals_vs_avg = None
    referee_over_2_5_pct = None
    if data.match_context and data.match_context.scrape_success:
        ref_name = getattr(data.match_context, "referee_name", None)
        if ref_name:
            try:
                from referee import get_referee_profile
                rp = get_referee_profile(ref_name)
                if rp:
                    referee_name = rp.name
                    referee_goals_vs_avg = rp.goals_vs_avg
                    referee_over_2_5_pct = rp.over_2_5_pct
            except Exception:
                pass

    # ── Weather context ─────────────────────────────────────────────
    weather_wind_kmh = None
    weather_temperature_c = None
    weather_precipitation_mm = None
    if data.weather_context and data.weather_context.scrape_success:
        weather_wind_kmh = data.weather_context.wind_speed_kmh
        weather_temperature_c = data.weather_context.temperature_c
        weather_precipitation_mm = data.weather_context.precipitation_mm

    # ── Line movement signals ─────────────────────────────────────
    movement_score_w1 = None
    movement_score_over = None
    favourite_gap_pct = None
    if data.market_signals and data.market_signals.scrape_success:
        ms = data.market_signals
        w1_sig = ms.one_x_two.get("1") or ms.one_x_two.get("W1")
        if w1_sig:
            movement_score_w1 = w1_sig.movement_score
        over_sig = ms.over_under_2_5.get("Over") or ms.over_under_2_5.get("over")
        if over_sig:
            movement_score_over = over_sig.movement_score
        favourite_gap_pct = ms.favourite_gap_pct

    return PredictionFeatureSnapshot(
        team1=data.team1,
        team2=data.team2,
        competition_name=_resolve_competition_name(data),
        data_completeness=round(data.data_completeness, 4),
        successful_source_count=data.successful_source_count,
        total_source_count=data.total_source_count,
        is_neutral_venue=is_neutral_venue,
        is_team1_home=is_team1_home,
        market_prob_w1=market_prob_w1,
        market_prob_draw=market_prob_draw,
        market_prob_w2=market_prob_w2,
        market_prob_over_2_5=market_over,
        market_prob_under_2_5=market_under,
        market_prob_btts_yes=market_btts_yes,
        market_prob_btts_no=market_btts_no,
        form_points_last5_diff=form_points_last5_diff,
        form_goal_diff_last5_diff=form_goal_diff_last5_diff,
        form_scored_avg_diff=form_scored_avg_diff,
        form_conceded_avg_diff=form_conceded_avg_diff,
        h2h_team1_win_rate=h2h_team1_win_rate,
        h2h_draw_rate=h2h_draw_rate,
        h2h_avg_goals=h2h_avg_goals,
        xg_diff=xg_diff,
        xga_edge=xga_edge,
        rating_diff=rating_diff,
        possession_diff=possession_diff,
        shots_on_target_avg_diff=shots_on_target_avg_diff,
        corners_avg_diff=corners_avg_diff,
        standings_point_gap=standings_point_gap,
        standings_position_gap=standings_position_gap,
        rest_gap_hours=rest_gap_hours,
        team1_short_rest=team1_short_rest,
        team2_short_rest=team2_short_rest,
        market_value_ratio=market_value_ratio,
        key_absence_diff=key_absence_diff,
        team1_home_scored_avg=t1_hs,
        team1_home_conceded_avg=t1_hc,
        team1_away_scored_avg=t1_as,
        team1_away_conceded_avg=t1_ac,
        team2_home_scored_avg=t2_hs,
        team2_home_conceded_avg=t2_hc,
        team2_away_scored_avg=t2_as,
        team2_away_conceded_avg=t2_ac,
        referee_name=referee_name,
        referee_goals_vs_avg=referee_goals_vs_avg,
        referee_over_2_5_pct=referee_over_2_5_pct,
        weather_wind_kmh=weather_wind_kmh,
        weather_temperature_c=weather_temperature_c,
        weather_precipitation_mm=weather_precipitation_mm,
        movement_score_w1=movement_score_w1,
        movement_score_over=movement_score_over,
        favourite_gap_pct=favourite_gap_pct,
    )


def _lookup_recalibration_delta(
    profiles: Optional[list[dict]],
    *,
    market: str,
    probability_pct: Optional[float],
    competition_name: Optional[str],
) -> Optional[float]:
    if not profiles or probability_pct is None:
        return None
    bucket_label = _probability_bucket_label(probability_pct)
    if bucket_label is None:
        return None
    competition_key = _competition_key(competition_name)
    for scope, scoped_competition in (("COMPETITION", competition_key), ("GLOBAL", None)):
        for profile in profiles:
            if profile.get("market") != market:
                continue
            if profile.get("bucket_label") != bucket_label:
                continue
            if profile.get("scope") != scope:
                continue
            if scope == "COMPETITION" and profile.get("competition_key") != scoped_competition:
                continue
            try:
                return float(profile.get("delta_pct") or 0.0)
            except (TypeError, ValueError):
                return None
    return None


def _lookup_recalibration_delta_with_fallbacks(
    profiles: Optional[list[dict]],
    *,
    markets: tuple[str, ...],
    probability_pct: Optional[float],
    competition_name: Optional[str],
) -> float:
    for market in markets:
        delta = _lookup_recalibration_delta(
            profiles,
            market=market,
            probability_pct=probability_pct,
            competition_name=competition_name,
        )
        if delta is not None:
            return delta
    return 0.0


def _btts_goal_environment_market(prob_over_2_5: Optional[float]) -> Optional[str]:
    if prob_over_2_5 is None:
        return None
    probability = float(prob_over_2_5)
    if probability < 47.5:
        return "BTTS_YES_LOW_TOTALS"
    if probability > 57.5:
        return "BTTS_YES_HIGH_TOTALS"
    return "BTTS_YES_MID_TOTALS"


def _apply_binary_delta(yes_probability: float, delta_pct: float) -> tuple[float, float]:
    adjusted_yes = _clamp(float(yes_probability) + float(delta_pct), 5.0, 95.0)
    return _normalize_pair(adjusted_yes, 100.0 - adjusted_yes)


def _apply_top_pick_delta(
    prob_w1: float,
    prob_draw: float,
    prob_w2: float,
    *,
    top_key: str,
    delta_pct: float,
) -> tuple[float, float, float]:
    probs = {
        "prob_w1": float(prob_w1),
        "prob_draw": float(prob_draw),
        "prob_w2": float(prob_w2),
    }
    other_keys = [key for key in probs if key != top_key]
    current_top = probs[top_key]
    adjusted_top = _clamp(current_top + delta_pct, 5.0, 90.0)
    transfer = adjusted_top - current_top
    other_total = sum(probs[key] for key in other_keys)

    if abs(transfer) > 0 and other_total > 0:
        for key in other_keys:
            share = probs[key] / other_total
            probs[key] -= transfer * share
    elif abs(transfer) > 0:
        for key in other_keys:
            probs[key] -= transfer / len(other_keys)
    probs[top_key] = adjusted_top
    return _normalize_triplet(probs["prob_w1"], probs["prob_draw"], probs["prob_w2"])


def apply_numeric_recalibration(
    baseline: dict[str, float],
    snapshot: PredictionFeatureSnapshot,
    profiles: Optional[list[dict]] = None,
) -> dict[str, float]:
    if not profiles:
        return baseline

    recalibrated = dict(baseline)
    top_key = max(
        ("prob_w1", "prob_draw", "prob_w2"),
        key=lambda key: float(recalibrated.get(key) or 0.0),
    )
    delta_1x2 = _lookup_recalibration_delta_with_fallbacks(
        profiles,
        markets=("1X2_TOP_PICK",),
        probability_pct=recalibrated.get(top_key),
        competition_name=snapshot.competition_name,
    )
    if abs(delta_1x2) >= 0.25:
        (
            recalibrated["prob_w1"],
            recalibrated["prob_draw"],
            recalibrated["prob_w2"],
        ) = _apply_top_pick_delta(
            recalibrated["prob_w1"],
            recalibrated["prob_draw"],
            recalibrated["prob_w2"],
            top_key=top_key,
            delta_pct=delta_1x2,
        )

    delta_over = _lookup_recalibration_delta_with_fallbacks(
        profiles,
        markets=("OVER_2_5",),
        probability_pct=recalibrated.get("prob_over_2_5"),
        competition_name=snapshot.competition_name,
    )
    if abs(delta_over) >= 0.25:
        recalibrated["prob_over_2_5"], recalibrated["prob_under_2_5"] = _apply_binary_delta(
            recalibrated["prob_over_2_5"],
            delta_over,
        )

    btts_environment_market = _btts_goal_environment_market(
        snapshot.market_prob_over_2_5 if snapshot.market_prob_over_2_5 is not None else recalibrated.get("prob_over_2_5"),
    )
    btts_market_candidates = (
        (btts_environment_market, "BTTS_YES")
        if btts_environment_market
        else ("BTTS_YES",)
    )
    delta_btts = _lookup_recalibration_delta_with_fallbacks(
        profiles,
        markets=tuple(str(market) for market in btts_market_candidates),
        probability_pct=recalibrated.get("prob_btts_yes"),
        competition_name=snapshot.competition_name,
    )
    if abs(delta_btts) >= 0.25:
        recalibrated["prob_btts_yes"], recalibrated["prob_btts_no"] = _apply_binary_delta(
            recalibrated["prob_btts_yes"],
            delta_btts,
        )

    return recalibrated


def build_numeric_baseline(
    data: MatchData,
    snapshot: Optional[PredictionFeatureSnapshot] = None,
    recalibration_profiles: Optional[list[dict]] = None,
) -> dict[str, float]:
    snapshot = snapshot or extract_feature_snapshot(data)

    market_w1 = snapshot.market_prob_w1
    market_draw = snapshot.market_prob_draw
    market_w2 = snapshot.market_prob_w2
    has_market_1x2 = None not in (market_w1, market_draw, market_w2)

    if has_market_1x2:
        prob_w1 = float(market_w1)
        prob_draw = float(market_draw)
        prob_w2 = float(market_w2)
    else:
        if snapshot.is_neutral_venue is True:
            prob_w1, prob_draw, prob_w2 = 36.0, 28.0, 36.0
        elif snapshot.is_team1_home is False:
            prob_w1, prob_draw, prob_w2 = 30.0, 27.0, 43.0
        else:
            prob_w1, prob_draw, prob_w2 = 43.0, 27.0, 30.0

    edge_pts = 0.0
    edge_pts += _clamp(snapshot.form_points_last5_diff or 0.0, -9.0, 9.0) * 0.9
    edge_pts += _clamp(snapshot.form_goal_diff_last5_diff or 0.0, -8.0, 8.0) * 0.75
    edge_pts += _clamp(snapshot.form_scored_avg_diff or 0.0, -2.0, 2.0) * 4.0
    edge_pts -= _clamp(snapshot.form_conceded_avg_diff or 0.0, -2.0, 2.0) * 3.0
    if snapshot.h2h_team1_win_rate is not None:
        edge_pts += _clamp(snapshot.h2h_team1_win_rate - 50.0, -30.0, 30.0) * 0.08
    edge_pts += _clamp(snapshot.xg_diff or 0.0, -15.0, 15.0) * 0.45
    edge_pts += _clamp(snapshot.xga_edge or 0.0, -15.0, 15.0) * 0.35
    edge_pts += _clamp(snapshot.rating_diff or 0.0, -2.0, 2.0) * 3.0
    edge_pts += _clamp(snapshot.possession_diff or 0.0, -20.0, 20.0) * 0.12
    edge_pts += _clamp(snapshot.standings_point_gap or 0.0, -25.0, 25.0) * 0.22
    edge_pts += _clamp(snapshot.standings_position_gap or 0.0, -12.0, 12.0) * 0.35
    edge_pts += _clamp(snapshot.rest_gap_hours or 0.0, -72.0, 72.0) * 0.05
    edge_pts += _clamp(_safe_log_ratio(snapshot.market_value_ratio), -2.0, 2.0) * 2.5
    edge_pts -= _clamp(float(snapshot.key_absence_diff or 0), -5.0, 5.0) * 2.0

    # ── Elo rating signal ──────────────────────────────────────────────────
    # Elo provides long-term team strength; blended as additional edge signal
    if snapshot.elo_team1 is not None and snapshot.elo_team2 is not None:
        elo_diff = (snapshot.elo_team1 or 1500.0) - (snapshot.elo_team2 or 1500.0)
        # 100 Elo diff ≈ 2.5 edge points (calibrated weight)
        edge_pts += _clamp(elo_diff * 0.025, -6.0, 6.0)

    if snapshot.is_neutral_venue is True:
        pass
    elif snapshot.is_team1_home is False:
        edge_pts -= 4.0
    else:
        edge_pts += 4.0

    edge_pts = _clamp(edge_pts, -18.0, 18.0)
    prob_w1 += edge_pts
    prob_w2 -= edge_pts

    draw_shift = 0.0
    if snapshot.h2h_draw_rate is not None:
        draw_shift += _clamp(snapshot.h2h_draw_rate - 27.0, -15.0, 15.0) * 0.2
    if abs(edge_pts) >= 9.0:
        draw_shift -= min(5.0, abs(edge_pts) * 0.18)
    elif abs(edge_pts) <= 4.0:
        draw_shift += 2.0
    # Elo draw probability influence (close Elo = more draws)
    if snapshot.elo_prob_draw is not None:
        elo_draw_signal = snapshot.elo_prob_draw - 25.0  # deviation from ~25% baseline
        draw_shift += _clamp(elo_draw_signal, -5.0, 5.0) * 0.3
    prob_draw += draw_shift
    prob_w1, prob_draw, prob_w2 = _normalize_triplet(prob_w1, prob_draw, prob_w2)

    # ── Elo 1X2 blend: 15% Elo + 85% form/market model ────────────────
    # Only blend when Elo data is available and we have enough signal
    if all(v is not None for v in [snapshot.elo_prob_w1, snapshot.elo_prob_draw, snapshot.elo_prob_w2]):
        elo_weight = 0.15
        prob_w1 = round(prob_w1 * (1.0 - elo_weight) + snapshot.elo_prob_w1 * elo_weight, 1)
        prob_draw = round(prob_draw * (1.0 - elo_weight) + snapshot.elo_prob_draw * elo_weight, 1)
        prob_w2 = round(prob_w2 * (1.0 - elo_weight) + snapshot.elo_prob_w2 * elo_weight, 1)
        prob_w1, prob_draw, prob_w2 = _normalize_triplet(prob_w1, prob_draw, prob_w2)

    # ── Line movement adjustment: sharp money signal ────────────────
    if snapshot.movement_score_w1 is not None and abs(snapshot.movement_score_w1) >= 2:
        # Movement score ≥2 means consistent line movement (sharp money)
        # Shift 1X2 by up to 1.5pp toward the movement direction
        move_shift = _clamp(snapshot.movement_score_w1 * 0.5, -1.5, 1.5)
        prob_w1 += move_shift
        prob_w2 -= move_shift
        prob_w1, prob_draw, prob_w2 = _normalize_triplet(prob_w1, prob_draw, prob_w2)

    # ── Expected Goals: multi-source blending ──────────────────────────────
    # Priority: real xG (Fotmob) > SOT-based proxy > raw goal averages
    ha = getattr(config, "POISSON_HOME_ADVANTAGE", 1.10)
    aw = getattr(config, "POISSON_ATTACK_WEIGHT", 0.60)
    xg_blend = getattr(config, "XG_BLEND_WEIGHT", 0.50)

    # ── Goal-based xG: venue-specific splits > overall averages ──────────
    # Example: Atalanta home scored=2.8, away scored=1.4
    # Using overall 2.1 loses a 0.7 signal — venue splits fix this
    goal_based_xg: float | None = None
    goal_based_lam1: float | None = None  # per-team lambda for Dixon-Coles
    goal_based_lam2: float | None = None
    is_home_match = snapshot.is_neutral_venue is not True and snapshot.is_team1_home is not False

    # Tier 1: Venue-specific goal splits (from standings or form)
    if is_home_match and all(v is not None for v in [
        snapshot.team1_home_scored_avg, snapshot.team1_home_conceded_avg,
        snapshot.team2_away_scored_avg, snapshot.team2_away_conceded_avg,
    ]):
        # team1 is HOME: cross-reference team1 home attack vs team2 away defence
        t1_lam = float(snapshot.team1_home_scored_avg) * aw + float(snapshot.team2_away_conceded_avg) * (1.0 - aw)
        t2_lam = float(snapshot.team2_away_scored_avg) * aw + float(snapshot.team1_home_conceded_avg) * (1.0 - aw)
        goal_based_xg = t1_lam + t2_lam
        goal_based_lam1 = t1_lam
        goal_based_lam2 = t2_lam
    elif not is_home_match and snapshot.is_team1_home is False and all(v is not None for v in [
        snapshot.team1_away_scored_avg, snapshot.team1_away_conceded_avg,
        snapshot.team2_home_scored_avg, snapshot.team2_home_conceded_avg,
    ]):
        # team1 is AWAY, team2 is HOME
        t1_lam = float(snapshot.team1_away_scored_avg) * aw + float(snapshot.team2_home_conceded_avg) * (1.0 - aw)
        t2_lam = float(snapshot.team2_home_scored_avg) * aw + float(snapshot.team1_away_conceded_avg) * (1.0 - aw)
        goal_based_xg = t1_lam + t2_lam
        goal_based_lam1 = t1_lam
        goal_based_lam2 = t2_lam

    # Tier 2: Overall averages with flat home advantage multiplier (fallback)
    if goal_based_xg is None and data.flashscore and data.flashscore.scrape_success:
        fs = data.flashscore
        if None not in (
            fs.team1_goals_scored_avg,
            fs.team1_goals_conceded_avg,
            fs.team2_goals_scored_avg,
            fs.team2_goals_conceded_avg,
        ):
            team1_xg = (float(fs.team1_goals_scored_avg) * aw) + (float(fs.team2_goals_conceded_avg) * (1.0 - aw))
            team2_xg = (float(fs.team2_goals_scored_avg) * aw) + (float(fs.team1_goals_conceded_avg) * (1.0 - aw))
            if is_home_match:
                team1_xg *= ha
            goal_based_xg = team1_xg + team2_xg

    # Real xG from Fotmob advanced stats (season averages — gold standard)
    fotmob_xg: float | None = None
    if data.advanced_stats and data.advanced_stats.scrape_success:
        s = data.advanced_stats
        if all(v is not None for v in [s.team1_metrics.xg, s.team1_metrics.xga,
                                        s.team2_metrics.xg, s.team2_metrics.xga]):
            # Cross-reference: team1 attack xG vs team2 defensive xGA
            t1_attack = float(s.team1_metrics.xg) * aw + float(s.team2_metrics.xga) * (1.0 - aw)
            t2_attack = float(s.team2_metrics.xg) * aw + float(s.team1_metrics.xga) * (1.0 - aw)
            if snapshot.is_neutral_venue is not True and snapshot.is_team1_home is not False:
                t1_attack *= ha
            fotmob_xg = t1_attack + t2_attack

    # Blend sources: fotmob_xg takes priority, then goal_based
    if fotmob_xg is not None and goal_based_xg is not None:
        model_expected_goals = goal_based_xg * (1.0 - xg_blend) + fotmob_xg * xg_blend
    elif fotmob_xg is not None:
        model_expected_goals = fotmob_xg
    elif goal_based_xg is not None:
        model_expected_goals = goal_based_xg
    else:
        model_expected_goals = 2.55  # league-average fallback

    # ── Elo xG adjustment: team strength shifts expected goals ────────────
    if snapshot.elo_xg_adjustment is not None:
        model_expected_goals += _clamp(snapshot.elo_xg_adjustment, -0.4, 0.4)

    if snapshot.team1_short_rest:
        model_expected_goals -= 0.08
    if snapshot.team2_short_rest:
        model_expected_goals -= 0.08
    if snapshot.h2h_avg_goals is not None:
        model_expected_goals += _clamp(snapshot.h2h_avg_goals - 2.6, -0.35, 0.35) * 0.35

    # ── Referee xG adjustment: lenient refs → more goals ────────────
    if snapshot.referee_goals_vs_avg is not None:
        model_expected_goals += _clamp(snapshot.referee_goals_vs_avg, -0.35, 0.35) * 0.6

    # ── Weather xG adjustment: wind/rain → fewer goals ────────────
    if snapshot.weather_wind_kmh is not None and snapshot.weather_wind_kmh >= 30.0:
        wind_penalty = min((snapshot.weather_wind_kmh - 30.0) / 40.0, 0.3) * 0.5
        model_expected_goals -= wind_penalty
    if snapshot.weather_precipitation_mm is not None and snapshot.weather_precipitation_mm >= 2.0:
        rain_penalty = min(snapshot.weather_precipitation_mm / 10.0, 0.25) * 0.4
        model_expected_goals -= rain_penalty

    # ── Line movement O/U adjustment: sharp money on Over/Under ────
    if snapshot.movement_score_over is not None and abs(snapshot.movement_score_over) >= 2:
        over_shift = _clamp(snapshot.movement_score_over * 0.04, -0.12, 0.12)
        model_expected_goals += over_shift

    model_expected_goals = round(_clamp(model_expected_goals, 1.6, 4.2), 2)

    # ── Dixon-Coles Poisson matrix for O/U and BTTS ────────────────────────
    # Split model_expected_goals into per-team lambdas.
    # Priority: venue-specific goal splits > Fotmob xG ratio > overall ratio > Elo > flat
    if goal_based_lam1 is not None and goal_based_lam2 is not None:
        # Tier 1: Direct venue-specific lambdas from home/away goal splits
        # Scale to match model_expected_goals (keeps blend/adjustments)
        raw_total = goal_based_lam1 + goal_based_lam2
        if raw_total > 0.1:
            ratio1 = goal_based_lam1 / raw_total
        else:
            ratio1 = 0.5
        lam1 = model_expected_goals * ratio1
        lam2 = model_expected_goals * (1.0 - ratio1)
    elif fotmob_xg is not None and data.advanced_stats and data.advanced_stats.scrape_success:
        # Tier 2: Fotmob xG ratio
        s = data.advanced_stats
        if all(v is not None for v in [s.team1_metrics.xg, s.team2_metrics.xg]):
            ratio1 = float(s.team1_metrics.xg) / max(float(s.team1_metrics.xg) + float(s.team2_metrics.xg), 0.5)
        else:
            ratio1 = 0.5
        lam1 = model_expected_goals * ratio1
        lam2 = model_expected_goals * (1.0 - ratio1)
    elif goal_based_xg is not None and data.flashscore and data.flashscore.scrape_success:
        # Tier 3: Overall goal averages ratio
        fs = data.flashscore
        total = (float(fs.team1_goals_scored_avg or 0) + float(fs.team2_goals_scored_avg or 0)) or 2.55
        ratio1 = float(fs.team1_goals_scored_avg or 1.0) / total
        lam1 = model_expected_goals * ratio1
        lam2 = model_expected_goals * (1.0 - ratio1)
    elif snapshot.elo_home_rating is not None and snapshot.elo_away_rating is not None:
        # Tier 4: Elo home/away rating ratio
        elo_h = snapshot.elo_home_rating
        elo_a = snapshot.elo_away_rating
        elo_ratio = 1.0 / (1.0 + 10.0 ** ((elo_a - elo_h) / 400.0))
        elo_ratio = _clamp(elo_ratio, 0.30, 0.70)
        lam1 = model_expected_goals * elo_ratio
        lam2 = model_expected_goals * (1.0 - elo_ratio)
    else:
        # Tier 5: flat home bias fallback
        lam1 = model_expected_goals * 0.53
        lam2 = model_expected_goals * 0.47

    league_rho = _get_league_rho(snapshot.competition_name)
    dc_matrix = _build_dixon_coles_matrix(lam1, lam2, league_rho)

    # O/U 2.5 from Dixon-Coles matrix (more accurate than simple Poisson)
    model_over = _matrix_over_probability(dc_matrix, 2.5)
    if snapshot.h2h_avg_goals is not None:
        model_over += (snapshot.h2h_avg_goals - 2.6) * 5.0
    if snapshot.market_prob_over_2_5 is not None:
        model_over = (0.65 * snapshot.market_prob_over_2_5) + (0.35 * model_over)
    model_over, model_under = _normalize_pair(model_over, 100.0 - model_over)

    market_over_1_5 = market_under_1_5 = None
    market_over_3_5 = market_under_3_5 = None
    if data.odds and data.odds.scrape_success:
        market_over_1_5, market_under_1_5 = _market_pair(data.odds.over_1_5_odds, data.odds.under_1_5_odds)
        market_over_3_5, market_under_3_5 = _market_pair(data.odds.over_3_5_odds, data.odds.under_3_5_odds)

    model_over_1_5 = _matrix_over_probability(dc_matrix, 1.5)
    if market_over_1_5 is not None:
        model_over_1_5 = round((0.7 * market_over_1_5) + (0.3 * model_over_1_5), 1)
    model_over_1_5, model_under_1_5 = _normalize_pair(model_over_1_5, 100.0 - model_over_1_5)

    model_over_3_5 = _matrix_over_probability(dc_matrix, 3.5)
    if market_over_3_5 is not None:
        model_over_3_5 = round((0.7 * market_over_3_5) + (0.3 * model_over_3_5), 1)
    model_over_3_5, model_under_3_5 = _normalize_pair(model_over_3_5, 100.0 - model_over_3_5)

    # BTTS from Dixon-Coles matrix (captures score correlation)
    btts_yes = _matrix_btts_probability(dc_matrix)
    if data.flashscore and data.flashscore.scrape_success:
        fs = data.flashscore
        if fs.team1_goals_scored_avg is not None and fs.team2_goals_scored_avg is not None:
            btts_yes += _clamp(min(fs.team1_goals_scored_avg, fs.team2_goals_scored_avg) - 0.9, -0.6, 1.5) * 5.0
        if fs.team1_goals_conceded_avg is not None and fs.team2_goals_conceded_avg is not None:
            btts_yes += _clamp(min(fs.team1_goals_conceded_avg, fs.team2_goals_conceded_avg) - 0.8, -0.8, 1.5) * 4.0
    if snapshot.market_prob_btts_yes is not None:
        btts_yes = (0.65 * snapshot.market_prob_btts_yes) + (0.35 * btts_yes)
    btts_yes, btts_no = _normalize_pair(btts_yes, 100.0 - btts_yes)

    baseline = {
        "prob_w1": prob_w1,
        "prob_draw": prob_draw,
        "prob_w2": prob_w2,
        "prob_over_2_5": model_over,
        "prob_under_2_5": model_under,
        "prob_over_1_5": model_over_1_5,
        "prob_under_1_5": model_under_1_5,
        "prob_over_3_5": model_over_3_5,
        "prob_under_3_5": model_under_3_5,
        "prob_btts_yes": btts_yes,
        "prob_btts_no": btts_no,
        "expected_goals": model_expected_goals,
    }
    return apply_numeric_recalibration(baseline, snapshot, recalibration_profiles)


def assign_llm_probabilities(report_dict: dict, snapshot: PredictionFeatureSnapshot) -> None:
    snapshot.llm_prob_w1 = report_dict.get("prob_w1")
    snapshot.llm_prob_draw = report_dict.get("prob_draw")
    snapshot.llm_prob_w2 = report_dict.get("prob_w2")
    snapshot.llm_prob_over_2_5 = report_dict.get("prob_over_2_5")
    snapshot.llm_prob_under_2_5 = report_dict.get("prob_under_2_5")
    snapshot.llm_prob_btts_yes = report_dict.get("prob_btts_yes")
    snapshot.llm_prob_btts_no = report_dict.get("prob_btts_no")


def _blend_alpha(snapshot: PredictionFeatureSnapshot) -> float:
    # NOTE: Agents now see the numeric baseline in their prompt, so they
    # already account for the model.  The post-hoc blend is a safety net
    # to prevent wild departures — not the primary integration mechanism.
    # Reduced from 0.12-0.50 → 0.06-0.25 to avoid double-counting.
    alpha = 0.06 + (snapshot.data_completeness * 0.09)
    if None not in (snapshot.market_prob_w1, snapshot.market_prob_draw, snapshot.market_prob_w2):
        alpha += 0.06
    if snapshot.market_prob_over_2_5 is not None:
        alpha += 0.02
    if snapshot.xg_diff is not None:
        alpha += 0.02
    if snapshot.standings_point_gap is not None:
        alpha += 0.01
    return round(_clamp(alpha, 0.06, 0.25), 3)


def blend_report_with_numeric_baseline(
    report_dict: dict,
    data: MatchData,
    snapshot: PredictionFeatureSnapshot,
    recalibration_profiles: Optional[list[dict]] = None,
    numeric_baseline_override: Optional[dict[str, float]] = None,
) -> dict[str, float]:
    assign_llm_probabilities(report_dict, snapshot)
    model_probs = numeric_baseline_override or build_numeric_baseline(data, snapshot, recalibration_profiles)

    snapshot.model_prob_w1 = model_probs["prob_w1"]
    snapshot.model_prob_draw = model_probs["prob_draw"]
    snapshot.model_prob_w2 = model_probs["prob_w2"]
    snapshot.model_prob_over_2_5 = model_probs["prob_over_2_5"]
    snapshot.model_prob_under_2_5 = model_probs["prob_under_2_5"]
    snapshot.model_prob_btts_yes = model_probs["prob_btts_yes"]
    snapshot.model_prob_btts_no = model_probs["prob_btts_no"]
    snapshot.model_expected_goals = model_probs["expected_goals"]

    llm_triplet = [
        float(report_dict.get("prob_w1") or 0.0),
        float(report_dict.get("prob_draw") or 0.0),
        float(report_dict.get("prob_w2") or 0.0),
    ]
    snapshot.llm_model_gap_pct = round(
        max(abs(llm_value - model_value) for llm_value, model_value in zip(
            llm_triplet,
            [model_probs["prob_w1"], model_probs["prob_draw"], model_probs["prob_w2"]],
        )),
        2,
    )

    alpha = _blend_alpha(snapshot)
    if snapshot.llm_model_gap_pct >= 20.0 and snapshot.market_prob_w1 is not None:
        # Agents saw the baseline but still deviated >20pp — increase blend weight
        # as a safety guardrail, but cap lower since agents already had the data.
        alpha = round(min(alpha + 0.05, 0.35), 3)
    snapshot.blend_alpha = alpha

    for keys in (
        ("prob_w1", "prob_draw", "prob_w2"),
        ("prob_over_2_5", "prob_under_2_5"),
        ("prob_btts_yes", "prob_btts_no"),
    ):
        for key in keys:
            llm_value = float(report_dict.get(key) or 0.0)
            model_value = float(model_probs.get(key) or 0.0)
            report_dict[key] = round(((1.0 - alpha) * llm_value) + (alpha * model_value), 1)

    report_dict["expected_goals"] = round(
        ((1.0 - alpha) * float(report_dict.get("expected_goals") or 0.0))
        + (alpha * float(model_probs["expected_goals"])),
        2,
    )
    return model_probs


def derive_expanded_market_probabilities(report_dict: dict) -> dict[str, float]:
    derived: dict[str, float] = {}

    expected_goals = float(report_dict.get("expected_goals") or 0.0)
    if expected_goals > 0:
        over_1_5 = _poisson_over_probability(expected_goals, 1.5)
        over_1_5, under_1_5 = _normalize_pair(over_1_5, 100.0 - over_1_5)
        over_3_5 = _poisson_over_probability(expected_goals, 3.5)
        over_3_5, under_3_5 = _normalize_pair(over_3_5, 100.0 - over_3_5)
        derived.update(
            {
                "prob_over_1_5": over_1_5,
                "prob_under_1_5": under_1_5,
                "prob_over_3_5": over_3_5,
                "prob_under_3_5": under_3_5,
            }
        )

    prob_w1 = report_dict.get("prob_w1")
    prob_draw = report_dict.get("prob_draw")
    prob_w2 = report_dict.get("prob_w2")
    if None not in (prob_w1, prob_draw, prob_w2):
        p1 = float(prob_w1)
        pd = float(prob_draw)
        p2 = float(prob_w2)
        no_draw_total = p1 + p2
        if no_draw_total > 0:
            dnb_team1 = round(p1 * 100.0 / no_draw_total, 1)
            dnb_team2 = round(p2 * 100.0 / no_draw_total, 1)
            dnb_team1, dnb_team2 = _normalize_pair(dnb_team1, dnb_team2)
            derived["prob_dnb_team1"] = dnb_team1
            derived["prob_dnb_team2"] = dnb_team2
        derived["prob_double_chance_1x"] = round(p1 + pd, 1)
        derived["prob_double_chance_12"] = round(p1 + p2, 1)
        derived["prob_double_chance_x2"] = round(pd + p2, 1)

    return derived


def build_market_candidates(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> list[dict[str, object]]:
    derived_probs = derive_expanded_market_probabilities(report_dict)
    candidates: list[dict[str, object]] = []

    def _candidate_confidence(probability: float, edge: float, odds: Optional[float]) -> float:
        longshot_penalty = max(0.0, (float(odds or 2.4) - 2.6)) * 6.0
        return round(
            _clamp(
                20.0 + (probability * 0.65) + (max(edge, 0.0) * 3.0) - longshot_penalty,
                48.0,
                82.0,
            ),
            1,
        )

    def _family_bonus(family: str) -> float:
        return float(_market_family_policy(family)["score_bias"])

    def _odds_bonus(odds: Optional[float]) -> float:
        if odds is None:
            return 0.0
        if 1.4 <= odds <= 2.4:
            return 1.2
        if 1.2 <= odds <= 3.2:
            return 0.4
        return -0.8

    def _quality_score_bonus(family: str, quality_profile: Optional[dict]) -> float:
        if not quality_profile:
            return 0.0
        if _core_market_family(family) != family:
            return 0.0
        sample_size = int(quality_profile.get("sample_size") or 0)
        trust_score = float(quality_profile.get("trust_score") or 0.0)
        if sample_size < 120:
            return 0.0
        if trust_score > 1.0:
            return round(min(1.2, trust_score * 0.28), 2)
        if trust_score < -1.0:
            return round(max(-1.0, trust_score * 0.22), 2)
        return 0.0

    def _quality_confidence_bonus(family: str, quality_profile: Optional[dict]) -> float:
        if not quality_profile:
            return 0.0
        if _core_market_family(family) != family:
            return 0.0
        sample_size = int(quality_profile.get("sample_size") or 0)
        trust_score = float(quality_profile.get("trust_score") or 0.0)
        if sample_size < 250:
            return 0.0
        if trust_score > 1.25:
            return min(2.0, trust_score * 0.45)
        if trust_score < -1.25:
            return max(-1.5, trust_score * 0.35)
        return 0.0

    def _candidate_market_identity(label: str, family: str) -> tuple[Optional[str], Optional[str]]:
        normalized = _normalize_pick_label(label)
        team1 = snapshot.team1.strip().lower()
        team2 = snapshot.team2.strip().lower()

        if family == "1x2":
            if normalized == "draw" or "draw" in normalized:
                return "1X2", "DRAW"
            if team1 and team1 in normalized:
                return "1X2", "TEAM1"
            if team2 and team2 in normalized:
                return "1X2", "TEAM2"
        elif family == "totals":
            if "over 2.5" in normalized:
                return "TOTALS_2_5", "OVER"
            if "under 2.5" in normalized:
                return "TOTALS_2_5", "UNDER"
        elif family == "total_1_5":
            if "over 1.5" in normalized:
                return "TOTALS_1_5", "OVER"
            if "under 1.5" in normalized:
                return "TOTALS_1_5", "UNDER"
        elif family == "total_3_5":
            if "over 3.5" in normalized:
                return "TOTALS_3_5", "OVER"
            if "under 3.5" in normalized:
                return "TOTALS_3_5", "UNDER"
        elif family == "btts":
            if "yes" in normalized:
                return "BTTS", "YES"
            if "no" in normalized:
                return "BTTS", "NO"
        elif family == "dnb":
            if team1 and team1 in normalized:
                return "DNB", "TEAM1"
            if team2 and team2 in normalized:
                return "DNB", "TEAM2"
        elif family == "double_chance":
            if "1x" in normalized or (team1 and team1 in normalized and "draw" in normalized):
                return "DOUBLE_CHANCE", "1X"
            if "12" in normalized or "no draw" in normalized:
                return "DOUBLE_CHANCE", "12"
            if "x2" in normalized or (team2 and team2 in normalized and "draw" in normalized):
                return "DOUBLE_CHANCE", "X2"
        return None, None

    def _add_candidate(
        label: str,
        probability: Optional[float],
        market_probability: Optional[float],
        *,
        odds: Optional[float],
        family: str,
    ) -> None:
        if probability is None or market_probability is None:
            return
        probability = round(float(probability), 1)
        market_probability = round(float(market_probability), 1)
        edge = round(probability - market_probability, 2)
        odds = odds or _implied_odds_from_pct(market_probability)
        market_code, selection_code = _candidate_market_identity(label, family)
        quality_profile = _market_family_quality_profile(family, family_quality_profiles)
        quality_score_bonus = _quality_score_bonus(family, quality_profile)
        score = round(
            (edge * 3.0)
            + ((probability - 50.0) * 0.14)
            + (snapshot.data_completeness * 8.0)
            + _family_bonus(family)
            + _odds_bonus(odds),
            2,
        )
        score = round(
            score + quality_score_bonus,
            2,
        )
        candidates.append(
            {
                "label": label,
                "probability": probability,
                "market_probability": market_probability,
                "edge": edge,
                "odds": odds,
                "family": family,
                "market_code": market_code,
                "selection_code": selection_code,
                "score": score,
                "confidence_pct": round(
                    _clamp(
                        _candidate_confidence(probability, edge, odds)
                        + _quality_confidence_bonus(family, quality_profile),
                        48.0,
                        84.0,
                    ),
                    1,
                ),
                "eligible_for_top_pick": _candidate_passes_family_gate(
                    {
                        "family": family,
                        "edge": edge,
                        "probability": probability,
                    },
                    snapshot,
                    purpose="top_pick",
                    family_quality_profiles=family_quality_profiles,
                ),
                "eligible_for_action_pick": _candidate_passes_family_gate(
                    {
                        "family": family,
                        "edge": edge,
                        "probability": probability,
                    },
                    snapshot,
                    purpose="action",
                    family_quality_profiles=family_quality_profiles,
                ),
                "eligible_for_value": _candidate_passes_family_gate(
                    {
                        "family": family,
                        "edge": edge,
                        "probability": probability,
                    },
                    snapshot,
                    purpose="value",
                    family_quality_profiles=family_quality_profiles,
                ),
                "family_trust_score": (
                    float(quality_profile.get("trust_score"))
                    if quality_profile and quality_profile.get("trust_score") is not None else None
                ),
                "family_sample_size": (
                    int(quality_profile.get("sample_size"))
                    if quality_profile and quality_profile.get("sample_size") is not None else None
                ),
                "family_quality_score_bonus": quality_score_bonus,
                "family_borderline_rerank_bonus": 0.0,
            }
        )

    market_over_1_5 = market_under_1_5 = None
    market_over_3_5 = market_under_3_5 = None
    if odds_data:
        market_over_1_5, market_under_1_5 = _market_pair(odds_data.over_1_5_odds, odds_data.under_1_5_odds)
        market_over_3_5, market_under_3_5 = _market_pair(odds_data.over_3_5_odds, odds_data.under_3_5_odds)

    _add_candidate(snapshot.team1, report_dict.get("prob_w1"), snapshot.market_prob_w1, odds=odds_data.team1_win_odds if odds_data else None, family="1x2")
    _add_candidate("Draw", report_dict.get("prob_draw"), snapshot.market_prob_draw, odds=odds_data.draw_odds if odds_data else None, family="1x2")
    _add_candidate(snapshot.team2, report_dict.get("prob_w2"), snapshot.market_prob_w2, odds=odds_data.team2_win_odds if odds_data else None, family="1x2")

    _add_candidate("Over 1.5", report_dict.get("prob_over_1_5", derived_probs.get("prob_over_1_5")), market_over_1_5, odds=odds_data.over_1_5_odds if odds_data else None, family="total_1_5")
    _add_candidate("Under 1.5", report_dict.get("prob_under_1_5", derived_probs.get("prob_under_1_5")), market_under_1_5, odds=odds_data.under_1_5_odds if odds_data else None, family="total_1_5")
    _add_candidate("Over 2.5", report_dict.get("prob_over_2_5"), snapshot.market_prob_over_2_5, odds=odds_data.over_2_5_odds if odds_data else None, family="totals")
    _add_candidate("Under 2.5", report_dict.get("prob_under_2_5"), snapshot.market_prob_under_2_5, odds=odds_data.under_2_5_odds if odds_data else None, family="totals")
    _add_candidate("Over 3.5", report_dict.get("prob_over_3_5", derived_probs.get("prob_over_3_5")), market_over_3_5, odds=odds_data.over_3_5_odds if odds_data else None, family="total_3_5")
    _add_candidate("Under 3.5", report_dict.get("prob_under_3_5", derived_probs.get("prob_under_3_5")), market_under_3_5, odds=odds_data.under_3_5_odds if odds_data else None, family="total_3_5")

    _add_candidate("BTTS Yes", report_dict.get("prob_btts_yes"), snapshot.market_prob_btts_yes, odds=odds_data.btts_yes_odds if odds_data else None, family="btts")
    _add_candidate("BTTS No", report_dict.get("prob_btts_no"), snapshot.market_prob_btts_no, odds=odds_data.btts_no_odds if odds_data else None, family="btts")

    market_prob_dnb_team1 = market_prob_dnb_team2 = None
    if None not in (snapshot.market_prob_w1, snapshot.market_prob_w2):
        market_prob_dnb_team1, market_prob_dnb_team2 = _normalize_pair(
            float(snapshot.market_prob_w1),
            float(snapshot.market_prob_w2),
        )
    _add_candidate(
        f"{snapshot.team1} Draw No Bet",
        report_dict.get("prob_dnb_team1", derived_probs.get("prob_dnb_team1")),
        market_prob_dnb_team1,
        odds=odds_data.dnb_team1_odds if odds_data else None,
        family="dnb",
    )
    _add_candidate(
        f"{snapshot.team2} Draw No Bet",
        report_dict.get("prob_dnb_team2", derived_probs.get("prob_dnb_team2")),
        market_prob_dnb_team2,
        odds=odds_data.dnb_team2_odds if odds_data else None,
        family="dnb",
    )

    if None not in (snapshot.market_prob_w1, snapshot.market_prob_draw, snapshot.market_prob_w2):
        mp1 = float(snapshot.market_prob_w1)
        mpd = float(snapshot.market_prob_draw)
        mp2 = float(snapshot.market_prob_w2)
        _add_candidate(
            f"{snapshot.team1} or Draw (1X)",
            report_dict.get("prob_double_chance_1x", derived_probs.get("prob_double_chance_1x")),
            round(mp1 + mpd, 1),
            odds=odds_data.double_chance_1x_odds if odds_data else None,
            family="double_chance",
        )
        _add_candidate(
            "No Draw (12)",
            report_dict.get("prob_double_chance_12", derived_probs.get("prob_double_chance_12")),
            round(mp1 + mp2, 1),
            odds=odds_data.double_chance_12_odds if odds_data else None,
            family="double_chance",
        )
        _add_candidate(
            f"Draw or {snapshot.team2} (X2)",
            report_dict.get("prob_double_chance_x2", derived_probs.get("prob_double_chance_x2")),
            round(mpd + mp2, 1),
            odds=odds_data.double_chance_x2_odds if odds_data else None,
            family="double_chance",
        )

    _apply_btts_borderline_rerank(candidates, snapshot)
    candidates.sort(
        key=lambda candidate: (
            float(candidate.get("score") or 0.0),
            float(candidate.get("edge") or -999.0),
            float(candidate.get("probability") or 0.0),
        ),
        reverse=True,
    )
    return candidates


def _normalize_pick_label(label: str) -> str:
    normalized = label.strip().lower()
    for prefix in ("speculative lean:", "action mode pick:", "speculative:", "lean:"):
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def choose_action_pick(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> Optional[dict[str, object]]:
    candidates = build_market_candidates(report_dict, snapshot, odds_data, family_quality_profiles)
    viable = [
        candidate
        for candidate in candidates
        if bool(candidate.get("eligible_for_action_pick"))
    ]
    if not viable:
        return None
    top_candidate = viable[0]
    top_family = str(top_candidate.get("family") or "")
    if _core_market_family(top_family) == top_family:
        return top_candidate

    core_candidates = [
        candidate
        for candidate in viable
        if _core_market_family(str(candidate.get("family") or ""))
        == str(candidate.get("family") or "").strip().lower()
    ]
    if not core_candidates:
        return top_candidate

    preferred_core = core_candidates[0]
    score_gap = float(top_candidate.get("score") or 0.0) - float(preferred_core.get("score") or 0.0)
    if score_gap > 0.9:
        return top_candidate

    edge_gap = float(top_candidate.get("edge") or 0.0) - float(preferred_core.get("edge") or 0.0)
    if edge_gap > 0.5:
        return top_candidate

    core_trust = float(preferred_core.get("family_trust_score") or 0.0)
    core_sample = int(preferred_core.get("family_sample_size") or 0)
    top_trust = float(top_candidate.get("family_trust_score") or 0.0)
    rerank_bonus = float(preferred_core.get("family_borderline_rerank_bonus") or 0.0)
    quality_bonus = float(preferred_core.get("family_quality_score_bonus") or 0.0)
    confidence_gap = float(top_candidate.get("confidence_pct") or 0.0) - float(preferred_core.get("confidence_pct") or 0.0)
    max_confidence_gap = 3.0
    if core_trust >= 2.0 and top_trust < 0.0:
        max_confidence_gap = 6.0
    if confidence_gap > max_confidence_gap:
        return top_candidate

    if core_sample < 250:
        return top_candidate
    if rerank_bonus <= 0.0 and quality_bonus <= 0.0 and core_trust < 1.25:
        return top_candidate
    if rerank_bonus <= 0.0 and core_trust <= top_trust + 0.5:
        return top_candidate

    return preferred_core


def _prefer_core_top_pick_candidate(
    top_candidate: Optional[dict[str, object]],
    candidates: list[dict[str, object]],
) -> Optional[dict[str, object]]:
    if top_candidate is None:
        return None

    top_family = str(top_candidate.get("family") or "")
    if _core_market_family(top_family) == top_family:
        return top_candidate

    core_candidates = [
        candidate
        for candidate in candidates
        if _core_market_family(str(candidate.get("family") or ""))
        == str(candidate.get("family") or "").strip().lower()
        and bool(candidate.get("eligible_for_top_pick"))
    ]
    if not core_candidates:
        return top_candidate

    preferred_core = core_candidates[0]
    score_gap = float(top_candidate.get("score") or 0.0) - float(preferred_core.get("score") or 0.0)
    if score_gap > 0.85:
        return top_candidate

    edge_gap = float(top_candidate.get("edge") or 0.0) - float(preferred_core.get("edge") or 0.0)
    if edge_gap > 0.45:
        return top_candidate

    top_trust = float(top_candidate.get("family_trust_score") or 0.0)
    core_trust = float(preferred_core.get("family_trust_score") or 0.0)
    core_sample = int(preferred_core.get("family_sample_size") or 0)
    rerank_bonus = float(preferred_core.get("family_borderline_rerank_bonus") or 0.0)
    quality_bonus = float(preferred_core.get("family_quality_score_bonus") or 0.0)

    if core_sample < 250:
        return top_candidate
    if rerank_bonus <= 0.0 and quality_bonus <= 0.0 and core_trust < 1.5:
        return top_candidate
    if rerank_bonus <= 0.0 and core_trust <= top_trust + 0.6:
        return top_candidate

    confidence_gap = float(top_candidate.get("confidence_pct") or 0.0) - float(preferred_core.get("confidence_pct") or 0.0)
    max_confidence_gap = 4.0
    if core_trust >= 2.0 and top_trust < 0.0:
        max_confidence_gap = 6.0
    if confidence_gap > max_confidence_gap:
        return top_candidate

    return preferred_core


def _find_matching_candidate(
    recommendation: str,
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> Optional[dict[str, object]]:
    normalized_label = _normalize_pick_label(recommendation)
    candidates = build_market_candidates(report_dict, snapshot, odds_data, family_quality_profiles)

    for candidate in candidates:
        candidate_label = str(candidate.get("label") or "").strip().lower()
        if candidate_label == normalized_label:
            return candidate

    partial_matches: list[dict[str, object]] = []
    for candidate in candidates:
        candidate_label = str(candidate.get("label") or "").strip().lower()
        if candidate_label and (
            candidate_label in normalized_label or normalized_label in candidate_label
        ):
            partial_matches.append(candidate)

    if not partial_matches:
        return None

    partial_matches.sort(
        key=lambda candidate: len(str(candidate.get("label") or "").strip()),
        reverse=True,
    )
    return partial_matches[0]


def apply_top_pick_identity(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
) -> None:
    recommendation = str(report_dict.get("top_recommendation") or "").strip()
    if not recommendation:
        report_dict["top_market_code"] = None
        report_dict["top_selection_code"] = None
        return

    if recommendation.lower().startswith("no bet"):
        report_dict["top_market_code"] = "NO_BET"
        report_dict["top_selection_code"] = "NO_BET"
        return

    candidate = _find_matching_candidate(recommendation, report_dict, snapshot, odds_data)
    report_dict["top_market_code"] = candidate.get("market_code") if candidate else None
    report_dict["top_selection_code"] = candidate.get("selection_code") if candidate else None


def estimate_market_edge_for_top_pick(
    top_rec: str,
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
) -> Optional[float]:
    rec_lower = top_rec.lower()

    if "no bet" in rec_lower:
        return 0.0

    candidates: list[tuple[Optional[float], Optional[float]]] = []
    if any(token in rec_lower for token in ("over 2.5", "o2.5", "тб 2.5")):
        candidates.append((report_dict.get("prob_over_2_5"), snapshot.market_prob_over_2_5))
    elif any(token in rec_lower for token in ("under 2.5", "u2.5", "тм 2.5")):
        candidates.append((report_dict.get("prob_under_2_5"), snapshot.market_prob_under_2_5))
    elif "btts" in rec_lower or "обе забьют" in rec_lower:
        if any(token in rec_lower for token in ("yes", "да")):
            candidates.append((report_dict.get("prob_btts_yes"), snapshot.market_prob_btts_yes))
        elif any(token in rec_lower for token in ("no", "нет")):
            candidates.append((report_dict.get("prob_btts_no"), snapshot.market_prob_btts_no))
    else:
        team1_lower = snapshot.team1.lower()
        team2_lower = snapshot.team2.lower()
        if team1_lower in rec_lower:
            candidates.append((report_dict.get("prob_w1"), snapshot.market_prob_w1))
        if team2_lower in rec_lower:
            candidates.append((report_dict.get("prob_w2"), snapshot.market_prob_w2))
        if "draw" in rec_lower or "ничья" in rec_lower:
            candidates.append((report_dict.get("prob_draw"), snapshot.market_prob_draw))

    edges = [
        float(model_prob) - float(market_prob)
        for model_prob, market_prob in candidates
        if model_prob is not None and market_prob is not None
    ]
    if not edges:
        return None
    return round(max(edges), 2)


def apply_no_bet_policy(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
) -> None:
    top_rec = str(report_dict.get("top_recommendation") or "").strip()
    if not top_rec:
        return

    if "insufficient data" in top_rec.lower():
        snapshot.no_bet_triggered = True
        snapshot.top_edge_pct = 0.0
        return

    snapshot.top_edge_pct = estimate_market_edge_for_top_pick(top_rec, report_dict, snapshot)
    low_confidence = float(report_dict.get("top_confidence_pct") or 0.0) < 57.0
    weak_market_edge = snapshot.top_edge_pct is not None and snapshot.top_edge_pct < 3.0
    severe_market_edge = snapshot.top_edge_pct is not None and snapshot.top_edge_pct < 1.5
    low_data = snapshot.data_completeness < 0.5
    severe_low_data = snapshot.data_completeness < 0.34
    high_disagreement = float(snapshot.llm_model_gap_pct or 0.0) >= 18.0
    too_many_warnings = len(report_dict.get("data_warnings") or []) >= 3

    reasons: list[str] = []
    if weak_market_edge:
        reasons.append("market edge too small")
    if low_confidence:
        reasons.append("confidence too low")
    if high_disagreement:
        reasons.append("model disagreement too high")
    if low_data:
        reasons.append("data completeness too low")
    if too_many_warnings:
        reasons.append("too many missing signals")

    should_no_bet = (
        severe_market_edge
        or severe_low_data
        or (high_disagreement and (snapshot.top_edge_pct is None or snapshot.top_edge_pct < 2.0))
        or len(reasons) >= 3
    )

    if not should_no_bet:
        snapshot.no_bet_triggered = False
        return

    primary_reason = reasons[0] if reasons else "insufficient edge"
    report_dict["top_recommendation"] = f"No bet - {primary_reason}"
    report_dict["top_recommendation_odds"] = None
    report_dict["top_confidence_pct"] = min(float(report_dict.get("top_confidence_pct") or 0.0), 52.0)
    report_dict["risk_level"] = "HIGH"

    warnings = list(report_dict.get("data_warnings") or [])
    if weak_market_family and family_reason and family_reason not in warnings:
        warnings.append(family_reason)
    warning_text = "No-bet filter triggered: " + ", ".join(reasons[:3] or [primary_reason])
    if warning_text not in warnings:
        warnings.append(warning_text)
    report_dict["data_warnings"] = warnings

    summary = str(report_dict.get("analysis_summary") or "").strip()
    no_bet_sentence = f"No-bet filter triggered because {', '.join(reasons[:2] or [primary_reason])}."
    if no_bet_sentence not in summary:
        summary = f"{summary} {no_bet_sentence}".strip()
    report_dict["analysis_summary"] = summary
    snapshot.no_bet_triggered = True


def estimate_market_edge_for_recommendation(
    recommendation: str,
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
) -> Optional[float]:
    rec_lower = recommendation.lower()
    if "no bet" in rec_lower:
        return 0.0

    candidate = _find_matching_candidate(recommendation, report_dict, snapshot, odds_data)
    if candidate is not None:
        edge = candidate.get("edge")
        return round(float(edge), 2) if edge is not None else None

    return estimate_market_edge_for_top_pick(recommendation, report_dict, snapshot, odds_data)


def build_value_bets(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> list[str]:
    value_bets: list[str] = []
    for candidate in build_value_candidate_snapshot(
        report_dict,
        snapshot,
        odds_data,
        family_quality_profiles,
    ):
        value_bets.append(
            f"{candidate.label}: {candidate.probability:.0f}% vs {candidate.market_probability:.0f}% buk. (+{candidate.edge:.0f}% edge)"
        )
    return value_bets[:5]


def build_value_candidate_snapshot(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> list[ValueCandidateSnapshot]:
    snapshots: list[ValueCandidateSnapshot] = []
    for candidate in build_market_candidates(report_dict, snapshot, odds_data, family_quality_profiles):
        edge = float(candidate.get("edge") or -999.0)
        if not bool(candidate.get("eligible_for_value")):
            continue
        snapshots.append(
            ValueCandidateSnapshot(
                label=str(candidate.get("label") or ""),
                family=str(candidate.get("family") or ""),
                market_code=candidate.get("market_code"),
                selection_code=candidate.get("selection_code"),
                probability=float(candidate.get("probability") or 0.0),
                market_probability=float(candidate.get("market_probability") or 0.0),
                edge=edge,
                odds=float(candidate.get("odds")) if candidate.get("odds") is not None else None,
                confidence_pct=(
                    float(candidate.get("confidence_pct"))
                    if candidate.get("confidence_pct") is not None else None
                ),
            )
        )
    return snapshots[:8]


def build_top_pick_context(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> Optional[str]:
    recommendation = str(report_dict.get("top_recommendation") or "").strip()
    if not recommendation:
        return None

    confidence = float(report_dict.get("top_confidence_pct") or 0.0)
    if recommendation.lower().startswith("no bet"):
        edge = snapshot.top_edge_pct
        edge_text = "n/a" if edge is None else f"{edge:+.1f}%"
        return (
            f"Фильтр оставил матч без ставки: edge {edge_text}, confidence {confidence:.0f}%, "
            f"данные {snapshot.data_completeness * 100:.0f}%."
        )

    candidate = _find_matching_candidate(
        recommendation,
        report_dict,
        snapshot,
        odds_data,
        family_quality_profiles,
    )
    if candidate is None:
        return None

    label = str(candidate.get("label") or recommendation)
    probability = float(candidate.get("probability") or 0.0)
    market_probability = float(candidate.get("market_probability") or 0.0)
    edge = float(candidate.get("edge") or 0.0)
    family = str(candidate.get("family") or "")
    synthetic_odds = False
    if family == "dnb":
        synthetic_odds = bool(odds_data and "draw no bet" in label.lower() and not (
            (snapshot.team1.lower() in label.lower() and odds_data.dnb_team1_odds)
            or (snapshot.team2.lower() in label.lower() and odds_data.dnb_team2_odds)
        ))
    elif family == "double_chance":
        synthetic_odds = bool(odds_data and not (
            ("1x" in label.lower() and odds_data.double_chance_1x_odds)
            or ("12" in label.lower() and odds_data.double_chance_12_odds)
            or ("x2" in label.lower() and odds_data.double_chance_x2_odds)
        ))

    context = (
        f"Выбор рынка: {label} — наша оценка {probability:.0f}% против рынка {market_probability:.0f}% "
        f"(edge {edge:+.1f}%), confidence {confidence:.0f}%."
    )
    if any("Action mode pick" in warning for warning in (report_dict.get("data_warnings") or [])):
        context += " Режим action разрешил тонкое преимущество, поэтому размер ставки должен быть минимальным."
    if any("Top pick normalized to stronger core market" in warning for warning in (report_dict.get("data_warnings") or [])):
        context += " Финальный выбор был смещён в сторону более надёжного core-рынка, потому что он шёл почти вровень с derived-идеей, но опирался на более сильный исторический сегмент."
    if synthetic_odds:
        context += " Коэффициент выведен из 1X2 консенсуса, а не из отдельного прямого рынка."
    trust_score = candidate.get("family_trust_score")
    family_sample_size = candidate.get("family_sample_size")
    if trust_score is not None and family_sample_size:
        if float(trust_score) < -0.5:
            context += " Исторически это семейство рынков сейчас слабее базового уровня модели, поэтому сигнал стоит считать более хрупким."
        elif float(trust_score) > 1.0 and int(family_sample_size) >= 250:
            context += " Исторически это семейство рынков держится устойчиво, поэтому доверие к сигналу чуть выше обычного."
    rerank_bonus = float(candidate.get("family_borderline_rerank_bonus") or 0.0)
    if family == "btts" and rerank_bonus > 0.0:
        context += " Историческая сила BTTS в этой лиге помогла отдать этому рынку приоритет в пограничном сравнении с другими core-сигналами."
    return context


def apply_stake_recommendation(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
) -> None:
    recommendation = str(report_dict.get("top_recommendation") or "").strip()
    confidence = float(report_dict.get("top_confidence_pct") or 0.0)
    risk_level = str(report_dict.get("risk_level") or "HIGH").upper()
    edge = float(snapshot.top_edge_pct or 0.0)
    data_pct = float(snapshot.data_completeness or 0.0) * 100.0
    warnings = list(report_dict.get("data_warnings") or [])
    bankroll_mode = _bankroll_mode()
    recommendation_mode = _recommendation_mode()

    if not recommendation or recommendation.lower().startswith("no bet"):
        report_dict["bankroll_mode"] = bankroll_mode
        report_dict["recommendation_mode"] = recommendation_mode
        report_dict["stake_units"] = 0.0
        report_dict["stake_label"] = "No bet"
        report_dict["stake_rationale"] = (
            f"No edge cleared the filter, so the correct stake is 0u "
            f"(edge {edge:+.1f}%, confidence {confidence:.0f}%, data {data_pct:.0f}%)."
        )
        return

    if any("Action mode pick" in warning for warning in warnings):
        report_dict["bankroll_mode"] = bankroll_mode
        report_dict["recommendation_mode"] = recommendation_mode
        report_dict["stake_units"] = 0.25
        report_dict["stake_label"] = "Minimal"
        report_dict["stake_rationale"] = (
            f"Thin-edge action-mode pick: edge {edge:+.1f}%, confidence {confidence:.0f}%, "
            f"data {data_pct:.0f}%, so stake stays capped at 0.25u under {bankroll_mode} bankroll mode."
        )
        return

    stake_units = 0.25
    stake_label = "Minimal"

    if (
        edge >= 6.0
        and confidence >= 70.0
        and snapshot.data_completeness >= 0.78
        and risk_level == "LOW"
    ):
        stake_units = 1.0
        stake_label = "Standard"
    elif (
        edge >= 4.0
        and confidence >= 64.0
        and snapshot.data_completeness >= 0.68
        and risk_level in {"LOW", "MEDIUM"}
    ):
        stake_units = 0.75
        stake_label = "Medium"
    elif (
        edge >= 2.5
        and confidence >= 58.0
        and snapshot.data_completeness >= 0.58
    ):
        stake_units = 0.5
        stake_label = "Small"

    if risk_level == "HIGH":
        stake_units = min(stake_units, 0.5)
    elif risk_level == "MEDIUM":
        stake_units = min(stake_units, 0.75)

    if edge < 1.5 or confidence < 55.0 or snapshot.data_completeness < 0.5:
        stake_units = 0.25

    if bankroll_mode == "conservative":
        stake_units *= 0.75
        stake_units = min(stake_units, 0.75)
    elif bankroll_mode == "aggressive":
        stake_units *= 1.15
        stake_units = min(stake_units, 1.25)

    if stake_units <= 0.25:
        stake_units = 0.25
    elif stake_units <= 0.50:
        stake_units = 0.50
    elif stake_units <= 0.75:
        stake_units = 0.75
    elif stake_units <= 1.00:
        stake_units = 1.00
    else:
        stake_units = 1.25

    if stake_units > 1.0:
        stake_label = "High"
    elif stake_units >= 1.0:
        stake_label = "Standard"
    elif stake_units >= 0.75:
        stake_label = "Medium"
    elif stake_units >= 0.5:
        stake_label = "Small"
    else:
        stake_label = "Minimal"

    if stake_units == 1.0:
        rationale = (
            f"Strong pre-match edge {edge:+.1f}% with confidence {confidence:.0f}% "
            f"and data {data_pct:.0f}% keeps this at a full 1.0u."
        )
    elif stake_units > 1.0:
        rationale = (
            f"Strong edge {edge:+.1f}% with confidence {confidence:.0f}% "
            f"lets aggressive bankroll mode stretch this to 1.25u."
        )
    elif stake_units == 0.75:
        rationale = (
            f"Solid edge {edge:+.1f}% with confidence {confidence:.0f}% "
            f"supports a controlled 0.75u stake."
        )
    elif stake_units == 0.5:
        rationale = (
            f"Playable but not dominant edge {edge:+.1f}% with confidence {confidence:.0f}% "
            f"points to a small 0.50u stake."
        )
    else:
        rationale = (
            f"Edge {edge:+.1f}% or data {data_pct:.0f}% is still thin, "
            f"so the stake stays minimal at 0.25u."
        )

    if risk_level == "HIGH" and stake_units > 0.25:
        rationale += " High risk keeps the size capped."
    if bankroll_mode == "conservative":
        rationale += " Conservative bankroll mode trims the size."
    elif bankroll_mode == "aggressive" and stake_units >= 0.5:
        rationale += " Aggressive bankroll mode allows a slightly larger size."

    # Kelly criterion overlay: compute optimal Kelly stake for informational display
    kelly_pct = 0.0
    top_odds = report_dict.get("top_recommendation_odds")
    if top_odds and top_odds > 1.0 and confidence > 50.0:
        b = top_odds - 1.0
        p = confidence / 100.0
        q = 1.0 - p
        kelly_full = (b * p - q) / b
        if kelly_full > 0:
            kelly_frac = float(config.ENSEMBLE_KELLY_FRACTION) if hasattr(config, 'ENSEMBLE_KELLY_FRACTION') else 0.25
            kelly_pct = min(kelly_full * kelly_frac * 100.0, 10.0)
            rationale += f" Kelly ({kelly_frac:.0%}): {kelly_pct:.1f}% bankroll."

    # Kelly-proportional mode: override fixed units with Kelly % of current bankroll
    if getattr(config, "BANKROLL_KELLY_PROPORTIONAL", False) and kelly_pct > 0:
        current_bankroll = report_dict.get("_current_bankroll_units") or getattr(
            config, "BANKROLL_STARTING_UNITS", 100.0
        )
        kelly_units = round(kelly_pct / 100.0 * current_bankroll, 2)
        kelly_units = max(kelly_units, 0.25)  # floor at 0.25u
        kelly_units = min(kelly_units, current_bankroll * 0.10)  # cap at 10% of bankroll
        stake_units = kelly_units
        stake_label = f"Kelly {kelly_pct:.1f}%"
        rationale = (
            f"Kelly-proportional: {kelly_pct:.1f}% of {current_bankroll:.1f}u bankroll = "
            f"{kelly_units:.2f}u (edge {edge:+.1f}%, odds {top_odds:.2f})."
        )

    report_dict["bankroll_mode"] = bankroll_mode
    report_dict["recommendation_mode"] = recommendation_mode
    report_dict["stake_units"] = stake_units
    report_dict["stake_label"] = stake_label
    report_dict["stake_rationale"] = rationale
    report_dict["kelly_pct"] = kelly_pct


def apply_pick_policy(
    report_dict: dict,
    snapshot: PredictionFeatureSnapshot,
    odds_data: Optional[OddsData] = None,
    family_quality_profiles: Optional[list[dict]] = None,
) -> None:
    top_rec = str(report_dict.get("top_recommendation") or "").strip()
    if not top_rec:
        return

    policy = _policy_thresholds()
    if "insufficient data" in top_rec.lower():
        snapshot.no_bet_triggered = True
        snapshot.top_edge_pct = 0.0
        return

    candidates = build_market_candidates(
        report_dict,
        snapshot,
        odds_data,
        family_quality_profiles,
    )
    top_candidate = _find_matching_candidate(
        top_rec,
        report_dict,
        snapshot,
        odds_data,
        family_quality_profiles,
    )
    preferred_top_candidate = _prefer_core_top_pick_candidate(top_candidate, candidates)
    if preferred_top_candidate is not None and preferred_top_candidate is not top_candidate:
        report_dict["top_recommendation"] = str(preferred_top_candidate.get("label") or top_rec)
        report_dict["top_recommendation_odds"] = preferred_top_candidate.get("odds")
        candidate_confidence = preferred_top_candidate.get("confidence_pct")
        if candidate_confidence is not None:
            report_dict["top_confidence_pct"] = float(candidate_confidence)
        warnings = list(report_dict.get("data_warnings") or [])
        normalization_warning = "Top pick normalized to stronger core market"
        if normalization_warning not in warnings:
            warnings.append(normalization_warning)
        report_dict["data_warnings"] = warnings

        summary = str(report_dict.get("analysis_summary") or "").strip()
        normalization_sentence = (
            f"Top pick was normalized from {top_rec} to {report_dict['top_recommendation']} "
            "because the core market had stronger historical trust with a similar edge."
        )
        if normalization_sentence not in summary:
            summary = f"{summary} {normalization_sentence}".strip()
        report_dict["analysis_summary"] = summary
        top_rec = str(report_dict.get("top_recommendation") or "").strip()
        top_candidate = preferred_top_candidate

    snapshot.top_edge_pct = estimate_market_edge_for_recommendation(top_rec, report_dict, snapshot, odds_data)
    low_confidence = float(report_dict.get("top_confidence_pct") or 0.0) < policy["min_confidence"]
    weak_market_edge = snapshot.top_edge_pct is not None and snapshot.top_edge_pct < policy["weak_market_edge"]
    severe_market_edge = snapshot.top_edge_pct is not None and snapshot.top_edge_pct < policy["severe_market_edge"]
    low_data = snapshot.data_completeness < policy["low_data"]
    severe_low_data = snapshot.data_completeness < policy["severe_low_data"]
    high_disagreement = float(snapshot.llm_model_gap_pct or 0.0) >= policy["high_disagreement"]
    too_many_warnings = len(report_dict.get("data_warnings") or []) >= int(policy["warning_count"])
    weak_market_family = top_candidate is not None and not _candidate_passes_family_gate(
        top_candidate,
        snapshot,
        purpose="top_pick",
    )

    reasons: list[str] = []
    if weak_market_edge:
        reasons.append("market edge too small")
    if low_confidence:
        reasons.append("confidence too low")
    if high_disagreement:
        reasons.append("model disagreement too high")
    if low_data:
        reasons.append("data completeness too low")
    if too_many_warnings:
        reasons.append("too many missing signals")
    family_reason = _candidate_family_reason(top_candidate)
    if weak_market_family and family_reason:
        reasons.append(family_reason)

    should_no_bet = (
        severe_market_edge
        or severe_low_data
        or weak_market_family
        or (high_disagreement and (snapshot.top_edge_pct is None or snapshot.top_edge_pct < max(0.75, policy["severe_market_edge"] + 0.65)))
        or len(reasons) >= int(policy["reason_count"])
    )
    if not should_no_bet:
        snapshot.no_bet_triggered = False
        report_dict["top_pick_context"] = build_top_pick_context(
            report_dict,
            snapshot,
            odds_data,
            family_quality_profiles,
        )
        apply_stake_recommendation(report_dict, snapshot)
        return

    if _recommendation_mode() == "action":
        candidate = choose_action_pick(report_dict, snapshot, odds_data, family_quality_profiles)
        if candidate is not None:
            candidate_label = str(candidate.get("label") or "").strip()
            candidate_edge = round(float(candidate.get("edge") or 0.0), 2)
            report_dict["top_recommendation"] = candidate_label
            report_dict["top_recommendation_odds"] = candidate.get("odds")
            report_dict["top_confidence_pct"] = max(
                float(report_dict.get("top_confidence_pct") or 0.0),
                float(candidate.get("confidence_pct") or 0.0),
            )
            report_dict["risk_level"] = "HIGH" if candidate_edge < 2.5 or snapshot.data_completeness < 0.65 else "MEDIUM"

            warnings = list(report_dict.get("data_warnings") or [])
            action_warning = "Action mode pick: thin edge accepted, use minimal stake only"
            if action_warning not in warnings:
                warnings.append(action_warning)
            report_dict["data_warnings"] = warnings

            summary = str(report_dict.get("analysis_summary") or "").strip()
            action_sentence = f"Action mode replaced no-bet with {candidate_label} despite the thin edge; stake should remain minimal."
            if action_sentence not in summary:
                summary = f"{summary} {action_sentence}".strip()
            report_dict["analysis_summary"] = summary

            snapshot.no_bet_triggered = False
            snapshot.top_edge_pct = candidate_edge
            report_dict["top_pick_context"] = build_top_pick_context(
                report_dict,
                snapshot,
                odds_data,
                family_quality_profiles,
            )
            apply_stake_recommendation(report_dict, snapshot)
            return

    primary_reason = reasons[0] if reasons else "insufficient edge"
    report_dict["top_recommendation"] = f"No bet - {primary_reason}"
    report_dict["top_recommendation_odds"] = None
    report_dict["top_confidence_pct"] = min(float(report_dict.get("top_confidence_pct") or 0.0), 52.0)
    report_dict["risk_level"] = "HIGH"

    warnings = list(report_dict.get("data_warnings") or [])
    warning_text = "No-bet filter triggered: " + ", ".join(reasons[:3] or [primary_reason])
    if warning_text not in warnings:
        warnings.append(warning_text)
    report_dict["data_warnings"] = warnings

    summary = str(report_dict.get("analysis_summary") or "").strip()
    no_bet_sentence = f"No-bet filter triggered because {', '.join(reasons[:2] or [primary_reason])}."
    if no_bet_sentence not in summary:
        summary = f"{summary} {no_bet_sentence}".strip()
    report_dict["analysis_summary"] = summary
    snapshot.no_bet_triggered = True
    report_dict["top_pick_context"] = build_top_pick_context(
        report_dict,
        snapshot,
        odds_data,
        family_quality_profiles,
    )
    apply_stake_recommendation(report_dict, snapshot)


def infer_blended_probabilities(snapshot: PredictionFeatureSnapshot) -> Optional[dict[str, float]]:
    if None in (
        snapshot.llm_prob_w1,
        snapshot.llm_prob_draw,
        snapshot.llm_prob_w2,
        snapshot.model_prob_w1,
        snapshot.model_prob_draw,
        snapshot.model_prob_w2,
    ):
        return None

    alpha = float(snapshot.blend_alpha or 0.0)

    def _blend(llm_value: Optional[float], model_value: Optional[float]) -> Optional[float]:
        if llm_value is None or model_value is None:
            return None
        return round(((1.0 - alpha) * llm_value) + (alpha * model_value), 1)

    return {
        "prob_w1": _blend(snapshot.llm_prob_w1, snapshot.model_prob_w1),
        "prob_draw": _blend(snapshot.llm_prob_draw, snapshot.model_prob_draw),
        "prob_w2": _blend(snapshot.llm_prob_w2, snapshot.model_prob_w2),
        "prob_over_2_5": _blend(snapshot.llm_prob_over_2_5, snapshot.model_prob_over_2_5),
        "prob_under_2_5": _blend(snapshot.llm_prob_under_2_5, snapshot.model_prob_under_2_5),
        "prob_btts_yes": _blend(snapshot.llm_prob_btts_yes, snapshot.model_prob_btts_yes),
        "prob_btts_no": _blend(snapshot.llm_prob_btts_no, snapshot.model_prob_btts_no),
    }
