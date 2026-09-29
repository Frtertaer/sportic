"""
Persistent SQLite store for self-learning.

Five tables:
  predictions  — every /predict call with probabilities (result added later)
  team_memory  — scraped team snapshots cached between sessions
  calibration  — precomputed accuracy stats injected into Claude's prompt
  rate_limits  — per-user last-request timestamps for rate limiting
  lessons      — auto-generated insights from prediction errors/successes

All blocking sqlite3 calls are wrapped in asyncio.to_thread() — no extra deps.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models import MatchData, PredictionFeatureSnapshot

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "sportsbot.db"

_PREDICTION_JSON_COLUMNS = (
    "team1_strengths",
    "team1_weaknesses",
    "team2_strengths",
    "team2_weaknesses",
    "data_warnings",
    "policy_flags",
    "value_bets",
)

_PREDICTION_JSON_OBJECT_LIST_COLUMNS = (
    "value_candidate_snapshot",
)

_PREDICTION_MIGRATION_COLUMNS: list[tuple[str, str]] = [
    ("actual_goals_home", "INTEGER"),
    ("actual_goals_away", "INTEGER"),
    ("user_id", "INTEGER"),
    ("notification_sent", "INTEGER DEFAULT 0"),
    ("historical_match_id", "INTEGER"),
    ("result_confidence", "TEXT"),
    ("prob_under_2_5", "REAL"),
    ("expected_goals", "REAL"),
    ("goals_confidence", "TEXT"),
    ("asian_handicap_rec", "TEXT"),
    ("asian_handicap_confidence", "TEXT"),
    ("prob_btts_no", "REAL"),
    ("btts_confidence", "TEXT"),
    ("team1_strengths", "TEXT"),
    ("team1_weaknesses", "TEXT"),
    ("team2_strengths", "TEXT"),
    ("team2_weaknesses", "TEXT"),
    ("top_recommendation_odds", "REAL"),
    ("top_market_code", "TEXT"),
    ("top_selection_code", "TEXT"),
    ("top_confidence_pct", "REAL"),
    ("risk_level", "TEXT"),
    ("stake_units", "REAL"),
    ("stake_label", "TEXT"),
    ("stake_rationale", "TEXT"),
    ("bankroll_mode", "TEXT"),
    ("recommendation_mode", "TEXT"),
    ("policy_flags", "TEXT"),
    ("analysis_summary", "TEXT"),
    ("data_warnings", "TEXT"),
    ("value_bets", "TEXT"),
    ("value_candidate_snapshot", "TEXT"),
    ("market_ah_line", "TEXT"),
    ("market_ah_odds", "REAL"),
    ("methodology_version", "TEXT"),
    ("prompt_version", "TEXT"),
]

_PREDICTION_SELECT_COLUMNS = """
    id, team1, team2, predicted_at,
    prob_w1, prob_draw, prob_w2, result_confidence,
    prob_over_2_5, prob_under_2_5, expected_goals, goals_confidence,
    asian_handicap_rec, asian_handicap_confidence,
    prob_btts_yes, prob_btts_no, btts_confidence,
    team1_strengths, team1_weaknesses, team2_strengths, team2_weaknesses,
    top_rec, top_recommendation_odds, top_market_code, top_selection_code,
    top_confidence_pct, risk_level, stake_units, stake_label, stake_rationale, bankroll_mode, recommendation_mode,
    policy_flags, analysis_summary, data_warnings, value_bets, value_candidate_snapshot,
    market_ah_line, market_ah_odds,
    methodology_version, prompt_version,
    completeness, actual_result, actual_score,
    actual_goals_home, actual_goals_away, result_at,
    user_id, notification_sent, historical_match_id
"""

_FEATURE_BOOL_COLUMNS = (
    "is_neutral_venue",
    "is_team1_home",
    "no_bet_triggered",
    "team1_short_rest",
    "team2_short_rest",
)

_HISTORICAL_MATCH_BOOL_COLUMNS = (
    "is_neutral_venue",
    "went_extra_time",
    "went_penalties",
)

_HISTORICAL_TEAM_SNAPSHOT_BOOL_COLUMNS = (
    "team1_short_rest",
    "team2_short_rest",
)

_FEATURE_SELECT_COLUMNS = """
    prediction_id, created_at,
    team1, team2, competition_name,
    data_completeness, successful_source_count, total_source_count,
    is_neutral_venue, is_team1_home,
    market_prob_w1, market_prob_draw, market_prob_w2,
    market_prob_over_2_5, market_prob_under_2_5,
    market_prob_btts_yes, market_prob_btts_no,
    llm_prob_w1, llm_prob_draw, llm_prob_w2,
    llm_prob_over_2_5, llm_prob_under_2_5,
    llm_prob_btts_yes, llm_prob_btts_no,
    model_prob_w1, model_prob_draw, model_prob_w2,
    model_prob_over_2_5, model_prob_under_2_5,
    model_prob_btts_yes, model_prob_btts_no,
    model_expected_goals,
    blend_alpha, top_edge_pct, llm_model_gap_pct, no_bet_triggered,
    form_points_last5_diff, form_goal_diff_last5_diff,
    form_scored_avg_diff, form_conceded_avg_diff,
    h2h_team1_win_rate, h2h_draw_rate, h2h_avg_goals,
    xg_diff, xga_edge, rating_diff, possession_diff,
    standings_point_gap, standings_position_gap,
    rest_gap_hours, team1_short_rest, team2_short_rest,
    market_value_ratio, key_absence_diff,
    movement_score_w1, movement_score_over, favourite_gap_pct
"""

_HISTORICAL_MATCH_SELECT_COLUMNS = """
    id, provider_match_key, provider_name,
    competition_name, competition_country, season_label, stage_name,
    match_date_utc, kickoff_utc,
    team1_name, team2_name, team1_normalized, team2_normalized,
    is_neutral_venue, venue_name,
    team1_score, team2_score, actual_result,
    went_extra_time, went_penalties, status,
    created_at, updated_at
"""

_HISTORICAL_MATCH_COLUMNS = [
    column.strip()
    for column in _HISTORICAL_MATCH_SELECT_COLUMNS.split(",")
]

_HISTORICAL_MARKET_LINE_SELECT_COLUMNS = """
    id, historical_match_id, snapshot_kind, captured_at, bookmaker_count,
    source_provider,
    odds_w1, odds_draw, odds_w2,
    implied_prob_w1, implied_prob_draw, implied_prob_w2,
    odds_over_1_5, odds_under_1_5,
    odds_over_2_5, odds_under_2_5,
    odds_over_3_5, odds_under_3_5,
    odds_btts_yes, odds_btts_no,
    odds_dnb_team1, odds_dnb_team2,
    odds_dc_1x, odds_dc_12, odds_dc_x2,
    ah_main_line, ah_team1_odds, ah_team2_odds,
    consensus_favourite,
    movement_score_team1, movement_score_team2,
    dispersion_1x2, dispersion_totals,
    created_at
"""

_HISTORICAL_MARKET_LINE_COLUMNS = [
    column.strip()
    for column in _HISTORICAL_MARKET_LINE_SELECT_COLUMNS.split(",")
]

_PREDICTION_CLV_MARKET_COLUMNS = (
    "odds_w1",
    "odds_draw",
    "odds_w2",
    "odds_over_1_5",
    "odds_under_1_5",
    "odds_over_2_5",
    "odds_under_2_5",
    "odds_over_3_5",
    "odds_under_3_5",
    "odds_btts_yes",
    "odds_btts_no",
    "odds_dnb_team1",
    "odds_dnb_team2",
    "odds_dc_1x",
    "odds_dc_12",
    "odds_dc_x2",
)

_HISTORICAL_TEAM_SNAPSHOT_SELECT_COLUMNS = """
    id, historical_match_id, snapshot_source, captured_at,
    data_completeness, successful_source_count, total_source_count,
    team1_form_points_last5, team2_form_points_last5,
    team1_goal_diff_last5, team2_goal_diff_last5,
    team1_scored_avg, team2_scored_avg,
    team1_conceded_avg, team2_conceded_avg,
    team1_shots_avg, team2_shots_avg,
    team1_shots_allowed_avg, team2_shots_allowed_avg,
    team1_shots_on_target_avg, team2_shots_on_target_avg,
    team1_shots_on_target_allowed_avg, team2_shots_on_target_allowed_avg,
    team1_corners_avg, team2_corners_avg,
    team1_corners_allowed_avg, team2_corners_allowed_avg,
    h2h_team1_win_rate, h2h_draw_rate, h2h_avg_goals,
    team1_xg_for, team2_xg_for, team1_xga, team2_xga,
    team1_rating, team2_rating, team1_possession, team2_possession,
    team1_table_position, team2_table_position,
    team1_points, team2_points,
    rest_gap_hours, team1_short_rest, team2_short_rest,
    team1_market_value_eur, team2_market_value_eur,
    key_absence_diff, competition_name, schedule_density_note,
    created_at
"""

_HISTORICAL_TEAM_SNAPSHOT_COLUMNS = [
    column.strip()
    for column in _HISTORICAL_TEAM_SNAPSHOT_SELECT_COLUMNS.split(",")
]

_HISTORICAL_TEAM_SNAPSHOT_MIGRATION_COLUMNS: list[tuple[str, str]] = [
    ("team1_shots_avg", "REAL"),
    ("team2_shots_avg", "REAL"),
    ("team1_shots_allowed_avg", "REAL"),
    ("team2_shots_allowed_avg", "REAL"),
    ("team1_shots_on_target_avg", "REAL"),
    ("team2_shots_on_target_avg", "REAL"),
    ("team1_shots_on_target_allowed_avg", "REAL"),
    ("team2_shots_on_target_allowed_avg", "REAL"),
    ("team1_corners_avg", "REAL"),
    ("team2_corners_avg", "REAL"),
    ("team1_corners_allowed_avg", "REAL"),
    ("team2_corners_allowed_avg", "REAL"),
]

_HISTORICAL_PROVIDER_COVERAGE_SELECT_COLUMNS = """
    id, historical_match_id, source_key, source_label, status,
    error_type, error_message, duration_ms, provider_name, created_at
"""

_HISTORICAL_PROVIDER_COVERAGE_COLUMNS = [
    column.strip()
    for column in _HISTORICAL_PROVIDER_COVERAGE_SELECT_COLUMNS.split(",")
]

_HISTORICAL_IMPORT_RUN_SELECT_COLUMNS = """
    id, run_type, provider_name, started_at, finished_at, status,
    season_label, competition_name,
    requested_from, requested_to,
    records_inserted, records_updated, records_failed,
    error_summary
"""

_NUMERIC_RECALIBRATION_SELECT_COLUMNS = """
    id, computed_at, scope, competition_key, market,
    bucket_label, bucket_low, bucket_high,
    sample_size, avg_pred_pct, actual_hit_pct, delta_pct
"""

_MARKET_FAMILY_QUALITY_SELECT_COLUMNS = """
    id, computed_at, scope, competition_key, family,
    sample_size, model_brier, market_brier, brier_delta, trust_score
