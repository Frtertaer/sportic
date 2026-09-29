"""Shared helpers for prediction persistence and result-resolution side effects."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import database as db
from learning import LearningModule
from memory import get_shared_vector_memory
from models import MatchData, MatchResult, PredictionReport
from team_parser import normalize_team_name

logger = logging.getLogger(__name__)


def make_prediction_memory_id(prediction_id: int) -> str:
    return f"prediction_{prediction_id}"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _resolve_competition_name(match_data: MatchData) -> str:
    if match_data.match_context and match_data.match_context.competition_name:
        return match_data.match_context.competition_name
    if match_data.flashscore and match_data.flashscore.league_name:
        return match_data.flashscore.league_name
    if match_data.standings_context and match_data.standings_context.league_name:
        return match_data.standings_context.league_name
    return "Unknown"


def _resolve_kickoff_utc(match_data: MatchData) -> Optional[str]:
    if match_data.match_context and match_data.match_context.kickoff_utc:
        return match_data.match_context.kickoff_utc
    return None


def _build_live_match_provider_key(match_data: MatchData) -> str:
    kickoff = _resolve_kickoff_utc(match_data)
    match_date = kickoff[:10] if kickoff else _now_utc()[:10]
    competition = _slugify(_resolve_competition_name(match_data))
    team1 = _slugify(normalize_team_name(match_data.team1))
    team2 = _slugify(normalize_team_name(match_data.team2))
    return f"{competition}:{match_date}:{team1}:{team2}"


def _points_from_recent_matches(matches) -> Optional[float]:
    if not matches:
        return None
    total = 0.0
    seen = False
    for match in matches[:5]:
        if match.result == MatchResult.WIN:
            total += 3.0
            seen = True
        elif match.result == MatchResult.DRAW:
            total += 1.0
            seen = True
        elif match.result == MatchResult.LOSS:
            seen = True
    return total if seen else None


def _goal_diff_from_recent_matches(matches) -> Optional[float]:
    if not matches:
        return None
    total = 0
    seen = False
    for match in matches[:5]:
        try:
            goals_for, goals_against = (int(part) for part in str(match.score).split("-", 1))
        except (TypeError, ValueError):
            continue
        seen = True
        if str(match.home_away).upper() == "H":
            total += goals_for - goals_against
        else:
            total += goals_against - goals_for
    return float(total) if seen else None


def _build_h2h_stats(match_data: MatchData) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if not (match_data.flashscore and match_data.flashscore.scrape_success):
        return None, None, None
    h2h_matches = match_data.flashscore.h2h_matches[:5]
    if not h2h_matches:
        return None, None, None

    team1_name = normalize_team_name(match_data.team1).lower()
    team2_name = normalize_team_name(match_data.team2).lower()
    team1_wins = 0
    draws = 0
    total_goals = 0
    counted = 0

    for match in h2h_matches:
        try:
            home_goals, away_goals = (int(part) for part in str(match.score).split("-", 1))
        except (TypeError, ValueError):
            continue

        home_team = normalize_team_name(match.home_team).lower()
        away_team = normalize_team_name(match.away_team).lower()
        if home_team == team1_name and away_team == team2_name:
            team1_goals, team2_goals = home_goals, away_goals
        elif home_team == team2_name and away_team == team1_name:
            team1_goals, team2_goals = away_goals, home_goals
        else:
            continue

        counted += 1
        total_goals += team1_goals + team2_goals
        if team1_goals > team2_goals:
            team1_wins += 1
        elif team1_goals == team2_goals:
            draws += 1

    if counted == 0:
        return None, None, None
    return (
        round(team1_wins / counted, 4),
        round(draws / counted, 4),
        round(total_goals / counted, 4),
    )


def _max_market_bookmaker_count(match_data: MatchData) -> Optional[int]:
    if not (match_data.market_signals and match_data.market_signals.scrape_success):
        return None
    counts: list[int] = []
    for bucket in (
        match_data.market_signals.one_x_two,
        match_data.market_signals.over_under_2_5,
        match_data.market_signals.btts,
        match_data.market_signals.asian_handicap,
    ):
        for signal in bucket.values():
            if signal.bookmaker_count:
                counts.append(signal.bookmaker_count)
    return max(counts) if counts else None


def _resolve_consensus_favourite(match_data: MatchData) -> Optional[str]:
    odds = match_data.odds if match_data.odds and match_data.odds.scrape_success else None
    if odds:
        candidates = [
            (match_data.team1, odds.team1_win_odds),
            ("Draw", odds.draw_odds),
            (match_data.team2, odds.team2_win_odds),
        ]
        ranked = [(label, odd) for label, odd in candidates if odd is not None and odd > 1.0]
        if ranked:
            ranked.sort(key=lambda item: item[1])
            return ranked[0][0]
    if match_data.market_signals and match_data.market_signals.favourite_label:
        label = match_data.market_signals.favourite_label
        if label == "team1":
            return match_data.team1
        if label == "team2":
            return match_data.team2
        return label.title()
    return None


def _resolve_totals_dispersion(match_data: MatchData) -> Optional[float]:
    if not (match_data.market_signals and match_data.market_signals.scrape_success):
        return None
    over_signal = match_data.market_signals.over_under_2_5.get("over")
    if over_signal and over_signal.min_odds is not None and over_signal.max_odds is not None:
        return round(over_signal.max_odds - over_signal.min_odds, 4)
    return None


def _build_historical_snapshot_payload(historical_match_id: int, match_data: MatchData) -> dict:
    flashscore = match_data.flashscore if match_data.flashscore and match_data.flashscore.scrape_success else None
    advanced = match_data.advanced_stats if match_data.advanced_stats and match_data.advanced_stats.scrape_success else None
    standings = match_data.standings_context if match_data.standings_context and match_data.standings_context.scrape_success else None
    schedule = match_data.schedule_load if match_data.schedule_load and match_data.schedule_load.scrape_success else None
    transfermarkt = match_data.transfermarkt if match_data.transfermarkt and match_data.transfermarkt.scrape_success else None
    sofascore = match_data.sofascore if match_data.sofascore and match_data.sofascore.scrape_success else None
    h2h_team1_win_rate, h2h_draw_rate, h2h_avg_goals = _build_h2h_stats(match_data)

    return {
        "historical_match_id": historical_match_id,
        "snapshot_source": "sportsbot_live_prediction",
        "captured_at": _now_utc(),
        "data_completeness": match_data.data_completeness,
        "successful_source_count": match_data.successful_source_count,
        "total_source_count": match_data.total_source_count,
        "team1_form_points_last5": _points_from_recent_matches(flashscore.team1_form) if flashscore else None,
        "team2_form_points_last5": _points_from_recent_matches(flashscore.team2_form) if flashscore else None,
        "team1_goal_diff_last5": _goal_diff_from_recent_matches(flashscore.team1_form) if flashscore else None,
        "team2_goal_diff_last5": _goal_diff_from_recent_matches(flashscore.team2_form) if flashscore else None,
        "team1_scored_avg": flashscore.team1_goals_scored_avg if flashscore else None,
        "team2_scored_avg": flashscore.team2_goals_scored_avg if flashscore else None,
        "team1_conceded_avg": flashscore.team1_goals_conceded_avg if flashscore else None,
        "team2_conceded_avg": flashscore.team2_goals_conceded_avg if flashscore else None,
        "h2h_team1_win_rate": h2h_team1_win_rate,
        "h2h_draw_rate": h2h_draw_rate,
        "h2h_avg_goals": h2h_avg_goals,
        "team1_xg_for": advanced.team1_metrics.xg if advanced else None,
        "team2_xg_for": advanced.team2_metrics.xg if advanced else None,
        "team1_xga": advanced.team1_metrics.xga if advanced else None,
        "team2_xga": advanced.team2_metrics.xga if advanced else None,
        "team1_rating": advanced.team1_metrics.rating if advanced else None,
        "team2_rating": advanced.team2_metrics.rating if advanced else None,
        "team1_possession": advanced.team1_metrics.possession_pct if advanced else None,
        "team2_possession": advanced.team2_metrics.possession_pct if advanced else None,
        "team1_table_position": standings.team1_overall.position if standings else None,
        "team2_table_position": standings.team2_overall.position if standings else None,
        "team1_points": standings.team1_overall.points if standings else None,
        "team2_points": standings.team2_overall.points if standings else None,
        "rest_gap_hours": schedule.rest_gap_hours if schedule else None,
        "team1_short_rest": schedule.team1.short_rest if schedule else None,
        "team2_short_rest": schedule.team2.short_rest if schedule else None,
        "team1_market_value_eur": transfermarkt.team1_market_value_eur if transfermarkt else None,
        "team2_market_value_eur": transfermarkt.team2_market_value_eur if transfermarkt else None,
        "key_absence_diff": (
            len(sofascore.team1_key_absences) - len(sofascore.team2_key_absences)
            if sofascore else None
        ),
        "competition_name": _resolve_competition_name(match_data),
        "schedule_density_note": "; ".join(schedule.load_warnings) if schedule and schedule.load_warnings else None,
    }


def _build_historical_market_line_payload(
    historical_match_id: int,
    report: PredictionReport,
    match_data: MatchData,
    *,
    snapshot_kind: str,
    captured_at: Optional[str] = None,
    source_provider: str = "sportsbot:live-predict",
) -> Optional[dict]:
    odds = match_data.odds if match_data.odds and match_data.odds.scrape_success else None
    feature_snapshot = getattr(report, "feature_snapshot", None)
    if odds is None and feature_snapshot is None:
        return None

    implied_w1 = (
        round(odds.implied_prob_w1 * 100.0, 4)
        if odds and odds.implied_prob_w1 is not None
        else getattr(feature_snapshot, "market_prob_w1", None)
    )
    implied_draw = (
        round(odds.implied_prob_draw * 100.0, 4)
        if odds and odds.implied_prob_draw is not None
        else getattr(feature_snapshot, "market_prob_draw", None)
    )
    implied_w2 = (
        round(odds.implied_prob_w2 * 100.0, 4)
        if odds and odds.implied_prob_w2 is not None
        else getattr(feature_snapshot, "market_prob_w2", None)
    )
    ah_line = None
    if odds and odds.asian_handicap is not None:
        ah_line = odds.asian_handicap
    elif odds and odds.asian_handicap_line is not None:
        sign = "+" if odds.asian_handicap_line > 0 else ""
        ah_line = f"{match_data.team1} {sign}{odds.asian_handicap_line:g}"

    movement_team1 = None
    movement_team2 = None
    dispersion_1x2 = None
    if match_data.market_signals and match_data.market_signals.scrape_success:
        movement_team1 = (
            match_data.market_signals.one_x_two.get("team1").movement_score
            if match_data.market_signals.one_x_two.get("team1") else None
        )
        movement_team2 = (
            match_data.market_signals.one_x_two.get("team2").movement_score
            if match_data.market_signals.one_x_two.get("team2") else None
        )
        favourite = _resolve_consensus_favourite(match_data)
        if favourite in {match_data.team1, match_data.team2}:
            label = "team1" if favourite == match_data.team1 else "team2"
            favourite_signal = match_data.market_signals.one_x_two.get(label)
            if favourite_signal and favourite_signal.min_odds is not None and favourite_signal.max_odds is not None:
                dispersion_1x2 = round(favourite_signal.max_odds - favourite_signal.min_odds, 4)

    return {
        "historical_match_id": historical_match_id,
        "snapshot_kind": snapshot_kind,
        "captured_at": captured_at or _now_utc(),
        "bookmaker_count": _max_market_bookmaker_count(match_data),
        "source_provider": source_provider,
        "odds_w1": odds.team1_win_odds if odds else None,
        "odds_draw": odds.draw_odds if odds else None,
        "odds_w2": odds.team2_win_odds if odds else None,
        "implied_prob_w1": implied_w1,
        "implied_prob_draw": implied_draw,
        "implied_prob_w2": implied_w2,
        "odds_over_1_5": odds.over_1_5_odds if odds else None,
        "odds_under_1_5": odds.under_1_5_odds if odds else None,
        "odds_over_2_5": odds.over_2_5_odds if odds else None,
        "odds_under_2_5": odds.under_2_5_odds if odds else None,
        "odds_over_3_5": odds.over_3_5_odds if odds else None,
        "odds_under_3_5": odds.under_3_5_odds if odds else None,
        "odds_btts_yes": odds.btts_yes_odds if odds else None,
        "odds_btts_no": odds.btts_no_odds if odds else None,
        "odds_dnb_team1": odds.dnb_team1_odds if odds else None,
        "odds_dnb_team2": odds.dnb_team2_odds if odds else None,
        "odds_dc_1x": odds.double_chance_1x_odds if odds else None,
        "odds_dc_12": odds.double_chance_12_odds if odds else None,
        "odds_dc_x2": odds.double_chance_x2_odds if odds else None,
        "ah_main_line": ah_line,
        "ah_team1_odds": odds.asian_handicap_home_odds if odds else None,
        "ah_team2_odds": odds.asian_handicap_away_odds if odds else None,
        "consensus_favourite": _resolve_consensus_favourite(match_data),
        "movement_score_team1": movement_team1,
        "movement_score_team2": movement_team2,
        "dispersion_1x2": dispersion_1x2,
        "dispersion_totals": _resolve_totals_dispersion(match_data),
    }


async def _upsert_live_market_history(
    historical_match_id: int,
    report: PredictionReport,
    match_data: MatchData,
) -> None:
    existing_lines = await db.get_historical_market_lines(historical_match_id)
    has_open_snapshot = any(
        line.get("snapshot_kind") == "OPEN"
        and line.get("source_provider") == "sportsbot:live-predict"
        for line in existing_lines
    )

    if not has_open_snapshot:
        open_payload = _build_historical_market_line_payload(
            historical_match_id,
            report,
            match_data,
            snapshot_kind="OPEN",
        )
        if open_payload is not None:
            await db.upsert_historical_market_line(open_payload)

    mid_payload = _build_historical_market_line_payload(
        historical_match_id,
        report,
        match_data,
        snapshot_kind="MID",
    )
    if mid_payload is not None:
        await db.upsert_historical_market_line(mid_payload)


async def _promote_latest_live_market_to_close(
    historical_match_id: int,
    *,
    captured_at: Optional[str] = None,
) -> None:
    existing_lines = await db.get_historical_market_lines(historical_match_id)
    if any(
        line.get("snapshot_kind") == "CLOSE"
        and line.get("source_provider") == "sportsbot:live-predict"
        for line in existing_lines
    ):
        return

    candidate = None
    for snapshot_kind in ("MID", "OPEN"):
        candidate = next(
            (
                line for line in reversed(existing_lines)
                if line.get("snapshot_kind") == snapshot_kind
                and line.get("source_provider") == "sportsbot:live-predict"
            ),
            None,
        )
        if candidate is not None:
            break
    if candidate is None:
        return

    close_payload = {key: value for key, value in candidate.items() if key not in {"id", "created_at"}}
    close_payload["snapshot_kind"] = "CLOSE"
    close_payload["captured_at"] = captured_at or _now_utc()
    await db.upsert_historical_market_line(close_payload)


async def sync_prediction_to_historical(
    prediction_id: int,
    report: PredictionReport,
    match_data: MatchData,
) -> Optional[int]:
    provider_name = "sportsbot_live"
    provider_match_key = _build_live_match_provider_key(match_data)
    existing = await db.get_historical_match_by_provider_key(provider_name, provider_match_key)

    kickoff_utc = _resolve_kickoff_utc(match_data)
    match_payload = {
        "provider_match_key": provider_match_key,
        "provider_name": provider_name,
        "competition_name": _resolve_competition_name(match_data),
        "season_label": kickoff_utc[:4] if kickoff_utc else None,
        "stage_name": match_data.match_context.stage if match_data.match_context else None,
        "match_date_utc": kickoff_utc[:10] if kickoff_utc else _now_utc()[:10],
        "kickoff_utc": kickoff_utc,
        "team1_name": report.team1,
        "team2_name": report.team2,
        "team1_normalized": normalize_team_name(report.team1).lower(),
        "team2_normalized": normalize_team_name(report.team2).lower(),
        "is_neutral_venue": (
            match_data.match_context.is_neutral_venue
            if match_data.match_context else None
        ),
        "venue_name": match_data.match_context.venue_name if match_data.match_context else None,
        "team1_score": existing["team1_score"] if existing else None,
        "team2_score": existing["team2_score"] if existing else None,
        "actual_result": existing["actual_result"] if existing else None,
        "status": existing["status"] if existing else "SCHEDULED",
    }
    historical_match_id = await db.upsert_historical_match(match_payload)
    await db.link_prediction_to_historical_match(prediction_id, historical_match_id)
    await db.upsert_historical_team_snapshot(
        _build_historical_snapshot_payload(historical_match_id, match_data)
    )
    await _upsert_live_market_history(historical_match_id, report, match_data)

    for status in match_data.resolved_source_statuses:
        if status.coverage_gap or not status.applicable:
            coverage_status = "UNSUPPORTED"
            error_type = "coverage_gap"
        elif status.scrape_success:
            coverage_status = "OK"
            error_type = None
        else:
            coverage_status = "FAILED"
            error_type = status.exception_type or "scrape_failure"

        await db.upsert_historical_provider_coverage(
            {
                "historical_match_id": historical_match_id,
                "source_key": status.key,
                "source_label": status.label,
                "status": coverage_status,
                "error_type": error_type,
                "error_message": None if status.scrape_success else status.error_message,
                "duration_ms": status.duration_ms,
                "provider_name": status.provider,
            }
        )

    return historical_match_id


async def update_prediction_historical_result(resolved_prediction: dict) -> None:
    historical_match_id = resolved_prediction.get("historical_match_id")
    if not historical_match_id:
        return
    await _promote_latest_live_market_to_close(
        int(historical_match_id),
        captured_at=resolved_prediction.get("result_at") or _now_utc(),
    )
    await db.update_historical_match_result(
        int(historical_match_id),
        team1_score=resolved_prediction.get("actual_goals_home"),
        team2_score=resolved_prediction.get("actual_goals_away"),
        actual_result=resolved_prediction.get("actual_result"),
        status="FINISHED",
    )


def _build_form_summary(data: MatchData) -> str:
    if not (data.flashscore and data.flashscore.scrape_success):
        return ""

    def _form(matches) -> str:
        return "".join(match.result.value for match in matches[:5]) or "N/A"

    return (
        f"{data.team1} last5={_form(data.flashscore.team1_form)}; "
        f"{data.team2} last5={_form(data.flashscore.team2_form)}"
    )


def _build_h2h_summary(data: MatchData) -> str:
    if not (data.flashscore and data.flashscore.scrape_success and data.flashscore.h2h_matches):
        return ""
    parts = [
        f"{match.home_team} {match.score} {match.away_team}"
        for match in data.flashscore.h2h_matches[:3]
    ]
    return "; ".join(parts)


def _build_prediction_summary(report: PredictionReport) -> str:
    return (
        f"{report.top_recommendation} ({report.top_confidence_pct:.0f}%) | "
        f"1X2 {report.prob_w1:.0f}/{report.prob_draw:.0f}/{report.prob_w2:.0f}"
    )


def _resolve_match_league(match_data: MatchData) -> str:
    if match_data.flashscore and match_data.flashscore.league_name:
        return match_data.flashscore.league_name
    if match_data.match_context and match_data.match_context.competition_name:
        return match_data.match_context.competition_name
    return "Unknown"

async def store_prediction_memory(
    prediction_id: int,
    report: PredictionReport,
    match_data: MatchData,
) -> None:
    try:
        vm = await get_shared_vector_memory()
        await vm.store_match(
            match_id=make_prediction_memory_id(prediction_id),
            team1=report.team1,
            team2=report.team2,
            league=_resolve_match_league(match_data),
            form=_build_form_summary(match_data),
            h2h=_build_h2h_summary(match_data),
            prediction=_build_prediction_summary(report),
            metadata={
                "prediction_id": prediction_id,
                "top_recommendation": report.top_recommendation,
                "risk_level": report.risk_level,
            },
        )
    except Exception as exc:
        logger.warning(
            "Prediction memory store failed for id=%d (non-critical): %s",
            prediction_id,
            exc,
        )


async def update_prediction_memory_result(
    prediction_id: int,
    actual_result: str,
    actual_score: str,
) -> None:
    try:
        vm = await get_shared_vector_memory()
        await vm.update_match_result(
            make_prediction_memory_id(prediction_id),
            result=actual_result,
            score=actual_score,
        )
    except Exception as exc:
        logger.warning(
            "Prediction memory result update failed for id=%d (non-critical): %s",
            prediction_id,
            exc,
        )


async def process_prediction_result(
    prediction_id: int,
    score: str,
    *,
    score_team1: Optional[str] = None,
    score_team2: Optional[str] = None,
) -> Optional[dict]:
    stored = await db.mark_result_by_prediction_id(
        prediction_id,
        score,
        score_team1=score_team1,
        score_team2=score_team2,
    )
    if not stored:
        return None

    await db.recompute_calibration()
    try:
        await db.recompute_numeric_recalibration()
    except Exception as exc:
        logger.warning(
            "Numeric recalibration recompute failed for prediction id=%d (non-critical): %s",
            prediction_id,
            exc,
        )

    resolved = await db.get_resolved_prediction(prediction_id)
    if not resolved:
        return None

    try:
        learner = LearningModule()
        await learner.process_result(
            prediction_id,
            resolved["actual_result"],
            resolved["actual_score"],
        )
    except Exception as exc:
        logger.warning(
            "Learning pipeline failed for prediction id=%d (non-critical): %s",
            prediction_id,
            exc,
        )

    try:
        await update_prediction_historical_result(resolved)
    except Exception as exc:
        logger.warning(
            "Historical result sync failed for prediction id=%d (non-critical): %s",
            prediction_id,
            exc,
        )

    await update_prediction_memory_result(
        prediction_id,
        resolved["actual_result"],
        resolved["actual_score"],
    )
    return resolved