"""


# ── Low-level sync helpers ─────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dumps_list(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads_list(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _json_loads_object_list(value: Optional[str]) -> list[dict]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []


def _prediction_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    data = dict(row)
    for key in _PREDICTION_JSON_COLUMNS:
        data[key] = _json_loads_list(data.get(key))
    for key in _PREDICTION_JSON_OBJECT_LIST_COLUMNS:
        data[key] = _json_loads_object_list(data.get(key))
    data["top_recommendation"] = data.get("top_rec")
    return data


def _bool_to_db(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0


def _feature_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    data = dict(row)
    for key in _FEATURE_BOOL_COLUMNS:
        value = data.get(key)
        if value is not None:
            data[key] = bool(value)
    return data


def _historical_match_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    data = dict(row)
    for key in _HISTORICAL_MATCH_BOOL_COLUMNS:
        value = data.get(key)
        if value is not None:
            data[key] = bool(value)
    return data


def _historical_team_snapshot_row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    data = dict(row)
    for key in _HISTORICAL_TEAM_SNAPSHOT_BOOL_COLUMNS:
        value = data.get(key)
        if value is not None:
            data[key] = bool(value)
    return data


def _normalized_probs_from_decimal_odds(
    odds_one: Optional[float],
    odds_two: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    if (
        odds_one is None
        or odds_two is None
        or odds_one <= 1.0
        or odds_two <= 1.0
    ):
        return None, None
    implied_one = 1.0 / odds_one
    implied_two = 1.0 / odds_two
    total = implied_one + implied_two
    if total <= 0:
        return None, None
    return round((implied_one / total) * 100.0, 4), round((implied_two / total) * 100.0, 4)


def _parse_score(score: str) -> Optional[tuple[int, int]]:
    try:
        parts = score.replace(" ", "").split("-")
        if len(parts) != 2:
            return None
        return int(parts[0]), int(parts[1])
    except (AttributeError, TypeError, ValueError):
        return None


def _parse_prediction_actual_goals(row: dict) -> Optional[tuple[int, int]]:
    home_goals = row.get("actual_goals_home")
    away_goals = row.get("actual_goals_away")
    if home_goals is not None and away_goals is not None:
        try:
            return int(home_goals), int(away_goals)
        except (TypeError, ValueError):
            return None
    score = row.get("actual_score")
    if score is None:
        return None
    return _parse_score(str(score))


def _grade_prediction_market_selection(
    row: dict,
    market_code: str,
    selection_code: str,
) -> Optional[str]:
    market_code = str(market_code or "").strip().upper()
    selection_code = str(selection_code or "").strip().upper()
    actual_result = str(row.get("actual_result") or "").strip().upper()
    goals = _parse_prediction_actual_goals(row)

    if market_code == "1X2":
        expected = {"TEAM1": "W1", "DRAW": "DRAW", "TEAM2": "W2"}.get(selection_code)
        if not expected or actual_result not in {"W1", "DRAW", "W2"}:
            return None
        return "WIN" if actual_result == expected else "LOSS"

    if market_code.startswith("TOTALS_"):
        if goals is None:
            return None
        try:
            threshold = float(market_code.removeprefix("TOTALS_").replace("_", "."))
        except ValueError:
            return None
        total_goals = goals[0] + goals[1]
        if selection_code == "OVER":
            if total_goals > threshold:
                return "WIN"
            if total_goals < threshold:
                return "LOSS"
            return "PUSH"
        if selection_code == "UNDER":
            if total_goals < threshold:
                return "WIN"
            if total_goals > threshold:
                return "LOSS"
            return "PUSH"
        return None

    if market_code == "BTTS":
        if goals is None:
            return None
        both_scored = goals[0] > 0 and goals[1] > 0
        if selection_code == "YES":
            return "WIN" if both_scored else "LOSS"
        if selection_code == "NO":
            return "WIN" if not both_scored else "LOSS"
        return None

    if market_code == "DNB":
        if actual_result not in {"W1", "DRAW", "W2"}:
            return None
        if actual_result == "DRAW":
            return "PUSH"
        expected = {"TEAM1": "W1", "TEAM2": "W2"}.get(selection_code)
        if not expected:
            return None
        return "WIN" if actual_result == expected else "LOSS"

    if market_code == "DOUBLE_CHANCE":
        if actual_result not in {"W1", "DRAW", "W2"}:
            return None
        winning_sets = {
            "1X": {"W1", "DRAW"},
            "12": {"W1", "W2"},
            "X2": {"DRAW", "W2"},
        }
        allowed = winning_sets.get(selection_code)
        if not allowed:
            return None
        return "WIN" if actual_result in allowed else "LOSS"

    return None


def _profit_units_from_outcome(
    outcome: Optional[str],
    odds: Optional[float],
    stake_units: Optional[float],
) -> Optional[float]:
    if outcome is None or odds is None or odds <= 1.0 or stake_units is None or stake_units <= 0:
        return None
    if outcome == "WIN":
        return round((odds - 1.0) * stake_units, 4)
    if outcome == "PUSH":
        return 0.0
    if outcome == "LOSS":
        return round(-stake_units, 4)
    return None


def _bankroll_market_family(market_code: Optional[str]) -> str:
    code = str(market_code or "").strip().upper()
    if code == "1X2":
        return "1X2"
    if code.startswith("TOTALS_"):
        return "Totals"
    if code == "BTTS":
        return "BTTS"
    if code == "DNB":
        return "DNB"
    if code == "DOUBLE_CHANCE":
        return "Double Chance"
    return code or "Other"


def _new_bankroll_segment(label: str) -> dict[str, object]:
    return {
        "segment": label,
        "tracked_bets": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "staked_units": 0.0,
        "profit_units": 0.0,
    }


def _update_bankroll_segment(
    bucket: dict[str, object],
    *,
    stake: float,
    profit: float,
    outcome: str,
) -> None:
    bucket["tracked_bets"] = int(bucket["tracked_bets"]) + 1
    bucket["staked_units"] = float(bucket["staked_units"]) + stake
    bucket["profit_units"] = float(bucket["profit_units"]) + profit
    if outcome == "WIN":
        bucket["wins"] = int(bucket["wins"]) + 1
    elif outcome == "LOSS":
        bucket["losses"] = int(bucket["losses"]) + 1
    elif outcome == "PUSH":
        bucket["pushes"] = int(bucket["pushes"]) + 1


def _finalize_bankroll_segments(grouped: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    def _sample_gate_for_segment(tracked: int, decisive: int) -> tuple[str, int]:
        if tracked >= 20 and decisive >= 10:
            return "stable", 3
        if tracked >= 8 and decisive >= 5:
            return "developing", 2
        if tracked >= 4 and decisive >= 2:
            return "thin", 1
        return "low-sample", 0

    segments: list[dict[str, object]] = []
    for label, bucket in grouped.items():
        tracked = int(bucket["tracked_bets"])
        if tracked <= 0:
            continue
        wins = int(bucket["wins"])
        losses = int(bucket["losses"])
        decisive = wins + losses
        staked = float(bucket["staked_units"])
        profit = float(bucket["profit_units"])
        sample_gate, sample_gate_rank = _sample_gate_for_segment(tracked, decisive)
        segments.append(
            {
                "segment": label,
                "tracked_bets": tracked,
                "wins": wins,
                "losses": int(bucket["losses"]),
                "pushes": int(bucket["pushes"]),
                "staked_units": round(staked, 2),
                "profit_units": round(profit, 2),
                "roi_pct": round((profit * 100.0) / staked, 2) if staked > 0 else None,
                "hit_rate": round((wins * 100.0) / decisive, 2) if decisive else None,
                "sample_gate": sample_gate,
                "sample_gate_rank": sample_gate_rank,
            }
        )
    segments.sort(
        key=lambda item: (
            int(item["sample_gate_rank"]),
            int(item["tracked_bets"]),
            abs(float(item["profit_units"])),
        ),
        reverse=True,
    )
    return segments


def _summarize_bankroll_window(
    entries: list[dict[str, object]],
    *,
    window_size: int = 20,
) -> Optional[dict[str, object]]:
    if not entries:
        return None

    window = entries[-window_size:]
    tracked = len(window)
    wins = sum(1 for item in window if item.get("outcome") == "WIN")
    losses = sum(1 for item in window if item.get("outcome") == "LOSS")
    pushes = sum(1 for item in window if item.get("outcome") == "PUSH")
    staked = sum(float(item.get("stake", 0.0) or 0.0) for item in window)
    profit = sum(float(item.get("profit", 0.0) or 0.0) for item in window)
    decisive = wins + losses

    streak_outcome: Optional[str] = None
    streak_length = 0
    for item in reversed(window):
        outcome = str(item.get("outcome") or "").upper()
        if outcome == "PUSH":
            continue
        if outcome not in {"WIN", "LOSS"}:
            break
        if streak_outcome is None:
            streak_outcome = outcome
            streak_length = 1
            continue
        if outcome == streak_outcome:
            streak_length += 1
        else:
            break

    if streak_outcome == "WIN":
        streak_label = f"W{streak_length}"
    elif streak_outcome == "LOSS":
        streak_label = f"L{streak_length}"
    else:
        streak_label = None

    return {
        "window_size": window_size,
        "tracked_bets": tracked,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "staked_units": round(staked, 2),
        "profit_units": round(profit, 2),
        "roi_pct": round((profit * 100.0) / staked, 2) if staked > 0 else None,
        "hit_rate": round((wins * 100.0) / decisive, 2) if decisive else None,
        "streak_label": streak_label,
    }


# ── Schema init ────────────────────────────────────────────────────────────────

def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS predictions (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        team1              TEXT    NOT NULL,
        team2              TEXT    NOT NULL,
        predicted_at       TEXT    NOT NULL,
        prob_w1            REAL,
        prob_draw          REAL,
        prob_w2            REAL,
        result_confidence  TEXT,
        prob_over_2_5      REAL,
        prob_under_2_5     REAL,
        expected_goals     REAL,
        goals_confidence   TEXT,
        asian_handicap_rec TEXT,
        asian_handicap_confidence TEXT,
        prob_btts_yes      REAL,
        prob_btts_no       REAL,
        btts_confidence    TEXT,
        team1_strengths    TEXT,
        team1_weaknesses   TEXT,
        team2_strengths    TEXT,
        team2_weaknesses   TEXT,
        top_rec            TEXT,
        top_recommendation_odds REAL,
        top_market_code    TEXT,
        top_selection_code TEXT,
        top_confidence_pct REAL,
        risk_level         TEXT,
        stake_units        REAL,
        stake_label        TEXT,
        stake_rationale    TEXT,
        bankroll_mode      TEXT,
        recommendation_mode TEXT,
        policy_flags       TEXT,
        analysis_summary   TEXT,
        data_warnings      TEXT,
        value_bets         TEXT,
        value_candidate_snapshot TEXT,
        market_ah_line     TEXT,
        market_ah_odds     REAL,
        methodology_version TEXT,
        prompt_version     TEXT,
        completeness       REAL,
        actual_result      TEXT,
        actual_score       TEXT,
        actual_goals_home  INTEGER,
        actual_goals_away  INTEGER,
        result_at          TEXT,
        user_id            INTEGER,
        notification_sent  INTEGER NOT NULL DEFAULT 0,
        historical_match_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS prediction_features (
        prediction_id              INTEGER PRIMARY KEY,
        created_at                 TEXT    NOT NULL,
        team1                      TEXT    NOT NULL,
        team2                      TEXT    NOT NULL,
        competition_name           TEXT,
        data_completeness          REAL    NOT NULL,
        successful_source_count    INTEGER NOT NULL DEFAULT 0,
        total_source_count         INTEGER NOT NULL DEFAULT 0,
        is_neutral_venue           INTEGER,
        is_team1_home              INTEGER,
        market_prob_w1             REAL,
        market_prob_draw           REAL,
        market_prob_w2             REAL,
        market_prob_over_2_5       REAL,
        market_prob_under_2_5      REAL,
        market_prob_btts_yes       REAL,
        market_prob_btts_no        REAL,
        llm_prob_w1                REAL,
        llm_prob_draw              REAL,
        llm_prob_w2                REAL,
        llm_prob_over_2_5          REAL,
        llm_prob_under_2_5         REAL,
        llm_prob_btts_yes          REAL,
        llm_prob_btts_no           REAL,
        model_prob_w1              REAL,
        model_prob_draw            REAL,
        model_prob_w2              REAL,
        model_prob_over_2_5        REAL,
        model_prob_under_2_5       REAL,
        model_prob_btts_yes        REAL,
        model_prob_btts_no         REAL,
        model_expected_goals       REAL,
        blend_alpha                REAL    NOT NULL DEFAULT 0,
        top_edge_pct               REAL,
        llm_model_gap_pct          REAL,
        no_bet_triggered           INTEGER NOT NULL DEFAULT 0,
        form_points_last5_diff     REAL,
        form_goal_diff_last5_diff  REAL,
        form_scored_avg_diff       REAL,
        form_conceded_avg_diff     REAL,
        h2h_team1_win_rate         REAL,
        h2h_draw_rate              REAL,
        h2h_avg_goals              REAL,
        xg_diff                    REAL,
        xga_edge                   REAL,
        rating_diff                REAL,
        possession_diff            REAL,
        standings_point_gap        REAL,
        standings_position_gap     REAL,
        rest_gap_hours             REAL,
        team1_short_rest           INTEGER,
        team2_short_rest           INTEGER,
        market_value_ratio         REAL,
        key_absence_diff           INTEGER,
        movement_score_w1          INTEGER,
        movement_score_over        INTEGER,
        favourite_gap_pct          REAL,
        FOREIGN KEY (prediction_id) REFERENCES predictions(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS team_memory (
        team      TEXT NOT NULL,
        key       TEXT NOT NULL,
        value     TEXT NOT NULL,
        saved_at  TEXT NOT NULL,
        PRIMARY KEY (team, key)
    );

    CREATE TABLE IF NOT EXISTS calibration (
        computed_at   TEXT PRIMARY KEY,
        n_total       INTEGER,
        n_with_result INTEGER,
        acc_1x2       REAL,
        brier_1x2     REAL,
        brier_ou      REAL,
        brier_btts    REAL
    );

    CREATE TABLE IF NOT EXISTS rate_limits (
        user_id      INTEGER PRIMARY KEY,
        last_request TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS lessons (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        text                 TEXT    NOT NULL,
        category             TEXT    NOT NULL,
        confidence           REAL    NOT NULL DEFAULT 0.5,
        times_confirmed      INTEGER NOT NULL DEFAULT 0,
        created_at           TEXT    NOT NULL,
        updated_at           TEXT    NOT NULL,
        source_match         TEXT,
        source_prediction_id INTEGER,
        active               INTEGER NOT NULL DEFAULT 1,
        embedding_synced     INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS historical_matches (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_match_key  TEXT    NOT NULL,
        provider_name       TEXT    NOT NULL,
        competition_name    TEXT,
        competition_country TEXT,
        season_label        TEXT,
        stage_name          TEXT,
        match_date_utc      TEXT    NOT NULL,
        kickoff_utc         TEXT,
        team1_name          TEXT    NOT NULL,
        team2_name          TEXT    NOT NULL,
        team1_normalized    TEXT    NOT NULL,
        team2_normalized    TEXT    NOT NULL,
        is_neutral_venue    INTEGER,
        venue_name          TEXT,
        team1_score         INTEGER,
        team2_score         INTEGER,
        actual_result       TEXT,
        went_extra_time     INTEGER NOT NULL DEFAULT 0,
        went_penalties      INTEGER NOT NULL DEFAULT 0,
        status              TEXT    NOT NULL DEFAULT 'FINISHED',
        created_at          TEXT    NOT NULL,
        updated_at          TEXT    NOT NULL,
        UNIQUE(provider_name, provider_match_key)
    );

    CREATE TABLE IF NOT EXISTS historical_market_lines (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        historical_match_id INTEGER NOT NULL,
        snapshot_kind       TEXT    NOT NULL,
        captured_at         TEXT,
        bookmaker_count     INTEGER,
        source_provider     TEXT    NOT NULL,
        odds_w1             REAL,
        odds_draw           REAL,
        odds_w2             REAL,
        implied_prob_w1     REAL,
        implied_prob_draw   REAL,
        implied_prob_w2     REAL,
        odds_over_1_5       REAL,
        odds_under_1_5      REAL,
        odds_over_2_5       REAL,
        odds_under_2_5      REAL,
        odds_over_3_5       REAL,
        odds_under_3_5      REAL,
        odds_btts_yes       REAL,
        odds_btts_no        REAL,
        odds_dnb_team1      REAL,
        odds_dnb_team2      REAL,
        odds_dc_1x          REAL,
        odds_dc_12          REAL,
        odds_dc_x2          REAL,
        ah_main_line        TEXT,
        ah_team1_odds       REAL,
        ah_team2_odds       REAL,
        consensus_favourite TEXT,
        movement_score_team1 REAL,
        movement_score_team2 REAL,
        dispersion_1x2      REAL,
        dispersion_totals   REAL,
        created_at          TEXT    NOT NULL,
        FOREIGN KEY (historical_match_id) REFERENCES historical_matches(id) ON DELETE CASCADE,
        UNIQUE(historical_match_id, snapshot_kind, source_provider)
    );

    CREATE TABLE IF NOT EXISTS historical_team_snapshots (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        historical_match_id      INTEGER NOT NULL UNIQUE,
        snapshot_source          TEXT    NOT NULL,
        captured_at              TEXT,
        data_completeness        REAL,
        successful_source_count  INTEGER,
        total_source_count       INTEGER,
        team1_form_points_last5  REAL,
        team2_form_points_last5  REAL,
        team1_goal_diff_last5    REAL,
        team2_goal_diff_last5    REAL,
        team1_scored_avg         REAL,
        team2_scored_avg         REAL,
        team1_conceded_avg       REAL,
        team2_conceded_avg       REAL,
        team1_shots_avg          REAL,
        team2_shots_avg          REAL,
        team1_shots_allowed_avg  REAL,
        team2_shots_allowed_avg  REAL,
        team1_shots_on_target_avg REAL,
        team2_shots_on_target_avg REAL,
        team1_shots_on_target_allowed_avg REAL,
        team2_shots_on_target_allowed_avg REAL,
        team1_corners_avg        REAL,
        team2_corners_avg        REAL,
        team1_corners_allowed_avg REAL,
        team2_corners_allowed_avg REAL,
        h2h_team1_win_rate       REAL,
        h2h_draw_rate            REAL,
        h2h_avg_goals            REAL,
        team1_xg_for             REAL,
        team2_xg_for             REAL,
        team1_xga                REAL,
        team2_xga                REAL,
        team1_rating             REAL,
        team2_rating             REAL,
        team1_possession         REAL,
        team2_possession         REAL,
        team1_table_position     INTEGER,
        team2_table_position     INTEGER,
        team1_points             INTEGER,
        team2_points             INTEGER,
        rest_gap_hours           REAL,
        team1_short_rest         INTEGER,
        team2_short_rest         INTEGER,
        team1_market_value_eur   REAL,
        team2_market_value_eur   REAL,
        key_absence_diff         INTEGER,
        competition_name         TEXT,
        schedule_density_note    TEXT,
        created_at               TEXT    NOT NULL,
        FOREIGN KEY (historical_match_id) REFERENCES historical_matches(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS historical_provider_coverage (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        historical_match_id INTEGER NOT NULL,
        source_key          TEXT    NOT NULL,
        source_label        TEXT    NOT NULL,
        status              TEXT    NOT NULL,
        error_type          TEXT,
        error_message       TEXT,
        duration_ms         INTEGER,
        provider_name       TEXT,
        created_at          TEXT    NOT NULL,
        FOREIGN KEY (historical_match_id) REFERENCES historical_matches(id) ON DELETE CASCADE,
        UNIQUE(historical_match_id, source_key)
    );

    CREATE TABLE IF NOT EXISTS historical_import_runs (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type          TEXT    NOT NULL,
        provider_name     TEXT    NOT NULL,
        started_at        TEXT    NOT NULL,
        finished_at       TEXT,
        status            TEXT    NOT NULL,
        season_label      TEXT,
        competition_name  TEXT,
        requested_from    TEXT,
        requested_to      TEXT,
        records_inserted  INTEGER NOT NULL DEFAULT 0,
        records_updated   INTEGER NOT NULL DEFAULT 0,
        records_failed    INTEGER NOT NULL DEFAULT 0,
        error_summary     TEXT
    );

    CREATE TABLE IF NOT EXISTS numeric_recalibration_profiles (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        computed_at     TEXT    NOT NULL,
        scope           TEXT    NOT NULL,
        competition_key TEXT,
        market          TEXT    NOT NULL,
        bucket_label    TEXT    NOT NULL,
        bucket_low      REAL    NOT NULL,
        bucket_high     REAL    NOT NULL,
        sample_size     INTEGER NOT NULL,
        avg_pred_pct    REAL    NOT NULL,
        actual_hit_pct  REAL    NOT NULL,
        delta_pct       REAL    NOT NULL,
        UNIQUE(scope, competition_key, market, bucket_label)
    );

    CREATE TABLE IF NOT EXISTS market_family_quality_profiles (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        computed_at     TEXT    NOT NULL,
        scope           TEXT    NOT NULL,
        competition_key TEXT,
        family          TEXT    NOT NULL,
        sample_size     INTEGER NOT NULL,
        model_brier     REAL    NOT NULL,
        market_brier    REAL    NOT NULL,
        brier_delta     REAL    NOT NULL,
        trust_score     REAL    NOT NULL,
        UNIQUE(scope, competition_key, family)
    );

    CREATE TABLE IF NOT EXISTS clv_tracking (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        prediction_id   INTEGER NOT NULL,
        team1           TEXT    NOT NULL,
        team2           TEXT    NOT NULL,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        top_market      TEXT,
        top_selection   TEXT,
        placement_odds  REAL,
        pin_w1          REAL,
        pin_draw        REAL,
        pin_w2          REAL,
        pin_over_2_5    REAL,
        pin_under_2_5   REAL,
        close_w1        REAL,
        close_draw      REAL,
        close_w2        REAL,
        close_over_2_5  REAL,
        close_under_2_5 REAL,
        clv_1x2_pct     REAL,
        clv_ou_pct      REAL,
        clv_top_pick_pct REAL,
        resolved        INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (prediction_id) REFERENCES predictions(id)
    );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_matches_match_date ON historical_matches(match_date_utc)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_matches_teams ON historical_matches(team1_normalized, team2_normalized)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_market_lines_match ON historical_market_lines(historical_match_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_provider_coverage_match ON historical_provider_coverage(historical_match_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_historical_import_runs_started ON historical_import_runs(started_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_numeric_recalibration_lookup "
        "ON numeric_recalibration_profiles(scope, competition_key, market, bucket_low)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_family_quality_lookup "
        "ON market_family_quality_profiles(scope, competition_key, family)"
    )
    conn.commit()

    # Idempotent migrations for existing databases
    for col, coltype in _PREDICTION_MIGRATION_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} {coltype}")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    for col, coltype in _HISTORICAL_TEAM_SNAPSHOT_MIGRATION_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE historical_team_snapshots ADD COLUMN {col} {coltype}")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    # Migration: add movement tracking columns to prediction_features
    for col, coltype in [
        ("movement_score_w1", "INTEGER"),
        ("movement_score_over", "INTEGER"),
        ("favourite_gap_pct", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE prediction_features ADD COLUMN {col} {coltype}")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


async def init_db() -> None:
    """Call once at startup to create tables if missing."""
    def _init():
        with _connect() as conn:
            _create_tables(conn)
    await asyncio.to_thread(_init)
    logger.info("Database initialised at %s", DB_PATH)


# ── Predictions ────────────────────────────────────────────────────────────────

def _save_prediction_sync(
    team1: str,
    team2: str,
    prob_w1: float,
    prob_draw: float,
    prob_w2: float,
    result_confidence: str,
    prob_over_2_5: float,
    prob_under_2_5: float,
    expected_goals: float,
    goals_confidence: str,
    asian_handicap_rec: str,
    asian_handicap_confidence: str,
    prob_btts_yes: float,
    prob_btts_no: float,
    btts_confidence: str,
    team1_strengths: list[str],
    team1_weaknesses: list[str],
    team2_strengths: list[str],
    team2_weaknesses: list[str],
    top_rec: str,
    top_recommendation_odds: Optional[float],
    top_market_code: Optional[str],
    top_selection_code: Optional[str],
    top_confidence_pct: float,
    risk_level: str,
    stake_units: Optional[float],
    stake_label: Optional[str],
    stake_rationale: Optional[str],
    bankroll_mode: Optional[str],
    recommendation_mode: Optional[str],
    policy_flags: list[str],
    analysis_summary: str,
    data_warnings: list[str],
    value_bets: list[str],
    value_candidate_snapshot: list[dict],
    market_ah_line: Optional[str],
    market_ah_odds: Optional[float],
    methodology_version: Optional[str],
    prompt_version: Optional[str],
    completeness: float,
    user_id: Optional[int] = None,
) -> int:
    insert_columns = (
        "team1", "team2", "predicted_at",
        "prob_w1", "prob_draw", "prob_w2", "result_confidence",
        "prob_over_2_5", "prob_under_2_5", "expected_goals", "goals_confidence",
        "asian_handicap_rec", "asian_handicap_confidence",
        "prob_btts_yes", "prob_btts_no", "btts_confidence",
        "team1_strengths", "team1_weaknesses", "team2_strengths", "team2_weaknesses",
        "top_rec", "top_recommendation_odds", "top_market_code", "top_selection_code",
        "top_confidence_pct", "risk_level", "stake_units", "stake_label", "stake_rationale", "bankroll_mode", "recommendation_mode",
        "policy_flags", "analysis_summary", "data_warnings", "value_bets", "value_candidate_snapshot",
        "market_ah_line", "market_ah_odds",
        "methodology_version", "prompt_version",
        "completeness", "user_id",
    )
    values = (
        team1, team2, _now_utc(),
        prob_w1, prob_draw, prob_w2, result_confidence,
        prob_over_2_5, prob_under_2_5, expected_goals, goals_confidence,
        asian_handicap_rec, asian_handicap_confidence,
        prob_btts_yes, prob_btts_no, btts_confidence,
        _json_dumps_list(team1_strengths),
        _json_dumps_list(team1_weaknesses),
        _json_dumps_list(team2_strengths),
        _json_dumps_list(team2_weaknesses),
        top_rec, top_recommendation_odds, top_market_code, top_selection_code,
        top_confidence_pct, risk_level, stake_units, stake_label, stake_rationale, bankroll_mode, recommendation_mode,
        _json_dumps_list(policy_flags),
        analysis_summary,
        _json_dumps_list(data_warnings),
        _json_dumps_list(value_bets),
        json.dumps(value_candidate_snapshot, ensure_ascii=False),
        market_ah_line, market_ah_odds,
        methodology_version, prompt_version,
        completeness, user_id,
    )
    with _connect() as conn:
        cur = conn.execute(
            f"""
            INSERT INTO predictions
              ({", ".join(insert_columns)})
            VALUES ({",".join("?" for _ in insert_columns)})
            """,
            values,
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def save_prediction(report, completeness: float, user_id: Optional[int] = None) -> int:
    """
    Persist a PredictionReport to the predictions table.
    Returns the new row id.
    """
    row_id = await asyncio.to_thread(
        _save_prediction_sync,
        report.team1,
        report.team2,
        report.prob_w1,
        report.prob_draw,
        report.prob_w2,
        report.result_confidence,
        report.prob_over_2_5,
        report.prob_under_2_5,
        report.expected_goals,
        report.goals_confidence,
        report.asian_handicap_rec,
        report.asian_handicap_confidence,
        report.prob_btts_yes,
        report.prob_btts_no,
        report.btts_confidence,
        report.team1_strengths,
        report.team1_weaknesses,
        report.team2_strengths,
        report.team2_weaknesses,
        report.top_recommendation,
        report.top_recommendation_odds,
        report.top_market_code,
        report.top_selection_code,
        report.top_confidence_pct,
        report.risk_level,
        report.stake_units,
        report.stake_label,
        report.stake_rationale,
        report.bankroll_mode,
        report.recommendation_mode,
        report.policy_flags,
        report.analysis_summary,
        report.data_warnings,
        report.value_bets,
        [item.model_dump(mode="json") for item in report.value_candidate_snapshot],
        report.market_ah_line,
        report.market_ah_odds,
        report.methodology_version,
        report.prompt_version,
        completeness,
        user_id,
    )
    if getattr(report, "feature_snapshot", None) is not None:
        await save_prediction_features(row_id, report.feature_snapshot)
    # Save CLV tracking data if Pinnacle odds available
    pin = getattr(report, "pinnacle_odds_snapshot", None)
    if pin is not None and (pin.pin_w1 is not None or pin.pin_over_2_5 is not None):
        await save_clv_tracking(
            prediction_id=row_id,
            team1=report.team1,
            team2=report.team2,
            top_market=report.top_market_code,
            top_selection=report.top_selection_code,
            placement_odds=report.top_recommendation_odds,
            snapshot=pin,
        )
    logger.info("Saved prediction id=%d: %s vs %s", row_id, report.team1, report.team2)
    return row_id


def _save_prediction_features_sync(
    prediction_id: int,
    snapshot: PredictionFeatureSnapshot,
) -> None:
    payload = snapshot.model_dump()
    payload["prediction_id"] = prediction_id
    payload["created_at"] = payload.get("created_at") or _now_utc()
    payload["is_neutral_venue"] = _bool_to_db(snapshot.is_neutral_venue)
    payload["is_team1_home"] = _bool_to_db(snapshot.is_team1_home)
    payload["no_bet_triggered"] = _bool_to_db(snapshot.no_bet_triggered) or 0
    payload["team1_short_rest"] = _bool_to_db(snapshot.team1_short_rest)
    payload["team2_short_rest"] = _bool_to_db(snapshot.team2_short_rest)

    columns = [column.strip() for column in _FEATURE_SELECT_COLUMNS.split(",")]
    placeholders = ",".join("?" for _ in columns)
    assignments = ", ".join(
        f"{column}=excluded.{column}"
        for column in columns
        if column != "prediction_id"
    )

    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO prediction_features ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(prediction_id) DO UPDATE SET {assignments}
            """,
            tuple(payload.get(column) for column in columns),
        )
        conn.commit()


async def save_prediction_features(
    prediction_id: int,
    snapshot: PredictionFeatureSnapshot,
) -> None:
    await asyncio.to_thread(_save_prediction_features_sync, prediction_id, snapshot)


def _get_prediction_features_sync(prediction_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {_FEATURE_SELECT_COLUMNS}
            FROM prediction_features
            WHERE prediction_id=?
            """,
            (prediction_id,),
        ).fetchone()
    return _feature_row_to_dict(row)


async def get_prediction_features(prediction_id: int) -> Optional[dict]:
    return await asyncio.to_thread(_get_prediction_features_sync, prediction_id)


def _find_latest_unresolved_prediction_sync(team1: str, team2: str) -> Optional[dict]:
    t1_l, t2_l = team1.strip().lower(), team2.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, team1, team2, predicted_at, top_rec, user_id
            FROM predictions
            WHERE actual_result IS NULL
              AND (
                (LOWER(team1)=? AND LOWER(team2)=?) OR
                (LOWER(team1)=? AND LOWER(team2)=?)
              )
            ORDER BY predicted_at DESC
            LIMIT 1
            """,
            (t1_l, t2_l, t2_l, t1_l),
        ).fetchone()
    return dict(row) if row else None


async def find_latest_unresolved_prediction(team1: str, team2: str) -> Optional[dict]:
    return await asyncio.to_thread(_find_latest_unresolved_prediction_sync, team1, team2)


def _mark_result_by_prediction_id_sync(
    prediction_id: int,
    score: str,
    *,
    score_team1: Optional[str] = None,
    score_team2: Optional[str] = None,
) -> bool:
    parsed = _parse_score(score)
    if parsed is None:
        logger.warning("Could not parse score '%s'", score)
        return False

    score_t1, score_t2 = parsed

    with _connect() as conn:
        stored = conn.execute(
            "SELECT team1, team2 FROM predictions WHERE id=? AND actual_result IS NULL",
            (prediction_id,),
        ).fetchone()

        if not stored:
            return False

        stored_team1 = stored["team1"]
        stored_team2 = stored["team2"]
        if score_team1 and score_team2:
            input_team1 = score_team1.strip().lower()
            input_team2 = score_team2.strip().lower()
            stored_team1_l = stored_team1.strip().lower()
            stored_team2_l = stored_team2.strip().lower()
            if input_team1 == stored_team2_l and input_team2 == stored_team1_l:
                score_t1, score_t2 = score_t2, score_t1

        if score_t1 > score_t2:
            actual_result = "W1"
        elif score_t1 < score_t2:
            actual_result = "W2"
        else:
            actual_result = "DRAW"

        normalized_score = f"{score_t1}-{score_t2}"
        conn.execute(
            """
            UPDATE predictions
            SET actual_result=?, actual_score=?, result_at=?,
                actual_goals_home=?, actual_goals_away=?
            WHERE id=?
            """,
            (actual_result, normalized_score, _now_utc(), score_t1, score_t2, prediction_id),
        )
        conn.commit()
        logger.info(
            "Marked result for prediction id=%d: %s → %s",
            prediction_id, normalized_score, actual_result,
        )
        return True


async def mark_result_by_prediction_id(
    prediction_id: int,
    score: str,
    *,
    score_team1: Optional[str] = None,
    score_team2: Optional[str] = None,
) -> bool:
    return await asyncio.to_thread(
        _mark_result_by_prediction_id_sync,
        prediction_id,
        score,
        score_team1=score_team1,
        score_team2=score_team2,
    )


def _mark_result_sync(team1: str, team2: str, score: str) -> bool:
    row = _find_latest_unresolved_prediction_sync(team1, team2)
    if not row:
        return False
    return _mark_result_by_prediction_id_sync(
        row["id"],
        score,
        score_team1=team1,
        score_team2=team2,
    )


async def mark_result(team1: str, team2: str, score: str) -> bool:
    return await asyncio.to_thread(_mark_result_sync, team1, team2, score)


def _mark_notification_sent_sync(prediction_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE predictions SET notification_sent=1 WHERE id=?",
            (prediction_id,),
        )
        conn.commit()


async def mark_notification_sent(prediction_id: int) -> None:
    await asyncio.to_thread(_mark_notification_sent_sync, prediction_id)


def _get_unresolved_sync(older_than_hours: float = 3.0) -> list[dict]:
    """Return predictions without a result, made more than N hours ago."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    cutoff_str = cutoff.isoformat()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, team1, team2, predicted_at, top_rec, user_id
            FROM predictions
            WHERE actual_result IS NULL
              AND notification_sent = 0
              AND predicted_at <= ?
            ORDER BY predicted_at ASC
            """,
            (cutoff_str,),
        ).fetchall()
        return [dict(r) for r in rows]


async def get_unresolved(older_than_hours: float = 3.0) -> list[dict]:
    return await asyncio.to_thread(_get_unresolved_sync, older_than_hours)


# ── Team memory ────────────────────────────────────────────────────────────────

def _save_team_snapshot_sync(team: str, key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO team_memory (team, key, value, saved_at)
            VALUES (?,?,?,?)
            ON CONFLICT(team, key) DO UPDATE SET value=excluded.value, saved_at=excluded.saved_at
            """,
            (team.strip(), key, value, _now_utc()),
        )
        conn.commit()


async def save_team_snapshot(team: str, key: str, value: str) -> None:
    await asyncio.to_thread(_save_team_snapshot_sync, team, key, value)


def _get_team_snapshot_sync(team: str) -> dict[str, tuple[str, str]]:
    """Returns {key: (value, saved_at)} for a team."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, value, saved_at FROM team_memory WHERE team=? ORDER BY key",
            (team.strip(),),
        ).fetchall()
        return {r["key"]: (r["value"], r["saved_at"]) for r in rows}


async def get_team_snapshot(team: str) -> dict[str, tuple[str, str]]:
    return await asyncio.to_thread(_get_team_snapshot_sync, team)


# ── Calibration ────────────────────────────────────────────────────────────────

def _brier_score(probs: list[tuple[float, ...]], outcomes: list[tuple[float, ...]]) -> float:
    """
    Brier score = mean of sum((p_i - o_i)^2) over all predictions.
    probs: list of probability tuples (summing to 1.0)
    outcomes: list of one-hot tuples
    """
    if not probs:
        return float("nan")
    total = 0.0
    for p_vec, o_vec in zip(probs, outcomes):
        total += sum((p - o) ** 2 for p, o in zip(p_vec, o_vec))
    return total / len(probs)


def _recompute_calibration_sync() -> Optional[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT prob_w1, prob_draw, prob_w2, prob_over_2_5, prob_btts_yes,
                   actual_result, actual_goals_home, actual_goals_away
            FROM predictions
            WHERE actual_result IS NOT NULL
              AND prob_w1 IS NOT NULL
            """,
        ).fetchall()
        n_total_row = conn.execute("SELECT COUNT(*) AS c FROM predictions").fetchone()

    n_total = n_total_row["c"] if n_total_row else 0
    n_with_result = len(rows)

    if n_with_result == 0:
        return None

    # 1X2 accuracy + Brier
    correct_1x2 = 0
    probs_1x2, outcomes_1x2 = [], []
    probs_ou, outcomes_ou = [], []
    probs_btts, outcomes_btts = [], []

    for r in rows:
        p1 = (r["prob_w1"] or 0) / 100
        pd = (r["prob_draw"] or 0) / 100
        p2 = (r["prob_w2"] or 0) / 100
        ar = r["actual_result"]

        # Predict label = highest prob
        predicted = max(
            [("W1", p1), ("DRAW", pd), ("W2", p2)],
            key=lambda x: x[1],
        )[0]
        if predicted == ar:
            correct_1x2 += 1

        o1 = 1.0 if ar == "W1" else 0.0
        od = 1.0 if ar == "DRAW" else 0.0
        o2 = 1.0 if ar == "W2" else 0.0
        probs_1x2.append((p1, pd, p2))
        outcomes_1x2.append((o1, od, o2))

        # O/U 2.5 Brier — requires actual goals columns
        if (r["prob_over_2_5"] is not None
                and r["actual_goals_home"] is not None
                and r["actual_goals_away"] is not None):
            total_goals = r["actual_goals_home"] + r["actual_goals_away"]
            pov = r["prob_over_2_5"] / 100
            o_over = 1.0 if total_goals > 2.5 else 0.0
            probs_ou.append((pov,))
            outcomes_ou.append((o_over,))

        # BTTS Brier — requires actual goals columns
        if (r["prob_btts_yes"] is not None
                and r["actual_goals_home"] is not None
                and r["actual_goals_away"] is not None):
            py = r["prob_btts_yes"] / 100
            btts = 1.0 if (r["actual_goals_home"] > 0 and r["actual_goals_away"] > 0) else 0.0
            probs_btts.append((py,))
            outcomes_btts.append((btts,))

    acc_1x2 = round(correct_1x2 / n_with_result * 100, 2) if n_with_result else 0.0
    bs_1x2 = round(_brier_score(probs_1x2, outcomes_1x2), 4)
    bs_ou = round(_brier_score(probs_ou, outcomes_ou), 4) if probs_ou else None
    bs_btts = round(_brier_score(probs_btts, outcomes_btts), 4) if probs_btts else None

    stats = {
        "computed_at": _now_utc(),
        "n_total": n_total,
        "n_with_result": n_with_result,
        "acc_1x2": acc_1x2,
        "brier_1x2": bs_1x2,
        "brier_ou": bs_ou,
        "brier_btts": bs_btts,
    }

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO calibration
              (computed_at, n_total, n_with_result, acc_1x2, brier_1x2, brier_ou, brier_btts)
            VALUES (:computed_at,:n_total,:n_with_result,:acc_1x2,:brier_1x2,:brier_ou,:brier_btts)
            ON CONFLICT(computed_at) DO UPDATE SET
              n_total=excluded.n_total,
              n_with_result=excluded.n_with_result,
              acc_1x2=excluded.acc_1x2,
              brier_1x2=excluded.brier_1x2,
              brier_ou=excluded.brier_ou,
              brier_btts=excluded.brier_btts
            """,
            stats,
        )
        conn.commit()

    logger.info(
        "Calibration updated: %d results, acc_1x2=%.1f%%, Brier=%.4f",
        n_with_result, acc_1x2, bs_1x2,
    )
    return stats


async def recompute_calibration() -> Optional[dict]:
    return await asyncio.to_thread(_recompute_calibration_sync)


def _get_calibration_stats_sync() -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM calibration ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


async def get_calibration_stats() -> Optional[dict]:
    return await asyncio.to_thread(_get_calibration_stats_sync)


def _get_calibration_curve_sync() -> Optional[dict]:
    """Compute calibration curve bins and ECE for 1X2 predictions.

    Bins resolved predictions by the confidence in the predicted outcome
    (max of prob_w1/draw/w2), then measures actual accuracy per bin.
    Returns dict with 'bins' list and 'ece' float.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT prob_w1, prob_draw, prob_w2, actual_result
            FROM predictions
            WHERE actual_result IS NOT NULL
              AND actual_result IN ('W1', 'DRAW', 'W2')
              AND prob_w1 IS NOT NULL
            """,
        ).fetchall()

    if len(rows) < 5:
        return None

    # Build per-prediction (confidence, correct) pairs
    entries: list[tuple[float, int]] = []
    for r in rows:
        p1 = (r["prob_w1"] or 0) / 100.0
        pd = (r["prob_draw"] or 0) / 100.0
        p2 = (r["prob_w2"] or 0) / 100.0
        ar = r["actual_result"]

        predicted_label, predicted_conf = max(
            [("W1", p1), ("DRAW", pd), ("W2", p2)],
            key=lambda x: x[1],
        )
        correct = 1 if predicted_label == ar else 0
        entries.append((predicted_conf, correct))

    # Bin into 10 equal-width bins: [0.0–0.1), [0.1–0.2), ..., [0.9–1.0]
    n_bins = 10
    bins: list[dict] = []
    total_n = len(entries)
    ece = 0.0

    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        in_bin = [(c, cr) for c, cr in entries if lo <= c < hi or (i == n_bins - 1 and c == 1.0 and lo <= c)]
        n = len(in_bin)
        if n == 0:
            bins.append({
                "range_lo": lo, "range_hi": hi, "n": 0,
                "avg_confidence": 0.0, "accuracy": 0.0, "gap": 0.0,
            })
            continue
        avg_conf = sum(c for c, _ in in_bin) / n
        accuracy = sum(cr for _, cr in in_bin) / n
        gap = abs(avg_conf - accuracy)
        ece += (n / total_n) * gap
        bins.append({
            "range_lo": lo, "range_hi": hi, "n": n,
            "avg_confidence": round(avg_conf * 100, 1),
            "accuracy": round(accuracy * 100, 1),
            "gap": round(gap * 100, 1),
        })

    return {
        "bins": bins,
        "ece": round(ece * 100, 2),
        "n_total": total_n,
    }


async def get_calibration_curve() -> Optional[dict]:
    return await asyncio.to_thread(_get_calibration_curve_sync)


def _get_odds_movement_stats_sync() -> Optional[dict]:
    """Aggregate odds movement statistics for resolved predictions."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                f.movement_score_w1,
                f.movement_score_over,
                p.actual_result,
                p.top_selection_code,
                p.top_market_code
            FROM prediction_features f
            JOIN predictions p ON p.id = f.prediction_id
            WHERE p.actual_result IS NOT NULL
              AND (f.movement_score_w1 IS NOT NULL OR f.movement_score_over IS NOT NULL)
            """,
        ).fetchall()

    if len(rows) < 3:
        return None

    total = len(rows)
    with_1x2_move = 0
    with_1x2_move_correct = 0
    sharp_aligned = 0       # movement was in same direction as our pick
    sharp_aligned_won = 0
    sharp_against = 0
    sharp_against_won = 0

    for r in rows:
        ms_w1 = r["movement_score_w1"]
        sel = r["top_selection_code"]
        ar = r["actual_result"]
        market = r["top_market_code"]

        if ms_w1 is not None and ms_w1 != 0 and market == "1X2" and sel in ("W1", "W2"):
            with_1x2_move += 1
            won = sel == ar
            if won:
                with_1x2_move_correct += 1

            # Sharp money direction: positive movement_score = odds on team1 shortening (money on team1)
            sharp_on_w1 = ms_w1 > 0
            our_pick_w1 = sel == "W1"
            if sharp_on_w1 == our_pick_w1:
                sharp_aligned += 1
                if won:
                    sharp_aligned_won += 1
            else:
                sharp_against += 1
                if won:
                    sharp_against_won += 1

    return {
        "total": total,
        "with_1x2_move": with_1x2_move,
        "with_1x2_move_correct": with_1x2_move_correct,
        "sharp_aligned": sharp_aligned,
        "sharp_aligned_won": sharp_aligned_won,
        "sharp_against": sharp_against,
        "sharp_against_won": sharp_against_won,
    }


async def get_odds_movement_stats() -> Optional[dict]:
    return await asyncio.to_thread(_get_odds_movement_stats_sync)


def _get_per_league_calibration_sync() -> list[dict]:
    """Per-league 1X2 accuracy breakdown from resolved predictions."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                COALESCE(f.competition_name, 'Unknown') AS league,
                COUNT(*) AS n,
                SUM(CASE
                    WHEN (p.top_selection_code = 'W1' AND p.actual_result = 'W1')
                      OR (p.top_selection_code = 'DRAW' AND p.actual_result = 'DRAW')
                      OR (p.top_selection_code = 'W2' AND p.actual_result = 'W2')
                    THEN 1 ELSE 0
                END) AS correct
            FROM predictions p
            LEFT JOIN prediction_features f ON f.prediction_id = p.id
            WHERE p.actual_result IS NOT NULL
              AND p.top_market_code = '1X2'
              AND p.top_selection_code IN ('W1', 'DRAW', 'W2')
            GROUP BY COALESCE(f.competition_name, 'Unknown')
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        n = int(row["n"])
        correct = int(row["correct"])
        result.append({
            "league": row["league"],
            "n": n,
            "correct": correct,
            "accuracy_pct": round(correct / n * 100, 1) if n > 0 else 0.0,
        })
    return result


async def get_per_league_calibration() -> list[dict]:
    return await asyncio.to_thread(_get_per_league_calibration_sync)


def _get_paper_bankroll_stats_sync(
    starting_bankroll_units: float = 100.0,
) -> Optional[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.predicted_at,
                p.result_at,
                p.actual_result,
                p.actual_score,
                p.actual_goals_home,
                p.actual_goals_away,
                p.top_market_code,
                p.top_selection_code,
                p.top_recommendation_odds,
                p.stake_units,
                p.risk_level,
                p.bankroll_mode,
                p.recommendation_mode,
                p.policy_flags,
                f.competition_name
            FROM predictions p
            LEFT JOIN prediction_features f
              ON f.prediction_id = p.id
            WHERE actual_result IS NOT NULL
              AND top_market_code IS NOT NULL
              AND top_market_code != 'NO_BET'
              AND stake_units IS NOT NULL
              AND stake_units > 0
            ORDER BY COALESCE(result_at, predicted_at), id
            """
        ).fetchall()

    if not rows:
        return None

    evaluated = 0
    wins = 0
    losses = 0
    pushes = 0
    staked_units = 0.0
    profit_units = 0.0
    current_bankroll = float(starting_bankroll_units)
    peak_bankroll = current_bankroll
    max_drawdown_units = 0.0
    by_market: dict[str, dict[str, object]] = {}
    by_competition: dict[str, dict[str, object]] = {}
    by_competition_policy: dict[str, dict[str, object]] = {}
    by_risk: dict[str, dict[str, object]] = {}
    by_bankroll_mode: dict[str, dict[str, object]] = {}
    by_recommendation_mode: dict[str, dict[str, object]] = {}
    by_policy_flag: dict[str, dict[str, object]] = {}
    evaluated_entries: list[dict[str, object]] = []

    for raw_row in rows:
        row = dict(raw_row)
        try:
            odds = float(row["top_recommendation_odds"]) if row["top_recommendation_odds"] is not None else None
            stake = float(row["stake_units"]) if row["stake_units"] is not None else None
        except (TypeError, ValueError):
            continue
        outcome = _grade_prediction_market_selection(
            row,
            str(row.get("top_market_code") or ""),
            str(row.get("top_selection_code") or ""),
        )
        profit = _profit_units_from_outcome(outcome, odds, stake)
        if outcome is None or profit is None or stake is None:
            continue

        evaluated += 1
        staked_units += stake
        profit_units += profit
        current_bankroll += profit
        peak_bankroll = max(peak_bankroll, current_bankroll)
        max_drawdown_units = max(max_drawdown_units, peak_bankroll - current_bankroll)
        evaluated_entries.append(
            {
                "stake": stake,
                "profit": profit,
                "outcome": outcome,
            }
        )

        market_key = _bankroll_market_family(row.get("top_market_code"))
        market_bucket = by_market.setdefault(market_key, _new_bankroll_segment(market_key))
        _update_bankroll_segment(market_bucket, stake=stake, profit=profit, outcome=outcome)

        competition_name = str(row.get("competition_name") or "").strip() or "Unknown"
        competition_bucket = by_competition.setdefault(
            competition_name,
            _new_bankroll_segment(competition_name),
        )
        _update_bankroll_segment(competition_bucket, stake=stake, profit=profit, outcome=outcome)

        risk_level = str(row.get("risk_level") or "").strip().upper() or "UNKNOWN"
        risk_bucket = by_risk.setdefault(risk_level, _new_bankroll_segment(risk_level))
        _update_bankroll_segment(risk_bucket, stake=stake, profit=profit, outcome=outcome)

        bankroll_mode = str(row.get("bankroll_mode") or "").strip().lower() or "unknown"
        bankroll_bucket = by_bankroll_mode.setdefault(
            bankroll_mode,
            _new_bankroll_segment(bankroll_mode),
        )
        _update_bankroll_segment(bankroll_bucket, stake=stake, profit=profit, outcome=outcome)

        recommendation_mode = str(row.get("recommendation_mode") or "").strip().lower() or "unknown"
        recommendation_bucket = by_recommendation_mode.setdefault(
            recommendation_mode,
            _new_bankroll_segment(recommendation_mode),
        )
        _update_bankroll_segment(recommendation_bucket, stake=stake, profit=profit, outcome=outcome)

        policy_flags = _json_loads_list(row.get("policy_flags"))
        for policy_flag in policy_flags:
            flag = str(policy_flag).strip()
            if not flag:
                continue
            flag_bucket = by_policy_flag.setdefault(flag, _new_bankroll_segment(flag))
            _update_bankroll_segment(flag_bucket, stake=stake, profit=profit, outcome=outcome)
            competition_policy_key = f"{competition_name} / {flag}"
            competition_policy_bucket = by_competition_policy.setdefault(
                competition_policy_key,
                _new_bankroll_segment(competition_policy_key),
            )
            _update_bankroll_segment(
                competition_policy_bucket,
                stake=stake,
                profit=profit,
                outcome=outcome,
            )

        if outcome == "WIN":
            wins += 1
        elif outcome == "LOSS":
            losses += 1
        elif outcome == "PUSH":
            pushes += 1

    if evaluated == 0 or staked_units <= 0:
        return None

    decisive = wins + losses
    return {
        "starting_bankroll_units": round(starting_bankroll_units, 2),
        "current_bankroll_units": round(current_bankroll, 2),
        "peak_bankroll_units": round(peak_bankroll, 2),
        "max_drawdown_units": round(max_drawdown_units, 2),
        "tracked_bets": evaluated,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round((wins * 100.0) / decisive, 2) if decisive else None,
        "staked_units": round(staked_units, 2),
        "profit_units": round(profit_units, 2),
        "roi_pct": round((profit_units * 100.0) / staked_units, 2),
        "avg_stake_units": round(staked_units / evaluated, 2),
        "by_market": _finalize_bankroll_segments(by_market),
        "by_competition": _finalize_bankroll_segments(by_competition),
        "by_competition_policy": _finalize_bankroll_segments(by_competition_policy),
        "by_risk": _finalize_bankroll_segments(by_risk),
        "by_bankroll_mode": _finalize_bankroll_segments(by_bankroll_mode),
        "by_recommendation_mode": _finalize_bankroll_segments(by_recommendation_mode),
        "by_policy_flag": _finalize_bankroll_segments(by_policy_flag),
        "recent_form_20": _summarize_bankroll_window(evaluated_entries, window_size=20),
    }


async def get_paper_bankroll_stats(
    starting_bankroll_units: float = 100.0,
) -> Optional[dict]:
    return await asyncio.to_thread(
        _get_paper_bankroll_stats_sync,
        starting_bankroll_units,
    )


def _get_bankroll_guardrails_sync() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_str = day_start.isoformat()

    with _connect() as conn:
        today_recommendations = conn.execute(
            """
            SELECT
                COUNT(*) AS recommended_bets_today,
                COALESCE(SUM(stake_units), 0.0) AS staked_today_units
            FROM predictions
            WHERE predicted_at >= ?
              AND stake_units IS NOT NULL
              AND stake_units > 0
              AND top_rec IS NOT NULL
              AND LOWER(top_rec) NOT LIKE 'no bet%'
            """,
            (day_start_str,),
        ).fetchone()
        resolved_rows = conn.execute(
            """
            SELECT
                id,
                predicted_at,
                result_at,
                actual_result,
                actual_score,
                actual_goals_home,
                actual_goals_away,
                top_market_code,
                top_selection_code,
                top_recommendation_odds,
                stake_units
            FROM predictions
            WHERE actual_result IS NOT NULL
              AND top_market_code IS NOT NULL
              AND top_market_code != 'NO_BET'
              AND stake_units IS NOT NULL
              AND stake_units > 0
            ORDER BY COALESCE(result_at, predicted_at) DESC, id DESC
            """
        ).fetchall()

    recommended_bets_today = int(
        (today_recommendations["recommended_bets_today"] if today_recommendations else 0) or 0
    )
    staked_today_units = float(
        (today_recommendations["staked_today_units"] if today_recommendations else 0.0) or 0.0
    )

    settled_bets_today = 0
    settled_profit_today_units = 0.0
    recent_loss_streak = 0
    streak_open = True

    for raw_row in resolved_rows:
        row = dict(raw_row)
        try:
            odds = (
                float(row["top_recommendation_odds"])
                if row["top_recommendation_odds"] is not None
                else None
            )
            stake = float(row["stake_units"]) if row["stake_units"] is not None else None
        except (TypeError, ValueError):
            continue

        outcome = _grade_prediction_market_selection(
            row,
            str(row.get("top_market_code") or ""),
            str(row.get("top_selection_code") or ""),
        )
        profit = _profit_units_from_outcome(outcome, odds, stake)
        if outcome is None or profit is None or stake is None:
            continue

        resolved_at = _parse_iso_datetime(row.get("result_at")) or _parse_iso_datetime(row.get("predicted_at"))
        if resolved_at is not None and resolved_at >= day_start:
            settled_bets_today += 1
            settled_profit_today_units += profit

        if streak_open:
            if outcome == "LOSS":
                recent_loss_streak += 1
            else:
                streak_open = False

    return {
        "as_of_utc": now.isoformat(),
        "day_start_utc": day_start_str,
        "recommended_bets_today": recommended_bets_today,
        "staked_today_units": round(staked_today_units, 2),
        "settled_bets_today": settled_bets_today,
        "settled_profit_today_units": round(settled_profit_today_units, 2),
        "recent_loss_streak": recent_loss_streak,
    }


async def get_bankroll_guardrails() -> dict[str, object]:
    return await asyncio.to_thread(_get_bankroll_guardrails_sync)


def _get_prediction_count_sync() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM predictions").fetchone()
        return row["c"] if row else 0


async def get_prediction_count() -> int:
    return await asyncio.to_thread(_get_prediction_count_sync)


# ── Rate limits ────────────────────────────────────────────────────────────────

def _get_last_request_sync(user_id: int) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_request FROM rate_limits WHERE user_id=?", (user_id,)
        ).fetchone()
        return row["last_request"] if row else None


async def get_last_request(user_id: int) -> Optional[str]:
    """Returns ISO-8601 UTC string of last request time, or None."""
    return await asyncio.to_thread(_get_last_request_sync, user_id)


def _set_last_request_sync(user_id: int, ts: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rate_limits (user_id, last_request) VALUES (?,?)
            ON CONFLICT(user_id) DO UPDATE SET last_request=excluded.last_request
            """,
            (user_id, ts),
        )
        conn.commit()


async def set_last_request(user_id: int) -> None:
    """Record current UTC time as the last request time for this user."""
    await asyncio.to_thread(_set_last_request_sync, user_id, _now_utc())


# ── History ────────────────────────────────────────────────────────────────────

def _get_prediction_history_sync(limit: int = 10) -> list[dict]:
    """Return the N most recent predictions, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT team1, team2, predicted_at,
                   top_rec, top_confidence_pct, risk_level, stake_units, stake_label,
                   methodology_version, prompt_version,
                   actual_result, actual_score, completeness
            FROM predictions
            ORDER BY predicted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


async def get_prediction_history(limit: int = 10) -> list[dict]:
    """Return the N most recent predictions for the /history command."""
    return await asyncio.to_thread(_get_prediction_history_sync, limit)


def _get_prediction_history_for_match_sync(
    team1: str,
    team2: str,
    limit: int = 5,
) -> list[dict]:
    t1_l = team1.strip().lower()
    t2_l = team2.strip().lower()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT team1, team2, predicted_at,
                   top_rec, top_confidence_pct, risk_level, stake_units, stake_label,
                   methodology_version, prompt_version,
                   actual_result, actual_score, completeness
            FROM predictions
            WHERE (
                (LOWER(team1)=? AND LOWER(team2)=?) OR
                (LOWER(team1)=? AND LOWER(team2)=?)
            )
            ORDER BY predicted_at DESC
            LIMIT ?
            """,
            (t1_l, t2_l, t2_l, t1_l, limit),
        ).fetchall()
        return [dict(r) for r in rows]


async def get_prediction_history_for_match(
    team1: str,
    team2: str,
    limit: int = 5,
) -> list[dict]:
    """Return recent predictions for a specific match-up regardless of team order."""
    return await asyncio.to_thread(
        _get_prediction_history_for_match_sync,
        team1,
        team2,
        limit,
    )


def _get_backtest_prediction_rows_sync(limit: Optional[int] = None) -> list[dict]:
    feature_columns = [
        column.strip()
        for column in _FEATURE_SELECT_COLUMNS.split(",")
        if column.strip() not in {"prediction_id", "team1", "team2"}
    ]
    query = f"""
        SELECT
            p.id AS prediction_id,
            p.team1,
            p.team2,
            p.predicted_at,
            p.actual_result,
            p.actual_score,
            p.actual_goals_home,
            p.actual_goals_away,
            p.prob_w1,
            p.prob_draw,
            p.prob_w2,
            p.prob_over_2_5,
            p.prob_under_2_5,
            p.prob_btts_yes,
            p.prob_btts_no,
            p.methodology_version,
            p.prompt_version,
            {", ".join(feature_columns)}
        FROM predictions p
        LEFT JOIN prediction_features f ON f.prediction_id = p.id
        WHERE p.actual_result IS NOT NULL
        ORDER BY p.result_at DESC
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        query += "\nLIMIT ?"
        params = (limit,)

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_feature_row_to_dict(row) or {} for row in rows]


async def get_backtest_prediction_rows(limit: Optional[int] = None) -> list[dict]:
    """Return resolved predictions joined with frozen feature snapshots for offline backtests."""
    return await asyncio.to_thread(_get_backtest_prediction_rows_sync, limit)


def _get_prediction_clv_rows_sync(limit: Optional[int] = None) -> list[dict]:
    open_columns = ",\n            ".join(
        f"open_l.{column} AS open_{column}"
        for column in _PREDICTION_CLV_MARKET_COLUMNS
    )
    close_columns = ",\n            ".join(
        f"close_l.{column} AS close_{column}"
        for column in _PREDICTION_CLV_MARKET_COLUMNS
    )
    query = f"""
        SELECT
            p.id AS prediction_id,
            p.team1,
            p.team2,
            p.predicted_at,
            hm.competition_name,
            p.actual_result,
            p.actual_score,
            p.actual_goals_home,
            p.actual_goals_away,
            p.top_rec AS top_recommendation,
            p.top_recommendation_odds AS entry_odds,
            p.top_market_code,
            p.top_selection_code,
            p.policy_flags,
            p.value_candidate_snapshot,
            p.historical_match_id,
            open_l.captured_at AS open_captured_at,
            close_l.captured_at AS close_captured_at,
            {open_columns},
            {close_columns}
        FROM predictions p
        LEFT JOIN historical_matches hm
               ON hm.id = p.historical_match_id
        LEFT JOIN historical_market_lines open_l
               ON open_l.id = (
                   SELECT l_open.id
                   FROM historical_market_lines l_open
                   WHERE l_open.historical_match_id = p.historical_match_id
                     AND l_open.source_provider = 'sportsbot:live-predict'
                     AND l_open.snapshot_kind = 'OPEN'
                   ORDER BY l_open.created_at ASC
                   LIMIT 1
               )
        LEFT JOIN historical_market_lines close_l
               ON close_l.id = (
                   SELECT l_close.id
                   FROM historical_market_lines l_close
                   WHERE l_close.historical_match_id = p.historical_match_id
                     AND l_close.source_provider = 'sportsbot:live-predict'
                     AND l_close.snapshot_kind = 'CLOSE'
                   ORDER BY l_close.created_at DESC
                   LIMIT 1
               )
        WHERE p.actual_result IS NOT NULL
          AND p.historical_match_id IS NOT NULL
        ORDER BY p.result_at DESC
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        query += "\nLIMIT ?"
        params = (limit,)

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    result: list[dict] = []
    for row in rows:
        data = dict(row)
        data["policy_flags"] = _json_loads_list(data.get("policy_flags"))
        data["value_candidate_snapshot"] = _json_loads_object_list(data.get("value_candidate_snapshot"))
        result.append(data)
    return result


async def get_prediction_clv_rows(limit: Optional[int] = None) -> list[dict]:
    """Return resolved predictions joined with linked live OPEN/CLOSE market snapshots."""
    return await asyncio.to_thread(_get_prediction_clv_rows_sync, limit)


def _get_unresolved_prediction_count_sync() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM predictions WHERE actual_result IS NULL"
        ).fetchone()
        return row["c"] if row else 0


async def get_unresolved_prediction_count() -> int:
    return await asyncio.to_thread(_get_unresolved_prediction_count_sync)


def _get_active_lessons_count_sync() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM lessons WHERE active=1"
        ).fetchone()
        return row["c"] if row else 0


async def get_active_lessons_count() -> int:
    return await asyncio.to_thread(_get_active_lessons_count_sync)


def _get_unsynced_lessons_count_sync() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM lessons WHERE active=1 AND embedding_synced=0"
        ).fetchone()
        return row["c"] if row else 0


async def get_unsynced_lessons_count() -> int:
    return await asyncio.to_thread(_get_unsynced_lessons_count_sync)


# ── Lessons (self-learning) ───────────────────────────────────────────────────

def _save_lesson_sync(
    text: str,
    category: str,
    confidence: float,
    source_match: Optional[str] = None,
    source_prediction_id: Optional[int] = None,
) -> int:
    now = _now_utc()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO lessons
              (text, category, confidence, created_at, updated_at,
               source_match, source_prediction_id)
            VALUES (?,?,?,?,?,?,?)
            """,
            (text, category, confidence, now, now,
             source_match, source_prediction_id),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def save_lesson(
    text: str,
    category: str,
    confidence: float,
    source_match: Optional[str] = None,
    source_prediction_id: Optional[int] = None,
) -> int:
    """Save a new lesson and return its row id."""
    return await asyncio.to_thread(
        _save_lesson_sync, text, category, confidence,
        source_match, source_prediction_id,
    )


def _get_lessons_sync(
    category: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 20,
) -> list[dict]:
    with _connect() as conn:
        query = """
            SELECT id, text, category, confidence, times_confirmed,
                   created_at, updated_at, source_match, source_prediction_id,
                   active, embedding_synced
            FROM lessons
            WHERE active = 1 AND confidence >= ?
        """
        params: list = [min_confidence]
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY times_confirmed DESC, confidence DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


async def get_lessons(
    category: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 20,
) -> list[dict]:
    """Get active lessons, optionally filtered by category and min confidence."""
    return await asyncio.to_thread(
        _get_lessons_sync, category, min_confidence, limit,
    )


def _get_unsynced_lessons_sync() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, text, category, confidence, times_confirmed,
                   created_at, source_match, source_prediction_id
            FROM lessons
            WHERE active = 1 AND embedding_synced = 0
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


async def get_unsynced_lessons() -> list[dict]:
    """Get lessons not yet embedded in vector store."""
    return await asyncio.to_thread(_get_unsynced_lessons_sync)


def _mark_lesson_synced_sync(lesson_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE lessons SET embedding_synced = 1 WHERE id = ?",
            (lesson_id,),
        )
        conn.commit()


async def mark_lesson_synced(lesson_id: int) -> None:
    """Mark a lesson as embedded in the vector store."""
    await asyncio.to_thread(_mark_lesson_synced_sync, lesson_id)


def _increment_lesson_confirmed_sync(lesson_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE lessons
            SET times_confirmed = times_confirmed + 1, updated_at = ?
            WHERE id = ?
            """,
            (_now_utc(), lesson_id),
        )
        conn.commit()


async def increment_lesson_confirmed(lesson_id: int) -> None:
    """Increment the confirmation count when a pattern recurs."""
    await asyncio.to_thread(_increment_lesson_confirmed_sync, lesson_id)


def _deactivate_lesson_sync(lesson_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE lessons SET active = 0, updated_at = ? WHERE id = ?",
            (_now_utc(), lesson_id),
        )
        conn.commit()


async def deactivate_lesson(lesson_id: int) -> None:
    """Soft-delete a lesson (set active=0)."""
    await asyncio.to_thread(_deactivate_lesson_sync, lesson_id)


# ── Extended prediction retrieval (for learning module) ──────────────────────

def _get_resolved_prediction_sync(prediction_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                   """ + _PREDICTION_SELECT_COLUMNS + """
            FROM predictions
            WHERE id = ? AND actual_result IS NOT NULL
            """,
            (prediction_id,),
        ).fetchone()
        return _prediction_row_to_dict(row)


async def get_resolved_prediction(prediction_id: int) -> Optional[dict]:
    """Get a single resolved prediction by id (for the learning module)."""
    return await asyncio.to_thread(_get_resolved_prediction_sync, prediction_id)


def _link_prediction_to_historical_match_sync(
    prediction_id: int,
    historical_match_id: int,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE predictions
            SET historical_match_id=?
            WHERE id=?
            """,
            (historical_match_id, prediction_id),
        )
        conn.commit()


async def link_prediction_to_historical_match(
    prediction_id: int,
    historical_match_id: int,
) -> None:
    await asyncio.to_thread(
        _link_prediction_to_historical_match_sync,
        prediction_id,
        historical_match_id,
    )


def _update_historical_match_result_sync(
    historical_match_id: int,
    *,
    team1_score: Optional[int],
    team2_score: Optional[int],
    actual_result: Optional[str],
    status: str = "FINISHED",
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE historical_matches
            SET team1_score=?,
                team2_score=?,
                actual_result=?,
                status=?,
                updated_at=?
            WHERE id=?
            """,
            (
                team1_score,
                team2_score,
                actual_result,
                status,
                _now_utc(),
                historical_match_id,
            ),
        )
        conn.commit()


async def update_historical_match_result(
    historical_match_id: int,
    *,
    team1_score: Optional[int],
    team2_score: Optional[int],
    actual_result: Optional[str],
    status: str = "FINISHED",
) -> None:
    await asyncio.to_thread(
        _update_historical_match_result_sync,
        historical_match_id,
        team1_score=team1_score,
        team2_score=team2_score,
        actual_result=actual_result,
        status=status,
    )


def _get_recent_resolved_sync(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                   """ + _PREDICTION_SELECT_COLUMNS + """
            FROM predictions
            WHERE actual_result IS NOT NULL
            ORDER BY result_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_prediction_row_to_dict(r) for r in rows if r is not None]


async def get_recent_resolved(limit: int = 50) -> list[dict]:
    """Get the N most recently resolved predictions."""
    return await asyncio.to_thread(_get_recent_resolved_sync, limit)


def _upsert_historical_match_sync(payload: dict) -> int:
    data = {column: payload.get(column) for column in _HISTORICAL_MATCH_COLUMNS}
    now = _now_utc()
    data["created_at"] = data.get("created_at") or now
    data["updated_at"] = data.get("updated_at") or now
    data["is_neutral_venue"] = _bool_to_db(payload.get("is_neutral_venue"))
    data["went_extra_time"] = _bool_to_db(payload.get("went_extra_time")) or 0
    data["went_penalties"] = _bool_to_db(payload.get("went_penalties")) or 0

    placeholders = ",".join("?" for _ in _HISTORICAL_MATCH_COLUMNS)
    assignments = ", ".join(
        f"{column}=COALESCE(excluded.{column}, historical_matches.{column})"
        for column in _HISTORICAL_MATCH_COLUMNS
        if column not in {"id", "created_at"}
    )

    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO historical_matches ({", ".join(_HISTORICAL_MATCH_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(provider_name, provider_match_key) DO UPDATE SET {assignments}
            """,
            tuple(data.get(column) for column in _HISTORICAL_MATCH_COLUMNS),
        )
        row = conn.execute(
            """
            SELECT id
            FROM historical_matches
            WHERE provider_name=? AND provider_match_key=?
            """,
            (data["provider_name"], data["provider_match_key"]),
        ).fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("Failed to upsert historical match")
    return int(row["id"])


async def upsert_historical_match(payload: dict) -> int:
    """Insert or update a canonical historical match row and return its id."""
    return await asyncio.to_thread(_upsert_historical_match_sync, payload)


def _get_historical_match_by_provider_key_sync(
    provider_name: str,
    provider_match_key: str,
) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {_HISTORICAL_MATCH_SELECT_COLUMNS}
            FROM historical_matches
            WHERE provider_name=? AND provider_match_key=?
            """,
            (provider_name, provider_match_key),
        ).fetchone()
    return _historical_match_row_to_dict(row)


async def get_historical_match_by_provider_key(
    provider_name: str,
    provider_match_key: str,
) -> Optional[dict]:
    return await asyncio.to_thread(
        _get_historical_match_by_provider_key_sync,
        provider_name,
        provider_match_key,
    )


def _get_historical_match_sync(historical_match_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {_HISTORICAL_MATCH_SELECT_COLUMNS}
            FROM historical_matches
            WHERE id=?
            """,
            (historical_match_id,),
        ).fetchone()
    return _historical_match_row_to_dict(row)


async def get_historical_match(historical_match_id: int) -> Optional[dict]:
    return await asyncio.to_thread(_get_historical_match_sync, historical_match_id)


def _get_historical_matches_for_range_sync(
    date_from: str,
    date_to: str,
    *,
    competition_name: Optional[str] = None,
) -> list[dict]:
    query = f"""
        SELECT {_HISTORICAL_MATCH_SELECT_COLUMNS}
        FROM historical_matches
        WHERE match_date_utc >= ?
          AND match_date_utc <= ?
          AND actual_result IS NOT NULL
    """
    params: list[object] = [date_from, date_to]
    if competition_name:
        query += " AND LOWER(COALESCE(competition_name, '')) = ?"
        params.append(" ".join(str(competition_name).strip().lower().split()))
    query += "\nORDER BY match_date_utc DESC, id DESC"

    with _connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [
        converted
        for row in rows
        if (converted := _historical_match_row_to_dict(row)) is not None
    ]


async def get_historical_matches_for_range(
    date_from: str,
    date_to: str,
    *,
    competition_name: Optional[str] = None,
) -> list[dict]:
    return await asyncio.to_thread(
        _get_historical_matches_for_range_sync,
        date_from,
        date_to,
        competition_name=competition_name,
    )


def _upsert_historical_market_line_sync(payload: dict) -> int:
    data = {column: payload.get(column) for column in _HISTORICAL_MARKET_LINE_COLUMNS}
    data["created_at"] = data.get("created_at") or _now_utc()
    placeholders = ",".join("?" for _ in _HISTORICAL_MARKET_LINE_COLUMNS)
    assignments = ", ".join(
        f"{column}=COALESCE(excluded.{column}, historical_market_lines.{column})"
        for column in _HISTORICAL_MARKET_LINE_COLUMNS
        if column not in {"id", "created_at"}
    )

    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO historical_market_lines ({", ".join(_HISTORICAL_MARKET_LINE_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(historical_match_id, snapshot_kind, source_provider)
            DO UPDATE SET {assignments}
            """,
            tuple(data.get(column) for column in _HISTORICAL_MARKET_LINE_COLUMNS),
        )
        row = conn.execute(
            """
            SELECT id
            FROM historical_market_lines
            WHERE historical_match_id=? AND snapshot_kind=? AND source_provider=?
            """,
            (data["historical_match_id"], data["snapshot_kind"], data["source_provider"]),
        ).fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("Failed to upsert historical market line")
    return int(row["id"])


async def upsert_historical_market_line(payload: dict) -> int:
    """Insert or update one historical market-line snapshot and return its id."""
    return await asyncio.to_thread(_upsert_historical_market_line_sync, payload)


def _get_historical_market_lines_sync(historical_match_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_HISTORICAL_MARKET_LINE_SELECT_COLUMNS}
            FROM historical_market_lines
            WHERE historical_match_id=?
            ORDER BY
                CASE snapshot_kind
                    WHEN 'OPEN' THEN 1
                    WHEN 'MID' THEN 2
                    WHEN 'CLOSE' THEN 3
                    ELSE 99
                END,
                created_at ASC
            """,
            (historical_match_id,),
        ).fetchall()
    return [dict(row) for row in rows]


async def get_historical_market_lines(historical_match_id: int) -> list[dict]:
    return await asyncio.to_thread(_get_historical_market_lines_sync, historical_match_id)


def _upsert_historical_team_snapshot_sync(payload: dict) -> int:
    data = {column: payload.get(column) for column in _HISTORICAL_TEAM_SNAPSHOT_COLUMNS}
    data["created_at"] = data.get("created_at") or _now_utc()
    data["team1_short_rest"] = _bool_to_db(payload.get("team1_short_rest"))
    data["team2_short_rest"] = _bool_to_db(payload.get("team2_short_rest"))
    placeholders = ",".join("?" for _ in _HISTORICAL_TEAM_SNAPSHOT_COLUMNS)
    assignments = ", ".join(
        f"{column}=COALESCE(excluded.{column}, historical_team_snapshots.{column})"
        for column in _HISTORICAL_TEAM_SNAPSHOT_COLUMNS
        if column not in {"id", "created_at"}
    )

    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO historical_team_snapshots ({", ".join(_HISTORICAL_TEAM_SNAPSHOT_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(historical_match_id) DO UPDATE SET {assignments}
            """,
            tuple(data.get(column) for column in _HISTORICAL_TEAM_SNAPSHOT_COLUMNS),
        )
        row = conn.execute(
            """
            SELECT id
            FROM historical_team_snapshots
            WHERE historical_match_id=?
            """,
            (data["historical_match_id"],),
        ).fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("Failed to upsert historical team snapshot")
    return int(row["id"])


async def upsert_historical_team_snapshot(payload: dict) -> int:
    """Insert or update the pre-match team-state snapshot for one match."""
    return await asyncio.to_thread(_upsert_historical_team_snapshot_sync, payload)


def _get_historical_team_snapshot_sync(historical_match_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {_HISTORICAL_TEAM_SNAPSHOT_SELECT_COLUMNS}
            FROM historical_team_snapshots
            WHERE historical_match_id=?
            """,
            (historical_match_id,),
        ).fetchone()
    return _historical_team_snapshot_row_to_dict(row)


async def get_historical_team_snapshot(historical_match_id: int) -> Optional[dict]:
    return await asyncio.to_thread(_get_historical_team_snapshot_sync, historical_match_id)


def _upsert_historical_provider_coverage_sync(payload: dict) -> int:
    data = {
        column: payload.get(column)
        for column in _HISTORICAL_PROVIDER_COVERAGE_COLUMNS
    }
    data["created_at"] = data.get("created_at") or _now_utc()
    placeholders = ",".join("?" for _ in _HISTORICAL_PROVIDER_COVERAGE_COLUMNS)
    assignments = ", ".join(
        f"{column}=COALESCE(excluded.{column}, historical_provider_coverage.{column})"
        for column in _HISTORICAL_PROVIDER_COVERAGE_COLUMNS
        if column not in {"id", "created_at"}
    )

    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO historical_provider_coverage ({", ".join(_HISTORICAL_PROVIDER_COVERAGE_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(historical_match_id, source_key) DO UPDATE SET {assignments}
            """,
            tuple(data.get(column) for column in _HISTORICAL_PROVIDER_COVERAGE_COLUMNS),
        )
        row = conn.execute(
            """
            SELECT id
            FROM historical_provider_coverage
            WHERE historical_match_id=? AND source_key=?
            """,
            (data["historical_match_id"], data["source_key"]),
        ).fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("Failed to upsert historical provider coverage")
    return int(row["id"])


async def upsert_historical_provider_coverage(payload: dict) -> int:
    """Insert or update provider coverage metadata for one historical match."""
    return await asyncio.to_thread(_upsert_historical_provider_coverage_sync, payload)


def _get_historical_provider_coverage_sync(historical_match_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_HISTORICAL_PROVIDER_COVERAGE_SELECT_COLUMNS}
            FROM historical_provider_coverage
            WHERE historical_match_id=?
            ORDER BY source_key ASC
            """,
            (historical_match_id,),
        ).fetchall()
    return [dict(row) for row in rows]


async def get_historical_provider_coverage(historical_match_id: int) -> list[dict]:
    return await asyncio.to_thread(
        _get_historical_provider_coverage_sync,
        historical_match_id,
    )


def _start_historical_import_run_sync(
    run_type: str,
    provider_name: str,
    *,
    status: str = "RUNNING",
    season_label: Optional[str] = None,
    competition_name: Optional[str] = None,
    requested_from: Optional[str] = None,
    requested_to: Optional[str] = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO historical_import_runs (
                run_type, provider_name, started_at, status,
                season_label, competition_name, requested_from, requested_to
            )
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                run_type,
                provider_name,
                _now_utc(),
                status,
                season_label,
                competition_name,
                requested_from,
                requested_to,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


async def start_historical_import_run(
    run_type: str,
    provider_name: str,
    *,
    status: str = "RUNNING",
    season_label: Optional[str] = None,
    competition_name: Optional[str] = None,
    requested_from: Optional[str] = None,
    requested_to: Optional[str] = None,
) -> int:
    """Create an importer-run row and return its id."""
    return await asyncio.to_thread(
        _start_historical_import_run_sync,
        run_type,
        provider_name,
        status=status,
        season_label=season_label,
        competition_name=competition_name,
        requested_from=requested_from,
        requested_to=requested_to,
    )


def _finish_historical_import_run_sync(
    run_id: int,
    *,
    status: str,
    records_inserted: int = 0,
    records_updated: int = 0,
    records_failed: int = 0,
    error_summary: Optional[str] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE historical_import_runs
            SET finished_at=?, status=?, records_inserted=?, records_updated=?,
                records_failed=?, error_summary=?
            WHERE id=?
            """,
            (
                _now_utc(),
                status,
                records_inserted,
                records_updated,
                records_failed,
                error_summary,
                run_id,
            ),
        )
        conn.commit()


async def finish_historical_import_run(
    run_id: int,
    *,
    status: str,
    records_inserted: int = 0,
    records_updated: int = 0,
    records_failed: int = 0,
    error_summary: Optional[str] = None,
) -> None:
    """Mark an importer run as finished with counters and optional error summary."""
    await asyncio.to_thread(
        _finish_historical_import_run_sync,
        run_id,
        status=status,
        records_inserted=records_inserted,
        records_updated=records_updated,
        records_failed=records_failed,
        error_summary=error_summary,
    )


def _get_recent_historical_import_runs_sync(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_HISTORICAL_IMPORT_RUN_SELECT_COLUMNS}
            FROM historical_import_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


async def get_recent_historical_import_runs(limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(_get_recent_historical_import_runs_sync, limit)


def _historical_backtest_row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    if data.get("is_neutral_venue") is not None:
        data["is_neutral_venue"] = bool(data["is_neutral_venue"])
    if data.get("is_team1_home") is not None:
        data["is_team1_home"] = bool(data["is_team1_home"])

    over_prob, under_prob = _normalized_probs_from_decimal_odds(
        data.get("odds_over_2_5"),
        data.get("odds_under_2_5"),
    )
    data["market_prob_over_2_5"] = over_prob
    data["market_prob_under_2_5"] = under_prob
    btts_yes_prob, btts_no_prob = _normalized_probs_from_decimal_odds(
        data.get("odds_btts_yes"),
        data.get("odds_btts_no"),
    )
    data["market_prob_btts_yes"] = btts_yes_prob
    data["market_prob_btts_no"] = btts_no_prob

    if data.get("team1_form_points_last5") is not None and data.get("team2_form_points_last5") is not None:
        data["form_points_last5_diff"] = round(
            float(data["team1_form_points_last5"]) - float(data["team2_form_points_last5"]),
            4,
        )
    if data.get("team1_points") is not None and data.get("team2_points") is not None:
        data["standings_point_gap"] = round(
            float(data["team1_points"]) - float(data["team2_points"]),
            4,
        )
    if data.get("team1_shots_avg") is not None and data.get("team2_shots_avg") is not None:
        data["shots_avg_diff"] = round(
            float(data["team1_shots_avg"]) - float(data["team2_shots_avg"]),
            4,
        )
    if data.get("team1_shots_on_target_avg") is not None and data.get("team2_shots_on_target_avg") is not None:
        data["shots_on_target_avg_diff"] = round(
            float(data["team1_shots_on_target_avg"]) - float(data["team2_shots_on_target_avg"]),
            4,
        )
    if data.get("team1_corners_avg") is not None and data.get("team2_corners_avg") is not None:
        data["corners_avg_diff"] = round(
            float(data["team1_corners_avg"]) - float(data["team2_corners_avg"]),
            4,
        )
    if (
        data.get("team1_shots_avg") is not None
        and data.get("team1_shots_allowed_avg") is not None
        and data.get("team2_shots_avg") is not None
        and data.get("team2_shots_allowed_avg") is not None
    ):
        team1_balance = float(data["team1_shots_avg"]) - float(data["team1_shots_allowed_avg"])
        team2_balance = float(data["team2_shots_avg"]) - float(data["team2_shots_allowed_avg"])
        data["shots_balance_diff"] = round(team1_balance - team2_balance, 4)
    if (
        data.get("team1_shots_on_target_avg") is not None
        and data.get("team1_shots_on_target_allowed_avg") is not None
        and data.get("team2_shots_on_target_avg") is not None
        and data.get("team2_shots_on_target_allowed_avg") is not None
    ):
        team1_balance = float(data["team1_shots_on_target_avg"]) - float(data["team1_shots_on_target_allowed_avg"])
        team2_balance = float(data["team2_shots_on_target_avg"]) - float(data["team2_shots_on_target_allowed_avg"])
        data["shots_on_target_balance_diff"] = round(team1_balance - team2_balance, 4)
    if (
        data.get("team1_corners_avg") is not None
        and data.get("team1_corners_allowed_avg") is not None
        and data.get("team2_corners_avg") is not None
        and data.get("team2_corners_allowed_avg") is not None
    ):
        team1_balance = float(data["team1_corners_avg"]) - float(data["team1_corners_allowed_avg"])
        team2_balance = float(data["team2_corners_avg"]) - float(data["team2_corners_allowed_avg"])
        data["corners_balance_diff"] = round(team1_balance - team2_balance, 4)
    return data


def _get_historical_backtest_rows_sync(limit: Optional[int] = None) -> list[dict]:
    snapshot_priority = """
        CASE l2.snapshot_kind
            WHEN 'CLOSE' THEN 1
            WHEN 'MID' THEN 2
            WHEN 'OPEN' THEN 3
            ELSE 99
        END
    """.strip()
    source_richness = """
        (
            CASE WHEN l2.odds_w1 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_draw IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_w2 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_over_2_5 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_under_2_5 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_btts_yes IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_btts_no IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_dnb_team1 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_dnb_team2 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_dc_1x IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_dc_12 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.odds_dc_x2 IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.ah_team1_odds IS NOT NULL THEN 1 ELSE 0 END +
            CASE WHEN l2.ah_team2_odds IS NOT NULL THEN 1 ELSE 0 END
        )
    """.strip()

    def _market_field(field: str, alias: Optional[str] = None) -> str:
        output_name = alias or field
        return f"""
            (
                SELECT l2.{field}
                FROM historical_market_lines l2
                WHERE l2.historical_match_id = m.id
                  AND l2.{field} IS NOT NULL
                ORDER BY {snapshot_priority},
                         l2.created_at DESC,
                         l2.id DESC
                LIMIT 1
            ) AS {output_name}
        """.strip()

    query = f"""
        SELECT
            m.id AS historical_match_id,
            m.provider_name,
            m.provider_match_key,
            m.competition_name,
            m.match_date_utc,
            COALESCE(m.kickoff_utc, m.match_date_utc || 'T00:00:00+00:00') AS predicted_at,
            m.team1_name AS team1,
            m.team2_name AS team2,
            m.actual_result,
            m.team1_score AS actual_goals_home,
            m.team2_score AS actual_goals_away,
            m.is_neutral_venue,
            CASE
                WHEN m.is_neutral_venue = 1 THEN NULL
                ELSE 1
            END AS is_team1_home,
            s.data_completeness,
            s.successful_source_count,
            s.total_source_count,
            s.team1_form_points_last5,
            s.team2_form_points_last5,
            s.team1_goal_diff_last5,
            s.team2_goal_diff_last5,
            s.team1_scored_avg,
            s.team2_scored_avg,
            s.team1_conceded_avg,
            s.team2_conceded_avg,
            s.team1_shots_avg,
            s.team2_shots_avg,
            s.team1_shots_allowed_avg,
            s.team2_shots_allowed_avg,
            s.team1_shots_on_target_avg,
            s.team2_shots_on_target_avg,
            s.team1_shots_on_target_allowed_avg,
            s.team2_shots_on_target_allowed_avg,
            s.team1_corners_avg,
            s.team2_corners_avg,
            s.team1_corners_allowed_avg,
            s.team2_corners_allowed_avg,
            s.team1_points,
            s.team2_points,
            (
                SELECT l2.source_provider
                FROM historical_market_lines l2
                WHERE l2.historical_match_id = m.id
                ORDER BY {snapshot_priority},
                         {source_richness} DESC,
                         l2.created_at DESC,
                         l2.id DESC
                LIMIT 1
            ) AS source_provider,
            {_market_field("odds_w1")},
            {_market_field("odds_draw")},
            {_market_field("odds_w2")},
            {_market_field("implied_prob_w1", "market_prob_w1")},
            {_market_field("implied_prob_draw", "market_prob_draw")},
            {_market_field("implied_prob_w2", "market_prob_w2")},
            {_market_field("odds_over_2_5")},
            {_market_field("odds_under_2_5")},
            {_market_field("odds_btts_yes")},
            {_market_field("odds_btts_no")},
            {_market_field("odds_dnb_team1")},
            {_market_field("odds_dnb_team2")},
            {_market_field("odds_dc_1x")},
            {_market_field("odds_dc_12")},
            {_market_field("odds_dc_x2")},
            {_market_field("ah_main_line")},
            {_market_field("ah_team1_odds")},
            {_market_field("ah_team2_odds")}
        FROM historical_matches m
        LEFT JOIN historical_team_snapshots s
               ON s.historical_match_id = m.id
        WHERE m.actual_result IS NOT NULL
        ORDER BY COALESCE(m.kickoff_utc, m.match_date_utc) DESC
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        query += "\nLIMIT ?"
        params = (limit,)

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_historical_backtest_row_to_dict(row) for row in rows]


async def get_historical_backtest_rows(limit: Optional[int] = None) -> list[dict]:
    """Return canonical historical rows joined with close-line markets and basic team snapshots."""
    return await asyncio.to_thread(_get_historical_backtest_rows_sync, limit)


def _normalize_competition_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = " ".join(str(value).strip().lower().split())
    return normalized or None


def _probability_bucket(probability_pct: float) -> tuple[float, float, str]:
    bounded = max(0.0, min(float(probability_pct), 99.9))
    low = math.floor(bounded / 5.0) * 5.0
    high = min(100.0, low + 5.0)
    return low, high, f"{int(low)}-{int(high)}"


def _btts_goal_environment_market(prob_over_2_5: Optional[float]) -> Optional[str]:
    if prob_over_2_5 is None:
        return None
    probability = float(prob_over_2_5)
    if probability < 47.5:
        return "BTTS_YES_LOW_TOTALS"
    if probability > 57.5:
        return "BTTS_YES_HIGH_TOTALS"
    return "BTTS_YES_MID_TOTALS"


def _recompute_numeric_recalibration_sync(
    min_sample_size: int = 4,
    shrinkage_denominator: float = 10.0,
) -> dict:
    from baseline_model import build_numeric_baseline, derive_expanded_market_probabilities

    rows = _get_historical_backtest_rows_sync()
    now = _now_utc()
    aggregates: dict[tuple[str, Optional[str], str, str], dict[str, float]] = {}

    def _add_observation(
        *,
        market: str,
        competition_key: Optional[str],
        probability_pct: Optional[float],
        outcome: Optional[float],
    ) -> None:
        if probability_pct is None or outcome is None:
            return
        bucket_low, bucket_high, bucket_label = _probability_bucket(float(probability_pct))
        for scope, scoped_competition in (
            ("GLOBAL", None),
            ("COMPETITION", competition_key),
        ):
            if scope == "COMPETITION" and not scoped_competition:
                continue
            key = (scope, scoped_competition, market, bucket_label)
            bucket = aggregates.setdefault(
                key,
                {
                    "bucket_low": bucket_low,
                    "bucket_high": bucket_high,
                    "pred_sum": 0.0,
                    "hit_sum": 0.0,
                    "sample_size": 0.0,
                },
            )
            bucket["pred_sum"] += float(probability_pct)
            bucket["hit_sum"] += float(outcome)
            bucket["sample_size"] += 1.0

    def _binary_brier(probability_pct: Optional[float], outcome: Optional[float]) -> Optional[float]:
        if probability_pct is None or outcome is None:
            return None
        return ((float(probability_pct) / 100.0) - float(outcome)) ** 2

    def _brier_1x2_row(
        p1: Optional[float],
        pd: Optional[float],
        p2: Optional[float],
        actual_result: Optional[str],
    ) -> Optional[float]:
        outcome_map = {
            "W1": (1.0, 0.0, 0.0),
            "DRAW": (0.0, 1.0, 0.0),
            "W2": (0.0, 0.0, 1.0),
        }
        if None in (p1, pd, p2) or actual_result not in outcome_map:
            return None
        o1, od, o2 = outcome_map[str(actual_result)]
        return (
            ((float(p1) / 100.0) - o1) ** 2
            + ((float(pd) / 100.0) - od) ** 2
            + ((float(p2) / 100.0) - o2) ** 2
        ) / 3.0

    for row in rows:
        try:
            snapshot = PredictionFeatureSnapshot.model_validate(row)
        except Exception:
            continue

        baseline = build_numeric_baseline(
            MatchData(team1=row["team1"], team2=row["team2"]),
            snapshot,
        )
        competition_key = _normalize_competition_key(row.get("competition_name"))

        top_key = max(
            ("prob_w1", "prob_draw", "prob_w2"),
            key=lambda key: float(baseline.get(key) or 0.0),
        )
        top_actual = {
            "prob_w1": 1.0 if row.get("actual_result") == "W1" else 0.0,
            "prob_draw": 1.0 if row.get("actual_result") == "DRAW" else 0.0,
            "prob_w2": 1.0 if row.get("actual_result") == "W2" else 0.0,
        }[top_key]
        _add_observation(
            market="1X2_TOP_PICK",
            competition_key=competition_key,
            probability_pct=baseline.get(top_key),
            outcome=top_actual,
        )

        if row.get("actual_goals_home") is not None and row.get("actual_goals_away") is not None:
            total_goals = int(row["actual_goals_home"]) + int(row["actual_goals_away"])
            btts_outcome = 1.0 if (int(row["actual_goals_home"]) > 0 and int(row["actual_goals_away"]) > 0) else 0.0
            _add_observation(
                market="OVER_2_5",
                competition_key=competition_key,
                probability_pct=baseline.get("prob_over_2_5"),
                outcome=1.0 if total_goals > 2.5 else 0.0,
            )
            _add_observation(
                market="BTTS_YES",
                competition_key=competition_key,
                probability_pct=baseline.get("prob_btts_yes"),
                outcome=btts_outcome,
            )
            btts_environment_market = _btts_goal_environment_market(
                row.get("market_prob_over_2_5")
                if row.get("market_prob_over_2_5") is not None else baseline.get("prob_over_2_5")
            )
            if btts_environment_market:
                _add_observation(
                    market=btts_environment_market,
                    competition_key=competition_key,
                    probability_pct=baseline.get("prob_btts_yes"),
                    outcome=btts_outcome,
                )

    profiles: list[dict] = []
    for (scope, competition_key, market, bucket_label), bucket in aggregates.items():
        sample_size = int(bucket["sample_size"])
        if sample_size < min_sample_size:
            continue
        avg_pred_pct = bucket["pred_sum"] / sample_size
        actual_hit_pct = (bucket["hit_sum"] * 100.0) / sample_size
        shrink = sample_size / (sample_size + shrinkage_denominator) if shrinkage_denominator > 0 else 1.0
        delta_pct = (actual_hit_pct - avg_pred_pct) * shrink
        profiles.append(
            {
                "computed_at": now,
                "scope": scope,
                "competition_key": competition_key,
                "market": market,
                "bucket_label": bucket_label,
                "bucket_low": bucket["bucket_low"],
                "bucket_high": bucket["bucket_high"],
                "sample_size": sample_size,
                "avg_pred_pct": round(avg_pred_pct, 4),
                "actual_hit_pct": round(actual_hit_pct, 4),
                "delta_pct": round(delta_pct, 4),
            }
        )

    profile_cache: dict[Optional[str], list[dict]] = {}

    def _profiles_for_competition(competition_key: Optional[str]) -> list[dict]:
        cache_key = competition_key or "__GLOBAL__"
        cached = profile_cache.get(cache_key)
        if cached is not None:
            return cached
        scoped = [
            profile
            for profile in profiles
            if profile.get("scope") == "GLOBAL"
            or (
                competition_key
                and profile.get("scope") == "COMPETITION"
                and profile.get("competition_key") == competition_key
            )
        ]
        profile_cache[cache_key] = scoped
        return scoped

    family_aggregates: dict[tuple[str, Optional[str], str], dict[str, float]] = {}

    def _add_family_observation(
        *,
        family: str,
        competition_key: Optional[str],
        model_brier: Optional[float],
        market_brier: Optional[float],
    ) -> None:
        if model_brier is None or market_brier is None:
            return
        for scope, scoped_competition in (("GLOBAL", None), ("COMPETITION", competition_key)):
            if scope == "COMPETITION" and not scoped_competition:
                continue
            key = (scope, scoped_competition, family)
            bucket = family_aggregates.setdefault(
                key,
                {
                    "sample_size": 0.0,
                    "model_brier_sum": 0.0,
                    "market_brier_sum": 0.0,
                },
            )
            bucket["sample_size"] += 1.0
            bucket["model_brier_sum"] += float(model_brier)
            bucket["market_brier_sum"] += float(market_brier)

    for row in rows:
        try:
            snapshot = PredictionFeatureSnapshot.model_validate(row)
        except Exception:
            continue

        competition_key = _normalize_competition_key(row.get("competition_name"))
        recalibrated = build_numeric_baseline(
            MatchData(team1=row["team1"], team2=row["team2"]),
            snapshot,
            recalibration_profiles=_profiles_for_competition(competition_key),
        )
        derived = derive_expanded_market_probabilities(recalibrated)

        _add_family_observation(
            family="1x2",
            competition_key=competition_key,
            model_brier=_brier_1x2_row(
                recalibrated.get("prob_w1"),
                recalibrated.get("prob_draw"),
                recalibrated.get("prob_w2"),
                row.get("actual_result"),
            ),
            market_brier=_brier_1x2_row(
                row.get("market_prob_w1"),
                row.get("market_prob_draw"),
                row.get("market_prob_w2"),
                row.get("actual_result"),
            ),
        )

        if row.get("actual_goals_home") is not None and row.get("actual_goals_away") is not None:
            total_goals = int(row["actual_goals_home"]) + int(row["actual_goals_away"])
            over_outcome = 1.0 if total_goals > 2.5 else 0.0
            btts_outcome = (
                1.0
                if (int(row["actual_goals_home"]) > 0 and int(row["actual_goals_away"]) > 0)
                else 0.0
            )
            _add_family_observation(
                family="totals",
                competition_key=competition_key,
                model_brier=_binary_brier(recalibrated.get("prob_over_2_5"), over_outcome),
                market_brier=_binary_brier(row.get("market_prob_over_2_5"), over_outcome),
            )
            _add_family_observation(
                family="btts",
                competition_key=competition_key,
                model_brier=_binary_brier(
                    recalibrated.get("prob_btts_yes", derived.get("prob_btts_yes")),
                    btts_outcome,
                ),
                market_brier=_binary_brier(row.get("market_prob_btts_yes"), btts_outcome),
            )

    family_profiles: list[dict] = []
    for (scope, competition_key, family), bucket in family_aggregates.items():
        sample_size = int(bucket["sample_size"])
        if sample_size < min_sample_size:
            continue
        model_brier = float(bucket["model_brier_sum"]) / sample_size
        market_brier = float(bucket["market_brier_sum"]) / sample_size
        brier_delta = market_brier - model_brier
        shrink = sample_size / (sample_size + 160.0)
        trust_score = brier_delta * shrink * 1000.0
        family_profiles.append(
            {
                "computed_at": now,
                "scope": scope,
                "competition_key": competition_key,
                "family": family,
                "sample_size": sample_size,
                "model_brier": round(model_brier, 6),
                "market_brier": round(market_brier, 6),
                "brier_delta": round(brier_delta, 6),
                "trust_score": round(trust_score, 4),
            }
        )

    with _connect() as conn:
        conn.execute("DELETE FROM numeric_recalibration_profiles")
        if profiles:
            columns = [column.strip() for column in _NUMERIC_RECALIBRATION_SELECT_COLUMNS.split(",") if column.strip() != "id"]
            placeholders = ",".join("?" for _ in columns)
            assignments = ", ".join(
                f"{column}=excluded.{column}"
                for column in columns
                if column not in {"scope", "competition_key", "market", "bucket_label"}
            )
            conn.executemany(
                f"""
                INSERT INTO numeric_recalibration_profiles ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(scope, competition_key, market, bucket_label)
                DO UPDATE SET {assignments}
                """,
                [tuple(profile.get(column) for column in columns) for profile in profiles],
            )
        conn.execute("DELETE FROM market_family_quality_profiles")
        if family_profiles:
            columns = [
                column.strip()
                for column in _MARKET_FAMILY_QUALITY_SELECT_COLUMNS.split(",")
                if column.strip() != "id"
            ]
            placeholders = ",".join("?" for _ in columns)
            assignments = ", ".join(
                f"{column}=excluded.{column}"
                for column in columns
                if column not in {"scope", "competition_key", "family"}
            )
            conn.executemany(
                f"""
                INSERT INTO market_family_quality_profiles ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(scope, competition_key, family)
                DO UPDATE SET {assignments}
                """,
                [tuple(profile.get(column) for column in columns) for profile in family_profiles],
            )
        conn.commit()

    return {
        "computed_at": now,
        "rows_evaluated": len(rows),
        "profiles_written": len(profiles),
        "family_profiles_written": len(family_profiles),
        "min_sample_size": min_sample_size,
    }


async def recompute_numeric_recalibration(
    min_sample_size: int = 4,
    shrinkage_denominator: float = 10.0,
) -> dict:
    return await asyncio.to_thread(
        _recompute_numeric_recalibration_sync,
        min_sample_size,
        shrinkage_denominator,
    )


def _get_numeric_recalibration_profiles_sync(
    competition_name: Optional[str] = None,
) -> list[dict]:
    competition_key = _normalize_competition_key(competition_name)
    query = f"""
        SELECT {_NUMERIC_RECALIBRATION_SELECT_COLUMNS}
        FROM numeric_recalibration_profiles
        WHERE scope='GLOBAL'
    """
    params: tuple[object, ...] = ()
    if competition_key:
        query += " OR (scope='COMPETITION' AND competition_key=?)"
        params = (competition_key,)
    query += """
        ORDER BY
            CASE scope WHEN 'COMPETITION' THEN 0 ELSE 1 END,
            market ASC,
            bucket_low ASC
    """
    with _connect() as conn:
        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
    return [dict(row) for row in rows]


async def get_numeric_recalibration_profiles(
    competition_name: Optional[str] = None,
) -> list[dict]:
    return await asyncio.to_thread(
        _get_numeric_recalibration_profiles_sync,
        competition_name,
    )


def _get_market_family_quality_profiles_sync(
    competition_name: Optional[str] = None,
) -> list[dict]:
    competition_key = _normalize_competition_key(competition_name)
    query = f"""
        SELECT {_MARKET_FAMILY_QUALITY_SELECT_COLUMNS}
        FROM market_family_quality_profiles
        WHERE scope='GLOBAL'
    """
    params: tuple[object, ...] = ()
    if competition_key:
        query += " OR (scope='COMPETITION' AND competition_key=?)"
        params = (competition_key,)
    query += """
        ORDER BY
            CASE scope WHEN 'COMPETITION' THEN 0 ELSE 1 END,
            family ASC,
            sample_size DESC
    """
    with _connect() as conn:
        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
    return [dict(row) for row in rows]


async def get_market_family_quality_profiles(
    competition_name: Optional[str] = None,
) -> list[dict]:
    return await asyncio.to_thread(
        _get_market_family_quality_profiles_sync,
        competition_name,
    )


# ── CLV Tracking ─────────────────────────────────────────────────────────────

def _save_clv_tracking_sync(
    prediction_id: int,
    team1: str,
    team2: str,
    top_market: Optional[str],
    top_selection: Optional[str],
    placement_odds: Optional[float],
    snapshot,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO clv_tracking (
                prediction_id, team1, team2, created_at,
                top_market, top_selection, placement_odds,
                pin_w1, pin_draw, pin_w2,
                pin_over_2_5, pin_under_2_5
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prediction_id, team1, team2, _now_utc(),
                top_market, top_selection, placement_odds,
                snapshot.pin_w1, snapshot.pin_draw, snapshot.pin_w2,
                snapshot.pin_over_2_5, snapshot.pin_under_2_5,
            ),
        )
        conn.commit()


async def save_clv_tracking(
    prediction_id: int,
    team1: str,
    team2: str,
    top_market: Optional[str],
    top_selection: Optional[str],
    placement_odds: Optional[float],
    snapshot,
) -> None:
    try:
        await asyncio.to_thread(
            _save_clv_tracking_sync,
            prediction_id, team1, team2,
            top_market, top_selection, placement_odds,
            snapshot,
        )
    except Exception as exc:
        logger.warning("CLV tracking save failed: %s", exc)


def _update_clv_closing_odds_sync(
    prediction_id: int,
    close_w1: Optional[float],
    close_draw: Optional[float],
    close_w2: Optional[float],
    close_over_2_5: Optional[float],
    close_under_2_5: Optional[float],
) -> None:
    from scrapers.pinnacle import compute_clv

    with _connect() as conn:
        row = conn.execute(
            "SELECT pin_w1, pin_draw, pin_w2, pin_over_2_5, pin_under_2_5, "
            "top_market, top_selection, placement_odds "
            "FROM clv_tracking WHERE prediction_id = ?",
            (prediction_id,),
        ).fetchone()
        if not row:
            return

        pin_w1 = row["pin_w1"]
        pin_over = row["pin_over_2_5"]
        placement = row["placement_odds"]

        # CLV for 1X2 (use the selection that was recommended)
        clv_1x2 = None
        if pin_w1 and close_w1:
            clv_1x2 = compute_clv(pin_w1, close_w1)

        # CLV for O/U
        clv_ou = None
        if pin_over and close_over_2_5:
            clv_ou = compute_clv(pin_over, close_over_2_5)

        # CLV for top pick
        clv_top = None
        if placement and placement > 1.0:
            market = row["top_market"]
            if market and "OVER" in str(market).upper() and close_over_2_5:
                clv_top = compute_clv(placement, close_over_2_5)
            elif market and "UNDER" in str(market).upper() and close_under_2_5:
                clv_top = compute_clv(placement, close_under_2_5)
            elif close_w1:
                # Default to W1 CLV
                clv_top = compute_clv(placement, close_w1)

        conn.execute(
            """
            UPDATE clv_tracking SET
                close_w1 = ?, close_draw = ?, close_w2 = ?,
                close_over_2_5 = ?, close_under_2_5 = ?,
                clv_1x2_pct = ?, clv_ou_pct = ?, clv_top_pick_pct = ?,
                resolved = 1
            WHERE prediction_id = ?
            """,
            (
                close_w1, close_draw, close_w2,
                close_over_2_5, close_under_2_5,
                clv_1x2, clv_ou, clv_top,
                prediction_id,
            ),
        )
        conn.commit()


async def update_clv_closing_odds(
    prediction_id: int,
    close_w1: Optional[float] = None,
    close_draw: Optional[float] = None,
    close_w2: Optional[float] = None,
    close_over_2_5: Optional[float] = None,
    close_under_2_5: Optional[float] = None,
) -> None:
    try:
        await asyncio.to_thread(
            _update_clv_closing_odds_sync,
            prediction_id, close_w1, close_draw, close_w2,
            close_over_2_5, close_under_2_5,
        )
    except Exception as exc:
        logger.warning("CLV closing odds update failed: %s", exc)


def _get_clv_stats_sync() -> dict:
    """Get aggregate CLV statistics."""
    with _connect() as conn:
        try:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
                    AVG(CASE WHEN resolved = 1 THEN clv_1x2_pct END) as avg_clv_1x2,
                    AVG(CASE WHEN resolved = 1 THEN clv_ou_pct END) as avg_clv_ou,
                    AVG(CASE WHEN resolved = 1 THEN clv_top_pick_pct END) as avg_clv_top,
                    SUM(CASE WHEN resolved = 1 AND clv_top_pick_pct > 0 THEN 1 ELSE 0 END) as positive_clv_count,
                    SUM(CASE WHEN resolved = 1 AND clv_top_pick_pct <= 0 THEN 1 ELSE 0 END) as negative_clv_count
                FROM clv_tracking
            """).fetchone()
            if not row:
                return {}
            return dict(row)
        except sqlite3.OperationalError:
            return {}


async def get_clv_stats() -> dict:
    return await asyncio.to_thread(_get_clv_stats_sync)


def _get_recent_clv_entries_sync(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        try:
            rows = conn.execute(
                """
                SELECT team1, team2, created_at, top_market, placement_odds,
                       pin_w1, pin_over_2_5, close_w1, close_over_2_5,
                       clv_1x2_pct, clv_ou_pct, clv_top_pick_pct, resolved
                FROM clv_tracking
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []


async def get_recent_clv_entries(limit: int = 10) -> list[dict]:
    return await asyncio.to_thread(_get_recent_clv_entries_sync, limit)


# ── Daily P&L summary ─────────────────────────────────────────────────────────

def _get_daily_pnl_summary_sync(day_start_iso: str, day_end_iso: str) -> Optional[dict]:
    """Compute P&L summary for a specific day (UTC boundaries)."""
    with _connect() as conn:
        # Predictions placed today
        placed = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM predictions
            WHERE predicted_at >= ? AND predicted_at < ?
              AND top_rec IS NOT NULL
              AND LOWER(top_rec) NOT LIKE 'no bet%'
              AND stake_units IS NOT NULL AND stake_units > 0
            """,
            (day_start_iso, day_end_iso),
        ).fetchone()

        # Resolved today (result_at within the day)
        resolved_rows = conn.execute(
            """
            SELECT
                top_market_code, top_selection_code,
                top_recommendation_odds, stake_units,
                actual_result, actual_score,
                actual_goals_home, actual_goals_away,
                team1, team2
            FROM predictions
            WHERE result_at >= ? AND result_at < ?
              AND actual_result IS NOT NULL
              AND top_market_code IS NOT NULL
              AND top_market_code != 'NO_BET'
              AND stake_units IS NOT NULL AND stake_units > 0
            ORDER BY result_at
            """,
            (day_start_iso, day_end_iso),
        ).fetchall()

    placed_count = int(placed["cnt"]) if placed else 0
    if not resolved_rows and placed_count == 0:
        return None

    wins = losses = pushes = 0
    profit_units = 0.0
    staked_units = 0.0
    details: list[dict] = []

    for raw_row in resolved_rows:
        row = dict(raw_row)
        try:
            odds = float(row["top_recommendation_odds"]) if row["top_recommendation_odds"] is not None else None
            stake = float(row["stake_units"]) if row["stake_units"] is not None else None
        except (TypeError, ValueError):
            continue
        outcome = _grade_prediction_market_selection(
            row,
            str(row.get("top_market_code") or ""),
            str(row.get("top_selection_code") or ""),
        )
        profit = _profit_units_from_outcome(outcome, odds, stake)
        if outcome is None or profit is None or stake is None:
            continue

        staked_units += stake
        profit_units += profit
        if outcome == "WIN":
            wins += 1
        elif outcome == "LOSS":
            losses += 1
        elif outcome == "PUSH":
            pushes += 1

        details.append({
            "team1": row.get("team1", ""),
            "team2": row.get("team2", ""),
            "outcome": outcome,
            "profit": round(profit, 2),
            "score": row.get("actual_score", ""),
        })

    settled = wins + losses + pushes
    return {
        "day": day_start_iso[:10],
        "placed_count": placed_count,
        "settled_count": settled,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "staked_units": round(staked_units, 2),
        "profit_units": round(profit_units, 2),
        "roi_pct": round((profit_units * 100.0) / staked_units, 2) if staked_units > 0 else 0.0,
        "details": details,
    }


async def get_daily_pnl_summary(day_start_iso: str, day_end_iso: str) -> Optional[dict]:
    return await asyncio.to_thread(_get_daily_pnl_summary_sync, day_start_iso, day_end_iso)


def _get_active_user_ids_sync() -> list[int]:
    """Return user_ids that have made at least one prediction."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM predictions WHERE user_id IS NOT NULL"
        ).fetchall()
        return [int(r["user_id"]) for r in rows]


async def get_active_user_ids() -> list[int]:
    return await asyncio.to_thread(_get_active_user_ids_sync)
