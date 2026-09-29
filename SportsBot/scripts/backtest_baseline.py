#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import database as db
from baseline_model import (
    _prefer_core_top_pick_candidate,
    build_market_candidates,
    build_numeric_baseline,
    choose_action_pick,
    derive_expanded_market_probabilities,
    infer_blended_probabilities,
)
from models import MatchData, PredictionFeatureSnapshot
from trainable_numeric_model import fit_trainable_numeric_model, predict_trainable_numeric_model


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline backtest report for stored predictions and canonical historical rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only evaluate the N most recent rows in each selected dataset.",
    )
    parser.add_argument(
        "--dataset",
        choices=("predictions", "historical", "both"),
        default="predictions",
        help="Choose which backtest dataset to evaluate.",
    )
    return parser.parse_args()


def _format_metric(name: str, value: Optional[float], count: int) -> str:
    if value is None:
        return f"{name:<20} n=0"
    return f"{name:<20} n={count:<4} brier={value:.4f}"


def _format_compact_metric(value: Optional[float], count: int) -> str:
    if value is None or count <= 0:
        return "n=0"
    return f"n={count:<3} brier={value:.4f}"


def _format_coverage_metric(count: int, total: int) -> str:
    if total <= 0:
        return "0/0 (0.0%)"
    return f"{count}/{total} ({(count * 100.0) / total:.1f}%)"


def _normalize_pick_label(label: str) -> str:
    normalized = " ".join(str(label).strip().lower().split())
    for prefix in ("speculative lean:", "action mode pick:", "speculative:", "lean:"):
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def _resolve_market_field_from_codes(
    market_code: str,
    selection_code: str,
) -> tuple[Optional[str], str]:
    mapping = {
        ("1X2", "TEAM1"): ("odds_w1", "1x2"),
        ("1X2", "DRAW"): ("odds_draw", "1x2"),
        ("1X2", "TEAM2"): ("odds_w2", "1x2"),
        ("TOTALS_1_5", "OVER"): ("odds_over_1_5", "totals"),
        ("TOTALS_1_5", "UNDER"): ("odds_under_1_5", "totals"),
        ("TOTALS_2_5", "OVER"): ("odds_over_2_5", "totals"),
        ("TOTALS_2_5", "UNDER"): ("odds_under_2_5", "totals"),
        ("TOTALS_3_5", "OVER"): ("odds_over_3_5", "totals"),
        ("TOTALS_3_5", "UNDER"): ("odds_under_3_5", "totals"),
        ("BTTS", "YES"): ("odds_btts_yes", "btts"),
        ("BTTS", "NO"): ("odds_btts_no", "btts"),
        ("DNB", "TEAM1"): ("odds_dnb_team1", "dnb"),
        ("DNB", "TEAM2"): ("odds_dnb_team2", "dnb"),
        ("DOUBLE_CHANCE", "1X"): ("odds_dc_1x", "double_chance"),
        ("DOUBLE_CHANCE", "12"): ("odds_dc_12", "double_chance"),
        ("DOUBLE_CHANCE", "X2"): ("odds_dc_x2", "double_chance"),
    }
    return mapping.get((market_code, selection_code), (None, "other"))


def _parse_actual_goals(row: dict) -> Optional[tuple[int, int]]:
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
    try:
        left, right = str(score).replace(" ", "").split("-", 1)
        return int(left), int(right)
    except (AttributeError, TypeError, ValueError):
        return None


def _grade_market_selection(
    row: dict,
    market_code: str,
    selection_code: str,
) -> Optional[str]:
    market_code = str(market_code or "").strip().upper()
    selection_code = str(selection_code or "").strip().upper()
    actual_result = str(row.get("actual_result") or "").strip().upper()
    goals = _parse_actual_goals(row)

    if market_code == "1X2":
        if actual_result not in {"W1", "DRAW", "W2"}:
            return None
        expected = {"TEAM1": "W1", "DRAW": "DRAW", "TEAM2": "W2"}.get(selection_code)
        if not expected:
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


def _profit_units_from_outcome(outcome: Optional[str], odds: Optional[float]) -> Optional[float]:
    if outcome is None or odds is None or odds <= 1.0:
        return None
    if outcome == "WIN":
        return round(odds - 1.0, 4)
    if outcome == "PUSH":
        return 0.0
    if outcome == "LOSS":
        return -1.0
    return None


def _parse_row_datetime(row: dict) -> Optional[datetime]:
    raw_value = row.get("predicted_at")
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _time_split_rows(
    rows: list[dict],
    *,
    minimum_rows: int = 9,
) -> list[dict]:
    chronological = [
        {"sort_dt": sort_dt, "row": row}
        for row in rows
        if (sort_dt := _parse_row_datetime(row)) is not None
    ]
    chronological.sort(key=lambda item: item["sort_dt"])
    count = len(chronological)
    if count < minimum_rows:
        return []

    train_count = max(1, int(count * 0.6))
    validate_count = max(1, int(count * 0.2))
    if train_count + validate_count >= count:
        validate_count = max(1, count - train_count - 1)
    test_count = count - train_count - validate_count
    if test_count < 1:
        if train_count > validate_count:
            train_count -= 1
        else:
            validate_count -= 1
        test_count = count - train_count - validate_count
    if train_count < 1 or validate_count < 1 or test_count < 1:
        return []

    slices = [
        ("Train", chronological[:train_count]),
        ("Validate", chronological[train_count:train_count + validate_count]),
        ("Test", chronological[train_count + validate_count:]),
    ]
    result: list[dict] = []
    for label, chunk in slices:
        if not chunk:
            continue
        result.append(
            {
                "label": label,
                "rows": [item["row"] for item in chunk],
                "count": len(chunk),
                "date_from": chunk[0]["sort_dt"].date().isoformat(),
                "date_to": chunk[-1]["sort_dt"].date().isoformat(),
            }
        )
    return result

def _extract_prediction_clv_entry(row: dict) -> Optional[dict]:
    recommendation = str(row.get("top_recommendation") or "").strip()
    normalized = _normalize_pick_label(recommendation)
    market_code = str(row.get("top_market_code") or "").strip().upper()
    selection_code = str(row.get("top_selection_code") or "").strip().upper()
    if not normalized or normalized.startswith("no bet") or market_code == "NO_BET":
        return None

    team1 = " ".join(str(row.get("team1") or "").strip().lower().split())
    team2 = " ".join(str(row.get("team2") or "").strip().lower().split())
    market_field: Optional[str]
    family: str

    market_field, family = _resolve_market_field_from_codes(market_code, selection_code)
    if not market_field:
        if any(token in normalized for token in ("over 1.5", "o1.5", "тб 1.5")):
            market_field, family = "odds_over_1_5", "totals"
            market_code, selection_code = "TOTALS_1_5", "OVER"
        elif any(token in normalized for token in ("under 1.5", "u1.5", "тм 1.5")):
            market_field, family = "odds_under_1_5", "totals"
            market_code, selection_code = "TOTALS_1_5", "UNDER"
        elif any(token in normalized for token in ("over 2.5", "o2.5", "тб 2.5")):
            market_field, family = "odds_over_2_5", "totals"
            market_code, selection_code = "TOTALS_2_5", "OVER"
        elif any(token in normalized for token in ("under 2.5", "u2.5", "тм 2.5")):
            market_field, family = "odds_under_2_5", "totals"
            market_code, selection_code = "TOTALS_2_5", "UNDER"
        elif any(token in normalized for token in ("over 3.5", "o3.5", "тб 3.5")):
            market_field, family = "odds_over_3_5", "totals"
            market_code, selection_code = "TOTALS_3_5", "OVER"
        elif any(token in normalized for token in ("under 3.5", "u3.5", "тм 3.5")):
            market_field, family = "odds_under_3_5", "totals"
            market_code, selection_code = "TOTALS_3_5", "UNDER"
        elif "btts" in normalized or "обе забьют" in normalized:
            family = "btts"
            if any(token in normalized for token in ("yes", "да")):
                market_field = "odds_btts_yes"
                market_code, selection_code = "BTTS", "YES"
            elif any(token in normalized for token in ("no", "нет")):
                market_field = "odds_btts_no"
                market_code, selection_code = "BTTS", "NO"
            else:
                market_field = None
        elif "draw no bet" in normalized or "dnb" in normalized:
            family = "dnb"
            if team1 and team1 in normalized:
                market_field = "odds_dnb_team1"
                market_code, selection_code = "DNB", "TEAM1"
            elif team2 and team2 in normalized:
                market_field = "odds_dnb_team2"
                market_code, selection_code = "DNB", "TEAM2"
            else:
                market_field = None
        elif "1x" in normalized or (team1 and team1 in normalized and "or draw" in normalized):
            market_field, family = "odds_dc_1x", "double_chance"
            market_code, selection_code = "DOUBLE_CHANCE", "1X"
        elif "12" in normalized or "no draw" in normalized:
            market_field, family = "odds_dc_12", "double_chance"
            market_code, selection_code = "DOUBLE_CHANCE", "12"
        elif "x2" in normalized or (team2 and team2 in normalized and "draw or" in normalized):
            market_field, family = "odds_dc_x2", "double_chance"
            market_code, selection_code = "DOUBLE_CHANCE", "X2"
        elif normalized == "draw" or normalized.startswith("draw"):
            market_field, family = "odds_draw", "1x2"
            market_code, selection_code = "1X2", "DRAW"
        elif team1 and (
            normalized == team1
            or f"{team1} win" in normalized
            or normalized.startswith(f"{team1} to win")
        ):
            market_field, family = "odds_w1", "1x2"
            market_code, selection_code = "1X2", "TEAM1"
        elif team2 and (
            normalized == team2
            or f"{team2} win" in normalized
            or normalized.startswith(f"{team2} to win")
        ):
            market_field, family = "odds_w2", "1x2"
            market_code, selection_code = "1X2", "TEAM2"

    if not market_field or not market_code or not selection_code:
        return None

    entry_odds = row.get("entry_odds")
    open_odds = row.get(f"open_{market_field}")
    close_odds = row.get(f"close_{market_field}")
    if entry_odds is None:
        entry_odds = open_odds
    try:
        entry_odds_f = float(entry_odds) if entry_odds is not None else None
        open_odds_f = float(open_odds) if open_odds is not None else None
        close_odds_f = float(close_odds) if close_odds is not None else None
    except (TypeError, ValueError):
        return None

    if (
        entry_odds_f is None
        or close_odds_f is None
        or entry_odds_f <= 1.0
        or close_odds_f <= 1.0
    ):
        return None

    entry_to_close_delta = round(close_odds_f - entry_odds_f, 4)
    open_to_close_delta = None
    if open_odds_f is not None and open_odds_f > 1.0:
        open_to_close_delta = round(close_odds_f - open_odds_f, 4)
    outcome = _grade_market_selection(row, market_code, selection_code)

    return {
        "market_code": market_code,
        "selection_code": selection_code,
        "prediction_id": row.get("prediction_id"),
        "label": recommendation,
        "competition_name": row.get("competition_name"),
        "policy_flags": list(row.get("policy_flags") or []),
        "family": family,
        "market_field": market_field,
        "entry_odds": entry_odds_f,
        "open_odds": open_odds_f,
        "close_odds": close_odds_f,
        "entry_to_close_delta": entry_to_close_delta,
        "open_to_close_delta": open_to_close_delta,
        "beat_close": close_odds_f < entry_odds_f - 1e-9,
        "flat_close": abs(close_odds_f - entry_odds_f) <= 1e-9,
        "implied_prob_delta_pct": round((100.0 / close_odds_f) - (100.0 / entry_odds_f), 4),
        "settlement": outcome,
        "profit_units": _profit_units_from_outcome(outcome, entry_odds_f),
    }


def _extract_value_candidate_clv_entries(row: dict) -> list[dict]:
    entries: list[dict] = []
    for candidate in row.get("value_candidate_snapshot") or []:
        market_code = str(candidate.get("market_code") or "").strip().upper()
        selection_code = str(candidate.get("selection_code") or "").strip().upper()
        market_field, family = _resolve_market_field_from_codes(market_code, selection_code)
        if not market_field:
            continue

        entry_odds = candidate.get("odds")
        open_odds = row.get(f"open_{market_field}")
        close_odds = row.get(f"close_{market_field}")
        try:
            entry_odds_f = float(entry_odds) if entry_odds is not None else None
            open_odds_f = float(open_odds) if open_odds is not None else None
            close_odds_f = float(close_odds) if close_odds is not None else None
        except (TypeError, ValueError):
            continue

        if (
            entry_odds_f is None
            or close_odds_f is None
            or entry_odds_f <= 1.0
            or close_odds_f <= 1.0
        ):
            continue

        open_to_close_delta = None
        if open_odds_f is not None and open_odds_f > 1.0:
            open_to_close_delta = round(close_odds_f - open_odds_f, 4)
        outcome = _grade_market_selection(row, market_code, selection_code)

        entries.append(
            {
                "market_code": market_code,
                "selection_code": selection_code,
                "prediction_id": row.get("prediction_id"),
                "label": candidate.get("label"),
                "competition_name": row.get("competition_name"),
                "family": family,
                "market_field": market_field,
                "entry_odds": entry_odds_f,
                "open_odds": open_odds_f,
                "close_odds": close_odds_f,
                "entry_to_close_delta": round(close_odds_f - entry_odds_f, 4),
                "open_to_close_delta": open_to_close_delta,
                "beat_close": close_odds_f < entry_odds_f - 1e-9,
                "flat_close": abs(close_odds_f - entry_odds_f) <= 1e-9,
                "implied_prob_delta_pct": round((100.0 / close_odds_f) - (100.0 / entry_odds_f), 4),
                "settlement": outcome,
                "profit_units": _profit_units_from_outcome(outcome, entry_odds_f),
            }
        )
    return entries


def _summarize_prediction_clv(entries: list[dict]) -> Optional[dict]:
    if not entries:
        return None

    beat_close = sum(1 for entry in entries if entry.get("beat_close"))
    flat_close = sum(1 for entry in entries if entry.get("flat_close"))
    family_counts: dict[str, int] = {}
    entry_deltas = [float(entry["entry_to_close_delta"]) for entry in entries]
    implied_deltas = [float(entry["implied_prob_delta_pct"]) for entry in entries]
    open_close_deltas = [
        float(entry["open_to_close_delta"])
        for entry in entries
        if entry.get("open_to_close_delta") is not None
    ]
    settled_entries = [entry for entry in entries if entry.get("settlement") in {"WIN", "LOSS", "PUSH"}]
    decisive_entries = [entry for entry in settled_entries if entry.get("settlement") in {"WIN", "LOSS"}]
    wins = sum(1 for entry in settled_entries if entry.get("settlement") == "WIN")
    losses = sum(1 for entry in settled_entries if entry.get("settlement") == "LOSS")
    pushes = sum(1 for entry in settled_entries if entry.get("settlement") == "PUSH")
    profits = [
        float(entry["profit_units"])
        for entry in settled_entries
        if entry.get("profit_units") is not None
    ]

    for entry in entries:
        family = str(entry.get("family") or "other")
        family_counts[family] = family_counts.get(family, 0) + 1

    count = len(entries)
    return {
        "count": count,
        "beat_close": beat_close,
        "flat_close": flat_close,
        "miss_close": count - beat_close - flat_close,
        "beat_close_rate": round((beat_close * 100.0) / count, 2),
        "avg_entry_to_close_delta": round(sum(entry_deltas) / count, 4),
        "avg_abs_entry_to_close_delta": round(sum(abs(delta) for delta in entry_deltas) / count, 4),
        "avg_implied_prob_delta_pct": round(sum(implied_deltas) / count, 4),
        "avg_open_to_close_delta": (
            round(sum(open_close_deltas) / len(open_close_deltas), 4)
            if open_close_deltas else None
        ),
        "family_counts": family_counts,
        "settled_count": len(settled_entries),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "decisive_count": len(decisive_entries),
        "hit_rate": (
            round((wins * 100.0) / len(decisive_entries), 2)
            if decisive_entries else None
        ),
        "avg_profit_units": (
            round(sum(profits) / len(profits), 4)
            if profits else None
        ),
        "roi_pct": (
            round((sum(profits) * 100.0) / len(profits), 2)
            if profits else None
        ),
    }


def _segment_prediction_clv(
    entries: list[dict],
    *,
    key_name: str,
    minimum_count: int = 1,
) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        raw_key = entry.get(key_name)
        if raw_key is None:
            continue
        key = str(raw_key).strip()
        if not key:
            continue
        grouped.setdefault(key, []).append(entry)

    results: list[dict] = []
    for key, bucket in grouped.items():
        if len(bucket) < minimum_count:
            continue
        summary = _summarize_prediction_clv(bucket)
        if summary is None:
            continue
        sample_gate, sample_gate_rank = _segment_sample_gate(summary)
        results.append(
            {
                "segment": key,
                "sample_gate": sample_gate,
                "sample_gate_rank": sample_gate_rank,
                **summary,
            }
        )

    results.sort(
        key=lambda item: (
            -int(item.get("sample_gate_rank") or 0),
            -int(item.get("count") or 0),
            -float(item.get("beat_close_rate") or 0.0),
            str(item.get("segment") or ""),
        ),
    )
    return results


def _segment_prediction_clv_by_policy_flag(
    entries: list[dict],
    *,
    minimum_count: int = 1,
) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        for raw_flag in entry.get("policy_flags") or []:
            flag = str(raw_flag).strip()
            if not flag:
                continue
            grouped.setdefault(flag, []).append(entry)

    results: list[dict] = []
    for flag, bucket in grouped.items():
        if len(bucket) < minimum_count:
            continue
        summary = _summarize_prediction_clv(bucket)
        if summary is None:
            continue
        sample_gate, sample_gate_rank = _segment_sample_gate(summary)
        results.append(
            {
                "segment": flag,
                "sample_gate": sample_gate,
                "sample_gate_rank": sample_gate_rank,
                **summary,
            }
        )

    results.sort(
        key=lambda item: (
            -int(item.get("sample_gate_rank") or 0),
            -int(item.get("count") or 0),
            -float(item.get("beat_close_rate") or 0.0),
            str(item.get("segment") or ""),
        ),
    )
    return results


def _segment_prediction_clv_by_competition_policy_flag(
    entries: list[dict],
    *,
    minimum_count: int = 1,
) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        competition_name = str(entry.get("competition_name") or "").strip()
        if not competition_name:
            continue
        for raw_flag in entry.get("policy_flags") or []:
            flag = str(raw_flag).strip()
            if not flag:
                continue
            grouped.setdefault(f"{competition_name} / {flag}", []).append(entry)

    results: list[dict] = []
    for segment, bucket in grouped.items():
        if len(bucket) < minimum_count:
            continue
        summary = _summarize_prediction_clv(bucket)
        if summary is None:
            continue
        sample_gate, sample_gate_rank = _segment_sample_gate(summary)
        results.append(
            {
                "segment": segment,
                "sample_gate": sample_gate,
                "sample_gate_rank": sample_gate_rank,
                **summary,
            }
        )

    results.sort(
        key=lambda item: (
            -int(item.get("sample_gate_rank") or 0),
            -int(item.get("count") or 0),
            -float(item.get("beat_close_rate") or 0.0),
            str(item.get("segment") or ""),
        ),
    )
    return results


def _policy_flag_category(flag: str) -> tuple[str, int]:
    normalized = str(flag or "").strip().lower()
    if normalized.startswith("path_"):
        return "Execution path", 0
    if normalized.startswith("top_market_"):
        return "Market type", 1
    if normalized.startswith("top_family_"):
        return "Historical trust", 2
    if normalized in {"action_mode_pick", "top_pick_normalized_core", "btts_borderline_rerank"}:
        return "Selection policy", 3
    return "Other", 4


def _humanize_policy_flag(flag: str) -> str:
    normalized = str(flag or "").strip().lower()
    explicit = {
        "path_pre_ai_gate": "Path: pre-AI gate",
        "path_agent_chain": "Path: agent chain",
        "path_single_call": "Path: single-call",
        "path_fallback_numeric": "Path: numeric fallback",
        "no_bet": "No bet",
        "action_mode_pick": "Action mode pick",
        "top_pick_normalized_core": "Top pick normalized to core",
        "top_market_core": "Top market: core",
        "top_market_derived": "Top market: derived",
        "top_family_positive_trust": "Top family: positive trust",
        "top_family_negative_trust": "Top family: negative trust",
        "btts_borderline_rerank": "BTTS borderline rerank",
        "pre_ai_gate_playable_seed": "Pre-AI gate playable seed",
    }
    if normalized in explicit:
        return explicit[normalized]
    return normalized.replace("_", " ")


def _split_competition_policy_flag(segment: str) -> tuple[str, str]:
    raw_segment = str(segment or "").strip()
    if " / " not in raw_segment:
        return "Unknown", raw_segment
    competition_name, flag = raw_segment.rsplit(" / ", 1)
    return competition_name.strip() or "Unknown", flag.strip()


def _segment_sample_gate(summary: dict) -> tuple[str, int]:
    count = int(summary.get("count") or 0)
    settled_count = int(summary.get("settled_count") or 0)
    decisive_count = int(summary.get("decisive_count") or 0)

    if count >= 20 and settled_count >= 12 and decisive_count >= 10:
        return "stable", 3
    if count >= 8 and settled_count >= 5 and decisive_count >= 4:
        return "developing", 2
    if count >= 4 and settled_count >= 3 and decisive_count >= 2:
        return "thin", 1
    return "low-sample", 0


def _print_segmented_clv(
    title: str,
    segments: list[dict],
    *,
    limit: int = 8,
) -> None:
    print(f"\n{title}")
    if not segments:
        print("  no segments")
        return
    for segment in segments[:limit]:
        hit_part = (
            f"  hit={segment['hit_rate']:>5.1f}%"
            if segment.get("hit_rate") is not None else ""
        )
        roi_part = (
            f"  roi={segment['roi_pct']:+.2f}%"
            if segment.get("roi_pct") is not None else ""
        )
        print(
            "  "
            f"{segment['segment']:<22} "
            f"[{segment.get('sample_gate', 'low-sample'):<11}] "
            f"n={segment['count']:<3} "
            f"beat={segment['beat_close_rate']:>5.1f}% "
            f"avg={segment['avg_entry_to_close_delta']:+.4f} "
            f"avg_abs={segment['avg_abs_entry_to_close_delta']:.4f}"
            f"{hit_part}"
            f"{roi_part}"
        )


def _print_policy_flag_diagnostics(
    title: str,
    segments: list[dict],
    *,
    limit_per_category: int = 4,
) -> None:
    print(f"\n{title}")
    if not segments:
        print("  no policy-flag segments")
        return

    grouped: dict[str, list[dict]] = {}
    category_order: dict[str, int] = {}
    for segment in segments:
        category, rank = _policy_flag_category(str(segment.get("segment") or ""))
        grouped.setdefault(category, []).append(segment)
        category_order.setdefault(category, rank)

    for category in sorted(grouped, key=lambda item: category_order.get(item, 99)):
        print(f"  {category}:")
        for segment in grouped[category][:limit_per_category]:
            hit_part = (
                f" hit={segment['hit_rate']:>5.1f}%"
                if segment.get("hit_rate") is not None else ""
            )
            roi_part = (
                f" roi={segment['roi_pct']:+.2f}%"
                if segment.get("roi_pct") is not None else ""
            )
            print(
                "    "
                f"{_humanize_policy_flag(str(segment.get('segment') or '')):<28} "
                f"[{segment.get('sample_gate', 'low-sample'):<11}] "
                f"n={segment['count']:<3} "
                f"beat={segment['beat_close_rate']:>5.1f}% "
                f"avg={segment['avg_entry_to_close_delta']:+.4f}"
                f"{hit_part}"
                f"{roi_part}"
            )


def _print_competition_policy_flag_diagnostics(
    title: str,
    segments: list[dict],
    *,
    limit_per_category: int = 6,
) -> None:
    print(f"\n{title}")
    if not segments:
        print("  no competition-policy segments")
        return

    grouped: dict[str, list[dict]] = {}
    category_order: dict[str, int] = {}
    for segment in segments:
        _competition_name, flag = _split_competition_policy_flag(str(segment.get("segment") or ""))
        category, rank = _policy_flag_category(flag)
        grouped.setdefault(category, []).append(segment)
        category_order.setdefault(category, rank)

    for category in sorted(grouped, key=lambda item: category_order.get(item, 99)):
        print(f"  {category}:")
        for segment in grouped[category][:limit_per_category]:
            competition_name, flag = _split_competition_policy_flag(str(segment.get("segment") or ""))
            hit_part = (
                f" hit={segment['hit_rate']:>5.1f}%"
                if segment.get("hit_rate") is not None else ""
            )
            roi_part = (
                f" roi={segment['roi_pct']:+.2f}%"
                if segment.get("roi_pct") is not None else ""
            )
            print(
                "    "
                f"{competition_name} / {_humanize_policy_flag(flag):<24} "
                f"[{segment.get('sample_gate', 'low-sample'):<11}] "
                f"n={segment['count']:<3} "
                f"beat={segment['beat_close_rate']:>5.1f}% "
                f"avg={segment['avg_entry_to_close_delta']:+.4f}"
                f"{hit_part}"
                f"{roi_part}"
            )


def _summarize_live_market_drift(rows: list[dict]) -> dict[str, dict]:
    market_groups = {
        "1X2": ("odds_w1", "odds_draw", "odds_w2"),
        "O/U 2.5": ("odds_over_2_5", "odds_under_2_5"),
        "BTTS": ("odds_btts_yes", "odds_btts_no"),
    }
    summary: dict[str, dict] = {}
    for label, fields in market_groups.items():
        deltas: list[float] = []
        for row in rows:
            for field in fields:
                open_odds = row.get(f"open_{field}")
                close_odds = row.get(f"close_{field}")
                if open_odds is None or close_odds is None:
                    continue
                try:
                    open_odds_f = float(open_odds)
                    close_odds_f = float(close_odds)
                except (TypeError, ValueError):
                    continue
                if open_odds_f <= 1.0 or close_odds_f <= 1.0:
                    continue
                deltas.append(close_odds_f - open_odds_f)
        if deltas:
            summary[label] = {
                "samples": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 4),
                "avg_abs_delta": round(sum(abs(delta) for delta in deltas) / len(deltas), 4),
            }
    return summary


def _summarize_market_coverage(rows: list[dict]) -> dict[str, dict[str, int]]:
    total = len(rows)
    groups = {
        "1X2": ("odds_w1", "odds_draw", "odds_w2"),
        "O/U 2.5": ("odds_over_2_5", "odds_under_2_5"),
        "BTTS": ("odds_btts_yes", "odds_btts_no"),
        "DNB": ("odds_dnb_team1", "odds_dnb_team2"),
        "Double Chance": ("odds_dc_1x", "odds_dc_12", "odds_dc_x2"),
        "AH": ("ah_team1_odds", "ah_team2_odds"),
    }
    summary: dict[str, dict[str, int]] = {}
    for label, fields in groups.items():
        present = 0
        for row in rows:
            if all(row.get(field) is not None for field in fields):
                present += 1
        summary[label] = {"count": present, "total": total}
    return summary


def _print_market_coverage(rows: list[dict], *, title: str) -> None:
    summary = _summarize_market_coverage(rows)
    print(f"\n{title}")
    if not rows:
        print("  no rows")
        return
    for label, metrics in summary.items():
        print(f"  {label:<14} {_format_coverage_metric(metrics['count'], metrics['total'])}")


def _market_family_trust_band(profile: dict) -> tuple[str, str, int]:
    sample_size = int(profile.get("sample_size") or 0)
    trust_score = float(profile.get("trust_score") or 0.0)

    if sample_size >= 250:
        sample_band, sample_rank = "stable", 3
    elif sample_size >= 80:
        sample_band, sample_rank = "developing", 2
    else:
        sample_band, sample_rank = "thin", 1

    if trust_score >= 1.5:
        signal = "strong"
    elif trust_score >= 0.5:
        signal = "positive"
    elif trust_score <= -1.5:
        signal = "weak"
    elif trust_score <= -0.5:
        signal = "negative"
    else:
        signal = "neutral"
    return signal, sample_band, sample_rank


def _prepare_market_family_trust_rows(
    profiles: list[dict],
    *,
    include_competition: bool,
) -> list[dict]:
    prepared: list[dict] = []
    for profile in profiles:
        signal, sample_band, sample_rank = _market_family_trust_band(profile)
        family = str(profile.get("family") or "").strip() or "unknown"
        competition_key = str(profile.get("competition_key") or "").strip()
        segment = family if not include_competition else f"{competition_key} / {family}"
        prepared.append(
            {
                "segment": segment,
                "family": family,
                "competition_key": competition_key or None,
                "sample_size": int(profile.get("sample_size") or 0),
                "model_brier": float(profile.get("model_brier") or 0.0),
                "market_brier": float(profile.get("market_brier") or 0.0),
                "brier_delta": float(profile.get("brier_delta") or 0.0),
                "trust_score": float(profile.get("trust_score") or 0.0),
                "signal": signal,
                "sample_band": sample_band,
                "sample_rank": sample_rank,
            }
        )
    prepared.sort(
        key=lambda item: (
            int(item["sample_rank"]),
            abs(float(item["trust_score"])),
            int(item["sample_size"]),
        ),
        reverse=True,
    )
    return prepared


def _print_market_family_trust(title: str, profiles: list[dict], *, limit: int = 10) -> None:
    print(f"\n{title}")
    if not profiles:
        print("  no profiles")
        return
    rows = _prepare_market_family_trust_rows(
        profiles,
        include_competition=any(profile.get("competition_key") for profile in profiles),
    )
    for row in rows[:limit]:
        print(
            "  "
            f"{row['segment']:<34} "
            f"[{row['signal']:<8}/{row['sample_band']:<10}] "
            f"n={row['sample_size']:<4} "
            f"trust={row['trust_score']:+.2f} "
            f"dbrier={row['brier_delta']:+.4f}"
        )


def _summarize_family_gating_impact(rows: list[dict], *, family: str) -> Optional[dict]:
    considered = 0
    eligible_neutral_top = 0
    eligible_trust_top = 0
    eligible_neutral_action = 0
    eligible_trust_action = 0
    promoted_top = 0
    demoted_top = 0
    promoted_action = 0
    demoted_action = 0
    top_family_neutral = 0
    top_family_trust = 0
    positive_trust_rows = 0
    negative_trust_rows = 0
    trust_sum = 0.0
    trust_count = 0

    for row in rows:
        snapshot = _snapshot_from_row(row)
        if snapshot is None:
            continue
        report_dict = _candidate_report_from_row(row)
        if report_dict is None:
            continue
        neutral_candidates = build_market_candidates(report_dict, snapshot, None, [])
        trust_candidates = build_market_candidates(
            report_dict,
            snapshot,
            None,
            row.get("_family_quality_profiles"),
        )
        neutral_family = _best_family_candidate(neutral_candidates, family)
        trust_family = _best_family_candidate(trust_candidates, family)
        if neutral_family is None and trust_family is None:
            continue

        considered += 1
        trust_profile = None
        for profile in row.get("_family_quality_profiles") or []:
            if str(profile.get("family") or "").strip().lower() == family:
                trust_profile = profile
                break
        if trust_profile is not None:
            trust_score = float(trust_profile.get("trust_score") or 0.0)
            trust_sum += trust_score
            trust_count += 1
            if trust_score > 0.5:
                positive_trust_rows += 1
            elif trust_score < -0.5:
                negative_trust_rows += 1

        neutral_top = bool(neutral_family and neutral_family.get("eligible_for_top_pick"))
        trust_top = bool(trust_family and trust_family.get("eligible_for_top_pick"))
        neutral_action = bool(neutral_family and neutral_family.get("eligible_for_action_pick"))
        trust_action = bool(trust_family and trust_family.get("eligible_for_action_pick"))
        if neutral_top:
            eligible_neutral_top += 1
        if trust_top:
            eligible_trust_top += 1
        if neutral_action:
            eligible_neutral_action += 1
        if trust_action:
            eligible_trust_action += 1
        if not neutral_top and trust_top:
            promoted_top += 1
        if neutral_top and not trust_top:
            demoted_top += 1
        if not neutral_action and trust_action:
            promoted_action += 1
        if neutral_action and not trust_action:
            demoted_action += 1
        if neutral_candidates and str(neutral_candidates[0].get("family") or "") == family:
            top_family_neutral += 1
        if trust_candidates and str(trust_candidates[0].get("family") or "") == family:
            top_family_trust += 1

    if considered <= 0:
        return None
    return {
        "family": family,
        "considered": considered,
        "eligible_neutral_top": eligible_neutral_top,
        "eligible_trust_top": eligible_trust_top,
        "eligible_neutral_action": eligible_neutral_action,
        "eligible_trust_action": eligible_trust_action,
        "promoted_top": promoted_top,
        "demoted_top": demoted_top,
        "promoted_action": promoted_action,
        "demoted_action": demoted_action,
        "top_family_neutral": top_family_neutral,
        "top_family_trust": top_family_trust,
        "positive_trust_rows": positive_trust_rows,
        "negative_trust_rows": negative_trust_rows,
        "avg_trust_score": round(trust_sum / trust_count, 3) if trust_count else None,
    }


def _print_family_gating_impact(summary: Optional[dict]) -> None:
    print("\nBTTS Trust + Gating Impact")
    if summary is None:
        print("  no evaluable rows")
        return
    print(f"  considered rows: {summary['considered']}")
    print(
        f"  top-pick eligible: neutral {summary['eligible_neutral_top']} -> trust {summary['eligible_trust_top']} "
        f"(+{summary['promoted_top']} / -{summary['demoted_top']})"
    )
    print(
        f"  action eligible:   neutral {summary['eligible_neutral_action']} -> trust {summary['eligible_trust_action']} "
        f"(+{summary['promoted_action']} / -{summary['demoted_action']})"
    )
    print(
        f"  family selected as top candidate: neutral {summary['top_family_neutral']} -> trust {summary['top_family_trust']}"
    )
    if summary["avg_trust_score"] is not None:
        print(
            f"  trust mix: avg={summary['avg_trust_score']:+.3f}  "
            f"positive_rows={summary['positive_trust_rows']}  negative_rows={summary['negative_trust_rows']}"
        )


def _summarize_action_core_preference_impact(rows: list[dict]) -> Optional[dict]:
    considered = 0
    switched = 0
    neutral_missing = 0
    trust_missing = 0
    switched_to_core = 0
    derived_to_core = 0
    switched_to_btts = 0
    switched_to_totals = 0
    switched_to_1x2 = 0
    trust_sum = 0.0
    trust_count = 0
    by_competition: dict[str, int] = {}
    by_target_family: dict[str, int] = {}

    for row in rows:
        snapshot = _snapshot_from_row(row)
        if snapshot is None:
            continue
        report_dict = _candidate_report_from_row(row)
        if report_dict is None:
            continue

        neutral_pick = choose_action_pick(report_dict, snapshot, None, [])
        trust_pick = choose_action_pick(
            report_dict,
            snapshot,
            None,
            row.get("_family_quality_profiles"),
        )
        if neutral_pick is None and trust_pick is None:
            continue

        considered += 1
        if neutral_pick is None:
            neutral_missing += 1
        if trust_pick is None:
            trust_missing += 1
        if neutral_pick is None or trust_pick is None:
            continue

        neutral_label = str(neutral_pick.get("label") or "")
        trust_label = str(trust_pick.get("label") or "")
        if neutral_label == trust_label:
            continue

        switched += 1
        trust_family = str(trust_pick.get("family") or "")
        neutral_family = str(neutral_pick.get("family") or "")
        by_target_family[trust_family] = by_target_family.get(trust_family, 0) + 1
        competition = str(row.get("competition_name") or "").strip() or "unknown"
        by_competition[competition] = by_competition.get(competition, 0) + 1

        trust_score = trust_pick.get("family_trust_score")
        if trust_score is not None:
            trust_sum += float(trust_score)
            trust_count += 1

        trust_is_core = trust_family in {"1x2", "totals", "btts"}
        neutral_is_core = neutral_family in {"1x2", "totals", "btts"}
        if trust_is_core:
            switched_to_core += 1
            if trust_family == "btts":
                switched_to_btts += 1
            elif trust_family == "totals":
                switched_to_totals += 1
            elif trust_family == "1x2":
                switched_to_1x2 += 1
        if (not neutral_is_core) and trust_is_core:
            derived_to_core += 1

    if considered <= 0:
        return None

    top_competitions = sorted(
        by_competition.items(),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )[:5]
    top_target_families = sorted(
        by_target_family.items(),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )

    return {
        "considered": considered,
        "switched": switched,
        "neutral_missing": neutral_missing,
        "trust_missing": trust_missing,
        "switched_to_core": switched_to_core,
        "derived_to_core": derived_to_core,
        "switched_to_btts": switched_to_btts,
        "switched_to_totals": switched_to_totals,
        "switched_to_1x2": switched_to_1x2,
        "avg_target_trust": round(trust_sum / trust_count, 3) if trust_count else None,
        "top_competitions": top_competitions,
        "top_target_families": top_target_families,
    }


def _print_action_core_preference_impact(summary: Optional[dict]) -> None:
    print("\nAction Core Preference Impact")
    if summary is None:
        print("  no evaluable rows")
        return

    print(f"  considered rows: {summary['considered']}")
    print(
        f"  action pick changed: {summary['switched']}  "
        f"(neutral missing {summary['neutral_missing']}, trust missing {summary['trust_missing']})"
    )
    print(
        f"  switched to core family: {summary['switched_to_core']}  "
        f"(derived->core {summary['derived_to_core']})"
    )
    print(
        f"  target families: BTTS {summary['switched_to_btts']}  "
        f"Totals {summary['switched_to_totals']}  "
        f"1X2 {summary['switched_to_1x2']}"
    )
    if summary["avg_target_trust"] is not None:
        print(f"  avg trust of chosen family on switched rows: {summary['avg_target_trust']:+.3f}")
    if summary["top_target_families"]:
        family_text = ", ".join(f"{family}={count}" for family, count in summary["top_target_families"])
        print(f"  switched target families by volume: {family_text}")
    if summary["top_competitions"]:
        comp_text = ", ".join(f"{competition}={count}" for competition, count in summary["top_competitions"])
        print(f"  top competitions: {comp_text}")


def _summarize_top_pick_core_preference_impact(rows: list[dict]) -> Optional[dict]:
    considered = 0
    switched = 0
    derived_to_core = 0
    switched_to_btts = 0
    switched_to_totals = 0
    switched_to_1x2 = 0
    trust_sum = 0.0
    trust_count = 0
    by_competition: dict[str, int] = {}
    by_target_family: dict[str, int] = {}

    for row in rows:
        snapshot = _snapshot_from_row(row)
        if snapshot is None:
            continue
        report_dict = _candidate_report_from_row(row)
        if report_dict is None:
            continue

        candidates = build_market_candidates(
            report_dict,
            snapshot,
            None,
            row.get("_family_quality_profiles"),
        )
        if not candidates:
            continue

        considered += 1
        top_candidate = candidates[0]
        preferred_candidate = _prefer_core_top_pick_candidate(top_candidate, candidates)
        if preferred_candidate is None:
            continue

        top_label = str(top_candidate.get("label") or "")
        preferred_label = str(preferred_candidate.get("label") or "")
        if top_label == preferred_label:
            continue

        switched += 1
        top_family = str(top_candidate.get("family") or "")
        preferred_family = str(preferred_candidate.get("family") or "")
        if top_family not in {"1x2", "totals", "btts"} and preferred_family in {"1x2", "totals", "btts"}:
            derived_to_core += 1
        if preferred_family == "btts":
            switched_to_btts += 1
        elif preferred_family == "totals":
            switched_to_totals += 1
        elif preferred_family == "1x2":
            switched_to_1x2 += 1

        by_target_family[preferred_family] = by_target_family.get(preferred_family, 0) + 1
        competition = str(row.get("competition_name") or "").strip() or "unknown"
        by_competition[competition] = by_competition.get(competition, 0) + 1

        trust_score = preferred_candidate.get("family_trust_score")
        if trust_score is not None:
            trust_sum += float(trust_score)
            trust_count += 1

    if considered <= 0:
        return None

    return {
        "considered": considered,
        "switched": switched,
        "derived_to_core": derived_to_core,
        "switched_to_btts": switched_to_btts,
        "switched_to_totals": switched_to_totals,
        "switched_to_1x2": switched_to_1x2,
        "avg_target_trust": round(trust_sum / trust_count, 3) if trust_count else None,
        "top_competitions": sorted(by_competition.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5],
        "top_target_families": sorted(by_target_family.items(), key=lambda item: (item[1], item[0]), reverse=True),
    }


def _print_top_pick_core_preference_impact(summary: Optional[dict]) -> None:
    print("\nTop-Pick Core Preference Impact")
    if summary is None:
        print("  no evaluable rows")
        return

    print(f"  considered rows: {summary['considered']}")
    print(f"  top pick changed: {summary['switched']}")
    print(f"  derived->core switches: {summary['derived_to_core']}")
    print(
        f"  target families: BTTS {summary['switched_to_btts']}  "
        f"Totals {summary['switched_to_totals']}  "
        f"1X2 {summary['switched_to_1x2']}"
    )
    if summary["avg_target_trust"] is not None:
        print(f"  avg trust of chosen family on switched rows: {summary['avg_target_trust']:+.3f}")
    if summary["top_target_families"]:
        family_text = ", ".join(f"{family}={count}" for family, count in summary["top_target_families"])
        print(f"  switched target families by volume: {family_text}")
    if summary["top_competitions"]:
        comp_text = ", ".join(f"{competition}={count}" for competition, count in summary["top_competitions"])
        print(f"  top competitions: {comp_text}")


def _print_prediction_clv(rows: list[dict]) -> None:
    entries = [entry for row in rows if (entry := _extract_prediction_clv_entry(row)) is not None]
    summary = _summarize_prediction_clv(entries)
    value_entries = [entry for row in rows for entry in _extract_value_candidate_clv_entries(row)]
    value_summary = _summarize_prediction_clv(value_entries)
    top_pick_family_segments = _segment_prediction_clv(entries, key_name="family", minimum_count=1)
    top_pick_competition_segments = _segment_prediction_clv(entries, key_name="competition_name", minimum_count=1)
    top_pick_policy_flag_segments = _segment_prediction_clv_by_policy_flag(entries, minimum_count=1)
    top_pick_competition_policy_flag_segments = _segment_prediction_clv_by_competition_policy_flag(entries, minimum_count=1)
    value_family_segments = _segment_prediction_clv(value_entries, key_name="family", minimum_count=1)
    value_competition_segments = _segment_prediction_clv(value_entries, key_name="competition_name", minimum_count=1)

    print("\nTop-Pick CLV (entry vs live close)")
    if summary is None:
        print("  no evaluable picks with entry odds and linked close line")
    else:
        family_counts = ", ".join(
            f"{family}={count}"
            for family, count in sorted(summary["family_counts"].items())
        )
        print(f"  evaluated picks: {summary['count']}")
        print(
            f"  beat close: {summary['beat_close']} / {summary['count']} "
            f"({summary['beat_close_rate']:.1f}%)"
        )
        print(f"  flat close: {summary['flat_close']}")
        print(f"  missed close: {summary['miss_close']}")
        print(f"  avg close-entry odds delta: {summary['avg_entry_to_close_delta']:+.4f}")
        print(f"  avg abs close-entry drift: {summary['avg_abs_entry_to_close_delta']:.4f}")
        print(f"  avg implied probability delta: {summary['avg_implied_prob_delta_pct']:+.4f}pp")
        if summary["avg_open_to_close_delta"] is not None:
            print(f"  avg open-close odds delta: {summary['avg_open_to_close_delta']:+.4f}")
        if summary["settled_count"]:
            print(
                f"  outcomes (flat 1u): "
                f"W {summary['wins']} / L {summary['losses']} / P {summary['pushes']}"
            )
            if summary["hit_rate"] is not None:
                print(f"  hit rate (push-excluded): {summary['hit_rate']:.1f}%")
            if summary["roi_pct"] is not None:
                print(f"  ROI (flat 1u): {summary['roi_pct']:+.2f}%")
        print(f"  market families: {family_counts}")

    print("\nValue-Candidate CLV (entry vs live close)")
    if value_summary is None:
        print("  no evaluable value candidates with linked close line")
    else:
        family_counts = ", ".join(
            f"{family}={count}"
            for family, count in sorted(value_summary["family_counts"].items())
        )
        print(f"  evaluated candidates: {value_summary['count']}")
        print(
            f"  beat close: {value_summary['beat_close']} / {value_summary['count']} "
            f"({value_summary['beat_close_rate']:.1f}%)"
        )
        print(f"  flat close: {value_summary['flat_close']}")
        print(f"  missed close: {value_summary['miss_close']}")
        print(f"  avg close-entry odds delta: {value_summary['avg_entry_to_close_delta']:+.4f}")
        print(f"  avg abs close-entry drift: {value_summary['avg_abs_entry_to_close_delta']:.4f}")
        print(f"  avg implied probability delta: {value_summary['avg_implied_prob_delta_pct']:+.4f}pp")
        if value_summary["avg_open_to_close_delta"] is not None:
            print(f"  avg open-close odds delta: {value_summary['avg_open_to_close_delta']:+.4f}")
        if value_summary["settled_count"]:
            print(
                f"  outcomes (flat 1u): "
                f"W {value_summary['wins']} / L {value_summary['losses']} / P {value_summary['pushes']}"
            )
            if value_summary["hit_rate"] is not None:
                print(f"  hit rate (push-excluded): {value_summary['hit_rate']:.1f}%")
            if value_summary["roi_pct"] is not None:
                print(f"  ROI (flat 1u): {value_summary['roi_pct']:+.2f}%")
        print(f"  market families: {family_counts}")

    drift = _summarize_live_market_drift(rows)
    print("\nLive Market Drift (OPEN->CLOSE)")
    if not drift:
        print("  no linked OPEN/CLOSE live market pairs")
    else:
        for label, metrics in drift.items():
            print(
                f"  {label:<10} samples={metrics['samples']:<3}  "
                f"avg={metrics['avg_delta']:+.4f}  "
                f"avg_abs={metrics['avg_abs_delta']:.4f}"
            )

    _print_segmented_clv("Top-Pick CLV by Market Family", top_pick_family_segments)
    _print_segmented_clv("Top-Pick CLV by Competition", top_pick_competition_segments)
    _print_segmented_clv("Top-Pick CLV by Policy Flag", top_pick_policy_flag_segments)
    _print_policy_flag_diagnostics("Top-Pick Policy Diagnostics (CLV + ROI)", top_pick_policy_flag_segments)
    _print_competition_policy_flag_diagnostics(
        "Top-Pick Competition + Policy Diagnostics (CLV + ROI)",
        top_pick_competition_policy_flag_segments,
    )
    _print_segmented_clv("Value-Candidate CLV by Market Family", value_family_segments)
    _print_segmented_clv("Value-Candidate CLV by Competition", value_competition_segments)


def _brier_1x2(
    rows: list[dict],
    p1_getter: Callable[[dict], Optional[float]],
    pd_getter: Callable[[dict], Optional[float]],
    p2_getter: Callable[[dict], Optional[float]],
) -> tuple[Optional[float], int]:
    total = 0.0
    count = 0
    outcome_map = {
        "W1": (1.0, 0.0, 0.0),
        "DRAW": (0.0, 1.0, 0.0),
        "W2": (0.0, 0.0, 1.0),
    }
    for row in rows:
        actual = row.get("actual_result")
        if actual not in outcome_map:
            continue
        p1 = p1_getter(row)
        pd = pd_getter(row)
        p2 = p2_getter(row)
        if None in (p1, pd, p2):
            continue
        o1, od, o2 = outcome_map[actual]
        total += (((p1 / 100.0) - o1) ** 2 + ((pd / 100.0) - od) ** 2 + ((p2 / 100.0) - o2) ** 2) / 3.0
        count += 1
    if count == 0:
        return None, 0
    return total / count, count


def _brier_binary(
    rows: list[dict],
    prob_getter: Callable[[dict], Optional[float]],
    outcome_getter: Callable[[dict], Optional[float]],
) -> tuple[Optional[float], int]:
    total = 0.0
    count = 0
    for row in rows:
        prob = prob_getter(row)
        outcome = outcome_getter(row)
        if prob is None or outcome is None:
            continue
        total += ((prob / 100.0) - outcome) ** 2
        count += 1
    if count == 0:
        return None, 0
    return total / count, count


def _bucket_label(probability: float, *, step: int = 5) -> str:
    lower = int(probability // step) * step
    upper = lower + step
    return f"{lower:02d}-{upper:02d}"


def _calibration_buckets_binary(
    rows: list[dict],
    prob_getter: Callable[[dict], Optional[float]],
    outcome_getter: Callable[[dict], Optional[float]],
    *,
    step: int = 5,
    minimum_probability: float = 40.0,
) -> list[dict]:
    buckets: dict[str, dict[str, float]] = {}
    for row in rows:
        probability = prob_getter(row)
        outcome = outcome_getter(row)
        if probability is None or outcome is None or probability < minimum_probability:
            continue
        label = _bucket_label(probability, step=step)
        bucket = buckets.setdefault(label, {"count": 0.0, "prob_sum": 0.0, "hit_sum": 0.0})
        bucket["count"] += 1.0
        bucket["prob_sum"] += probability
        bucket["hit_sum"] += outcome

    results: list[dict] = []
    for label in sorted(buckets):
        bucket = buckets[label]
        count = int(bucket["count"])
        avg_prob = bucket["prob_sum"] / bucket["count"]
        hit_rate = (bucket["hit_sum"] / bucket["count"]) * 100.0
        results.append(
            {
                "bucket": label,
                "count": count,
                "avg_prob": round(avg_prob, 2),
                "hit_rate": round(hit_rate, 2),
                "gap": round(hit_rate - avg_prob, 2),
            }
        )
    return results


def _calibration_buckets_1x2_top_pick(
    rows: list[dict],
    p1_getter: Callable[[dict], Optional[float]],
    pd_getter: Callable[[dict], Optional[float]],
    p2_getter: Callable[[dict], Optional[float]],
    *,
    step: int = 5,
    minimum_probability: float = 40.0,
) -> list[dict]:
    def _top_pick_prob(row: dict) -> Optional[float]:
        p1 = p1_getter(row)
        pd = pd_getter(row)
        p2 = p2_getter(row)
        if None in (p1, pd, p2):
            return None
        return max(float(p1), float(pd), float(p2))

    def _top_pick_hit(row: dict) -> Optional[float]:
        p1 = p1_getter(row)
        pd = pd_getter(row)
        p2 = p2_getter(row)
        actual = row.get("actual_result")
        if None in (p1, pd, p2) or actual not in {"W1", "DRAW", "W2"}:
            return None
        top_label, _ = max(
            [("W1", float(p1)), ("DRAW", float(pd)), ("W2", float(p2))],
            key=lambda item: item[1],
        )
        return 1.0 if top_label == actual else 0.0

    return _calibration_buckets_binary(
        rows,
        _top_pick_prob,
        _top_pick_hit,
        step=step,
        minimum_probability=minimum_probability,
    )


def _print_calibration(title: str, buckets: list[dict]) -> None:
    print(f"\n{title}")
    if not buckets:
        print("  no buckets")
        return
    for bucket in buckets:
        print(
            "  "
            f"{bucket['bucket']}  "
            f"n={bucket['count']:<3}  "
            f"pred={bucket['avg_prob']:>5.1f}%  "
            f"hit={bucket['hit_rate']:>5.1f}%  "
            f"gap={bucket['gap']:>+5.1f}pp"
        )


def _outcome_over_2_5(row: dict) -> Optional[float]:
    if row.get("actual_goals_home") is None or row.get("actual_goals_away") is None:
        return None
    return 1.0 if (row["actual_goals_home"] + row["actual_goals_away"]) > 2 else 0.0


def _outcome_btts(row: dict) -> Optional[float]:
    if row.get("actual_goals_home") is None or row.get("actual_goals_away") is None:
        return None
    return 1.0 if (row["actual_goals_home"] > 0 and row["actual_goals_away"] > 0) else 0.0


def _snapshot_from_row(row: dict) -> Optional[PredictionFeatureSnapshot]:
    if row.get("data_completeness") is None:
        return None
    try:
        return PredictionFeatureSnapshot.model_validate(row)
    except Exception:
        return None


def _blended_getter(row: dict, key: str) -> Optional[float]:
    snapshot = _snapshot_from_row(row)
    if snapshot is None:
        return None
    blended = infer_blended_probabilities(snapshot)
    if blended is None:
        return None
    return blended.get(key)


def _numeric_getter(row: dict, key: str, *, recalibrated: bool = False) -> Optional[float]:
    cache_key = "_numeric_baseline_recalibrated" if recalibrated else "_numeric_baseline_raw"
    cached = row.get(cache_key)
    if cached is None:
        snapshot = _snapshot_from_row(row)
        if snapshot is None:
            return None
        cached = build_numeric_baseline(
            MatchData(team1=row["team1"], team2=row["team2"]),
            snapshot,
            recalibration_profiles=row.get("_recalibration_profiles") if recalibrated else None,
        )
        row[cache_key] = cached
    return cached.get(key)


def _candidate_report_from_row(row: dict) -> Optional[dict]:
    baseline = row.get("_numeric_baseline_recalibrated")
    if baseline is None:
        _ = _numeric_getter(row, "prob_w1", recalibrated=True)
        baseline = row.get("_numeric_baseline_recalibrated")
    if baseline is None:
        return None
    report = {
        "prob_w1": baseline.get("prob_w1"),
        "prob_draw": baseline.get("prob_draw"),
        "prob_w2": baseline.get("prob_w2"),
        "prob_over_2_5": baseline.get("prob_over_2_5"),
        "prob_under_2_5": baseline.get("prob_under_2_5"),
        "prob_btts_yes": baseline.get("prob_btts_yes"),
        "prob_btts_no": baseline.get("prob_btts_no"),
        "expected_goals": baseline.get("expected_goals"),
    }
    report.update(derive_expanded_market_probabilities(report))
    return report


def _best_family_candidate(candidates: list[dict], family: str) -> Optional[dict]:
    family_candidates = [candidate for candidate in candidates if str(candidate.get("family") or "") == family]
    if not family_candidates:
        return None
    family_candidates.sort(
        key=lambda candidate: (
            float(candidate.get("score") or 0.0),
            float(candidate.get("edge") or -999.0),
            float(candidate.get("probability") or 0.0),
        ),
        reverse=True,
    )
    return family_candidates[0]


def _trainable_getter(row: dict, key: str, model) -> Optional[float]:
    if model is None:
        return None
    cache_key = "_trainable_numeric_prediction"
    cached = row.get(cache_key)
    if cached is None:
        cached = predict_trainable_numeric_model(model, row)
        row[cache_key] = cached
    if cached is None:
        return None
    return cached.get(key)


def _hybrid_getter(row: dict, key: str, model, alpha: float) -> Optional[float]:
    bounded_alpha = max(0.0, min(1.0, float(alpha)))
    recal = _numeric_getter(row, key, recalibrated=True)
    if recal is None:
        return None
    if bounded_alpha <= 0.0 or model is None:
        return recal

    if key in {"prob_w1", "prob_draw", "prob_w2"}:
        cache_key = f"_hybrid_triplet_{bounded_alpha:.2f}"
        cached = row.get(cache_key)
        if cached is None:
            recal_triplet = [
                _numeric_getter(row, "prob_w1", recalibrated=True),
                _numeric_getter(row, "prob_draw", recalibrated=True),
                _numeric_getter(row, "prob_w2", recalibrated=True),
            ]
            train_triplet = [
                _trainable_getter(row, "prob_w1", model),
                _trainable_getter(row, "prob_draw", model),
                _trainable_getter(row, "prob_w2", model),
            ]
            if any(value is None for value in recal_triplet + train_triplet):
                return None
            blended = [
                ((1.0 - bounded_alpha) * float(recal_value)) + (bounded_alpha * float(train_value))
                for recal_value, train_value in zip(recal_triplet, train_triplet)
            ]
            total = sum(blended)
            if total <= 0.0:
                return None
            cached = {
                "prob_w1": round((blended[0] * 100.0) / total, 4),
                "prob_draw": round((blended[1] * 100.0) / total, 4),
                "prob_w2": round((blended[2] * 100.0) / total, 4),
            }
            row[cache_key] = cached
        return cached.get(key) if cached is not None else None

    train_value = _trainable_getter(row, key, model)
    if train_value is None:
        return None
    return round(((1.0 - bounded_alpha) * float(recal)) + (bounded_alpha * float(train_value)), 4)


def _hybrid_alpha_cap(market_key: str, *, train_rows: int, validate_rows: int) -> float:
    base_caps = {
        "1X2": 0.40,
        "prob_over_2_5": 0.25,
        "prob_btts_yes": 0.15,
    }
    base_cap = base_caps.get(market_key, 0.20)
    if train_rows <= 0 or validate_rows <= 0:
        return 0.0
    sample_scale = min(1.0, float(train_rows) / 180.0, float(validate_rows) / 45.0)
    return round(base_cap * max(0.0, sample_scale), 2)


def _fit_best_alpha_1x2(validate_rows: list[dict], model, *, train_rows: int = 0) -> float:
    if model is None:
        return 0.0

    cap = _hybrid_alpha_cap("1X2", train_rows=train_rows, validate_rows=len(validate_rows))
    max_step = int((cap + 1e-9) / 0.05)
    best_alpha = 0.0
    best_score: Optional[float] = None
    for step in range(max_step + 1):
        alpha = step / 20.0
        score, count = _brier_1x2(
            validate_rows,
            lambda row: _hybrid_getter(row, "prob_w1", model, alpha),
            lambda row: _hybrid_getter(row, "prob_draw", model, alpha),
            lambda row: _hybrid_getter(row, "prob_w2", model, alpha),
        )
        if score is None or count <= 0:
            continue
        if best_score is None or score < (best_score - 1e-9) or (abs(score - best_score) <= 1e-9 and alpha < best_alpha):
            best_alpha = alpha
            best_score = score
    return round(best_alpha, 2)


def _fit_best_alpha_binary(
    validate_rows: list[dict],
    key: str,
    outcome_getter: Callable[[dict], Optional[float]],
    model,
    *,
    train_rows: int = 0,
) -> float:
    if model is None:
        return 0.0

    cap = _hybrid_alpha_cap(key, train_rows=train_rows, validate_rows=len(validate_rows))
    max_step = int((cap + 1e-9) / 0.05)
    best_alpha = 0.0
    best_score: Optional[float] = None
    for step in range(max_step + 1):
        alpha = step / 20.0
        score, count = _brier_binary(
            validate_rows,
            lambda row: _hybrid_getter(row, key, model, alpha),
            outcome_getter,
        )
        if score is None or count <= 0:
            continue
        if best_score is None or score < (best_score - 1e-9) or (abs(score - best_score) <= 1e-9 and alpha < best_alpha):
            best_alpha = alpha
            best_score = score
    return round(best_alpha, 2)


async def _attach_recalibration_profiles(rows: list[dict]) -> None:
    if not rows:
        return
    competition_cache: dict[Optional[str], list[dict]] = {}
    competition_names = {row.get("competition_name") for row in rows if row.get("data_completeness") is not None}
    for competition_name in competition_names:
        competition_cache[competition_name] = await db.get_numeric_recalibration_profiles(competition_name)
    for row in rows:
        row["_recalibration_profiles"] = competition_cache.get(row.get("competition_name"), [])


async def _attach_family_quality_profiles(rows: list[dict]) -> None:
    if not rows:
        return
    competition_cache: dict[Optional[str], list[dict]] = {}
    competition_names = {row.get("competition_name") for row in rows if row.get("data_completeness") is not None}
    for competition_name in competition_names:
        competition_cache[competition_name] = await db.get_market_family_quality_profiles(competition_name)
    for row in rows:
        row["_family_quality_profiles"] = competition_cache.get(row.get("competition_name"), [])


def _print_dataset_header(title: str, rows: list[dict]) -> None:
    print(f"\n=== {title} ===")
    print(f"Rows: {len(rows)}")
    snapshot_rows = [row for row in rows if row.get("data_completeness") is not None]
    print(f"Rows with feature snapshot: {len(snapshot_rows)}")


def _print_prediction_metrics(rows: list[dict]) -> None:
    if not rows:
        print("No resolved predictions available for this dataset.")
        return

    print("\n1X2 Brier")
    for name, getters in [
        ("Stored bot", (lambda row: row.get("prob_w1"), lambda row: row.get("prob_draw"), lambda row: row.get("prob_w2"))),
        ("Raw LLM", (lambda row: row.get("llm_prob_w1"), lambda row: row.get("llm_prob_draw"), lambda row: row.get("llm_prob_w2"))),
        ("Market prior", (lambda row: row.get("market_prob_w1"), lambda row: row.get("market_prob_draw"), lambda row: row.get("market_prob_w2"))),
        ("Stored numeric", (lambda row: row.get("model_prob_w1"), lambda row: row.get("model_prob_draw"), lambda row: row.get("model_prob_w2"))),
        ("Raw numeric", (lambda row: _numeric_getter(row, "prob_w1"), lambda row: _numeric_getter(row, "prob_draw"), lambda row: _numeric_getter(row, "prob_w2"))),
        ("Recalibrated", (lambda row: _numeric_getter(row, "prob_w1", recalibrated=True), lambda row: _numeric_getter(row, "prob_draw", recalibrated=True), lambda row: _numeric_getter(row, "prob_w2", recalibrated=True))),
        ("Blended baseline", (lambda row: _blended_getter(row, "prob_w1"), lambda row: _blended_getter(row, "prob_draw"), lambda row: _blended_getter(row, "prob_w2"))),
    ]:
        metric, count = _brier_1x2(rows, *getters)
        print(_format_metric(name, metric, count))

    print("\nOver 2.5 Brier")
    for name, getter in [
        ("Stored bot", lambda row: row.get("prob_over_2_5")),
        ("Raw LLM", lambda row: row.get("llm_prob_over_2_5")),
        ("Market prior", lambda row: row.get("market_prob_over_2_5")),
        ("Stored numeric", lambda row: row.get("model_prob_over_2_5")),
        ("Raw numeric", lambda row: _numeric_getter(row, "prob_over_2_5")),
        ("Recalibrated", lambda row: _numeric_getter(row, "prob_over_2_5", recalibrated=True)),
        ("Blended baseline", lambda row: _blended_getter(row, "prob_over_2_5")),
    ]:
        metric, count = _brier_binary(rows, getter, _outcome_over_2_5)
        print(_format_metric(name, metric, count))

    print("\nBTTS Yes Brier")
    for name, getter in [
        ("Stored bot", lambda row: row.get("prob_btts_yes")),
        ("Raw LLM", lambda row: row.get("llm_prob_btts_yes")),
        ("Market prior", lambda row: row.get("market_prob_btts_yes")),
        ("Stored numeric", lambda row: row.get("model_prob_btts_yes")),
        ("Raw numeric", lambda row: _numeric_getter(row, "prob_btts_yes")),
        ("Recalibrated", lambda row: _numeric_getter(row, "prob_btts_yes", recalibrated=True)),
        ("Blended baseline", lambda row: _blended_getter(row, "prob_btts_yes")),
    ]:
        metric, count = _brier_binary(rows, getter, _outcome_btts)
        print(_format_metric(name, metric, count))

    _print_calibration(
        "1X2 Top-Pick Calibration",
        _calibration_buckets_1x2_top_pick(
            rows,
            lambda row: row.get("prob_w1"),
            lambda row: row.get("prob_draw"),
            lambda row: row.get("prob_w2"),
        ),
    )
    _print_calibration(
        "Over 2.5 Calibration",
        _calibration_buckets_binary(rows, lambda row: row.get("prob_over_2_5"), _outcome_over_2_5),
    )


def _print_historical_metrics(rows: list[dict]) -> None:
    if not rows:
        print("No historical rows available for this dataset.")
        return

    print("\n1X2 Brier")
    for name, getters in [
        ("Market close", (lambda row: row.get("market_prob_w1"), lambda row: row.get("market_prob_draw"), lambda row: row.get("market_prob_w2"))),
        ("Raw numeric", (lambda row: _numeric_getter(row, "prob_w1"), lambda row: _numeric_getter(row, "prob_draw"), lambda row: _numeric_getter(row, "prob_w2"))),
        ("Recalibrated", (lambda row: _numeric_getter(row, "prob_w1", recalibrated=True), lambda row: _numeric_getter(row, "prob_draw", recalibrated=True), lambda row: _numeric_getter(row, "prob_w2", recalibrated=True))),
    ]:
        metric, count = _brier_1x2(rows, *getters)
        print(_format_metric(name, metric, count))

    print("\nOver 2.5 Brier")
    for name, getter in [
        ("Market close", lambda row: row.get("market_prob_over_2_5")),
        ("Raw numeric", lambda row: _numeric_getter(row, "prob_over_2_5")),
        ("Recalibrated", lambda row: _numeric_getter(row, "prob_over_2_5", recalibrated=True)),
    ]:
        metric, count = _brier_binary(rows, getter, _outcome_over_2_5)
        print(_format_metric(name, metric, count))

    print("\nBTTS Yes Brier")
    for name, getter in [
        ("Raw numeric", lambda row: _numeric_getter(row, "prob_btts_yes")),
        ("Recalibrated", lambda row: _numeric_getter(row, "prob_btts_yes", recalibrated=True)),
    ]:
        metric, count = _brier_binary(rows, getter, _outcome_btts)
        print(_format_metric(name, metric, count))

    _print_calibration(
        "1X2 Top-Pick Calibration (Market Close)",
        _calibration_buckets_1x2_top_pick(
            rows,
            lambda row: row.get("market_prob_w1"),
            lambda row: row.get("market_prob_draw"),
            lambda row: row.get("market_prob_w2"),
        ),
    )
    _print_calibration(
        "1X2 Top-Pick Calibration (Raw Numeric)",
        _calibration_buckets_1x2_top_pick(
            rows,
            lambda row: _numeric_getter(row, "prob_w1"),
            lambda row: _numeric_getter(row, "prob_draw"),
            lambda row: _numeric_getter(row, "prob_w2"),
        ),
    )
    _print_calibration(
        "1X2 Top-Pick Calibration (Recalibrated)",
        _calibration_buckets_1x2_top_pick(
            rows,
            lambda row: _numeric_getter(row, "prob_w1", recalibrated=True),
            lambda row: _numeric_getter(row, "prob_draw", recalibrated=True),
            lambda row: _numeric_getter(row, "prob_w2", recalibrated=True),
        ),
    )
    _print_calibration(
        "Over 2.5 Calibration (Market Close)",
        _calibration_buckets_binary(rows, lambda row: row.get("market_prob_over_2_5"), _outcome_over_2_5),
    )
    _print_calibration(
        "Over 2.5 Calibration (Raw Numeric)",
        _calibration_buckets_binary(rows, lambda row: _numeric_getter(row, "prob_over_2_5"), _outcome_over_2_5),
    )
    _print_calibration(
        "Over 2.5 Calibration (Recalibrated)",
        _calibration_buckets_binary(
            rows,
            lambda row: _numeric_getter(row, "prob_over_2_5", recalibrated=True),
            _outcome_over_2_5,
        ),
    )


def _print_time_split_backtest(rows: list[dict], *, dataset_kind: str) -> None:
    splits = _time_split_rows(rows)
    title = "Chronological Time Split"
    print(f"\n{title}")
    if not splits:
        print("  not enough chronological rows for train/validate/test split")
        return

    trainable_model = fit_trainable_numeric_model(splits[0]["rows"])
    hybrid_alpha_1x2 = 0.0
    hybrid_alpha_o25 = 0.0
    hybrid_alpha_btts = 0.0
    if trainable_model is None:
        print("  trainable model: not enough train rows with feature snapshots")
    else:
        print(f"  trainable model: fitted on {trainable_model.trained_rows} train rows")
        hybrid_alpha_1x2 = _fit_best_alpha_1x2(
            splits[1]["rows"],
            trainable_model,
            train_rows=trainable_model.trained_rows,
        )
        hybrid_alpha_o25 = _fit_best_alpha_binary(
            splits[1]["rows"],
            "prob_over_2_5",
            _outcome_over_2_5,
            trainable_model,
            train_rows=trainable_model.trained_rows,
        )
        hybrid_alpha_btts = _fit_best_alpha_binary(
            splits[1]["rows"],
            "prob_btts_yes",
            _outcome_btts,
            trainable_model,
            train_rows=trainable_model.trained_rows,
        )
        print(
            "  hybrid alphas (validate capped): "
            f"1X2={hybrid_alpha_1x2:.2f}, "
            f"O2.5={hybrid_alpha_o25:.2f}, "
            f"BTTS={hybrid_alpha_btts:.2f}"
        )

    if dataset_kind == "predictions":
        metric_specs = [
            (
                "1X2",
                (
                    "Stored",
                    lambda split_rows: _brier_1x2(
                        split_rows,
                        lambda row: row.get("prob_w1"),
                        lambda row: row.get("prob_draw"),
                        lambda row: row.get("prob_w2"),
                    ),
                ),
                (
                    "RawNum",
                    lambda split_rows: _brier_1x2(
                        split_rows,
                        lambda row: _numeric_getter(row, "prob_w1"),
                        lambda row: _numeric_getter(row, "prob_draw"),
                        lambda row: _numeric_getter(row, "prob_w2"),
                    ),
                ),
                  (
                      "Recal",
                      lambda split_rows: _brier_1x2(
                          split_rows,
                          lambda row: _numeric_getter(row, "prob_w1", recalibrated=True),
                          lambda row: _numeric_getter(row, "prob_draw", recalibrated=True),
                          lambda row: _numeric_getter(row, "prob_w2", recalibrated=True),
                      ),
                  ),
                  (
                      "TrainML",
                      lambda split_rows: _brier_1x2(
                          split_rows,
                          lambda row: _trainable_getter(row, "prob_w1", trainable_model),
                          lambda row: _trainable_getter(row, "prob_draw", trainable_model),
                          lambda row: _trainable_getter(row, "prob_w2", trainable_model),
                      ),
                  ),
                  (
                      "HybridML",
                      lambda split_rows: _brier_1x2(
                          split_rows,
                          lambda row: _hybrid_getter(row, "prob_w1", trainable_model, hybrid_alpha_1x2),
                          lambda row: _hybrid_getter(row, "prob_draw", trainable_model, hybrid_alpha_1x2),
                          lambda row: _hybrid_getter(row, "prob_w2", trainable_model, hybrid_alpha_1x2),
                      ),
                  ),
                  (
                      "Blend",
                      lambda split_rows: _brier_1x2(
                          split_rows,
                        lambda row: _blended_getter(row, "prob_w1"),
                        lambda row: _blended_getter(row, "prob_draw"),
                        lambda row: _blended_getter(row, "prob_w2"),
                    ),
                ),
            ),
            (
                "O2.5",
                (
                    "Stored",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: row.get("prob_over_2_5"),
                        _outcome_over_2_5,
                    ),
                ),
                (
                    "RawNum",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: _numeric_getter(row, "prob_over_2_5"),
                        _outcome_over_2_5,
                    ),
                ),
                  (
                      "Recal",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _numeric_getter(row, "prob_over_2_5", recalibrated=True),
                          _outcome_over_2_5,
                      ),
                  ),
                  (
                      "TrainML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _trainable_getter(row, "prob_over_2_5", trainable_model),
                          _outcome_over_2_5,
                      ),
                  ),
                  (
                      "HybridML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _hybrid_getter(row, "prob_over_2_5", trainable_model, hybrid_alpha_o25),
                          _outcome_over_2_5,
                      ),
                  ),
                  (
                      "Blend",
                      lambda split_rows: _brier_binary(
                          split_rows,
                        lambda row: _blended_getter(row, "prob_over_2_5"),
                        _outcome_over_2_5,
                    ),
                ),
            ),
            (
                "BTTS",
                (
                    "Stored",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: row.get("prob_btts_yes"),
                        _outcome_btts,
                    ),
                ),
                (
                    "RawNum",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: _numeric_getter(row, "prob_btts_yes"),
                        _outcome_btts,
                    ),
                ),
                  (
                      "Recal",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _numeric_getter(row, "prob_btts_yes", recalibrated=True),
                          _outcome_btts,
                      ),
                  ),
                  (
                      "TrainML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _trainable_getter(row, "prob_btts_yes", trainable_model),
                          _outcome_btts,
                      ),
                  ),
                  (
                      "HybridML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _hybrid_getter(row, "prob_btts_yes", trainable_model, hybrid_alpha_btts),
                          _outcome_btts,
                      ),
                  ),
                  (
                      "Blend",
                      lambda split_rows: _brier_binary(
                          split_rows,
                        lambda row: _blended_getter(row, "prob_btts_yes"),
                        _outcome_btts,
                    ),
                ),
            ),
        ]
    else:
        metric_specs = [
            (
                "1X2",
                (
                    "Market",
                    lambda split_rows: _brier_1x2(
                        split_rows,
                        lambda row: row.get("market_prob_w1"),
                        lambda row: row.get("market_prob_draw"),
                        lambda row: row.get("market_prob_w2"),
                    ),
                ),
                (
                    "RawNum",
                    lambda split_rows: _brier_1x2(
                        split_rows,
                        lambda row: _numeric_getter(row, "prob_w1"),
                        lambda row: _numeric_getter(row, "prob_draw"),
                        lambda row: _numeric_getter(row, "prob_w2"),
                    ),
                ),
                  (
                      "Recal",
                      lambda split_rows: _brier_1x2(
                          split_rows,
                          lambda row: _numeric_getter(row, "prob_w1", recalibrated=True),
                          lambda row: _numeric_getter(row, "prob_draw", recalibrated=True),
                          lambda row: _numeric_getter(row, "prob_w2", recalibrated=True),
                      ),
                  ),
                  (
                      "TrainML",
                      lambda split_rows: _brier_1x2(
                          split_rows,
                          lambda row: _trainable_getter(row, "prob_w1", trainable_model),
                          lambda row: _trainable_getter(row, "prob_draw", trainable_model),
                          lambda row: _trainable_getter(row, "prob_w2", trainable_model),
                      ),
                  ),
                  (
                      "HybridML",
                      lambda split_rows: _brier_1x2(
                          split_rows,
                          lambda row: _hybrid_getter(row, "prob_w1", trainable_model, hybrid_alpha_1x2),
                          lambda row: _hybrid_getter(row, "prob_draw", trainable_model, hybrid_alpha_1x2),
                          lambda row: _hybrid_getter(row, "prob_w2", trainable_model, hybrid_alpha_1x2),
                      ),
                  ),
              ),
              (
                  "O2.5",
                (
                    "Market",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: row.get("market_prob_over_2_5"),
                        _outcome_over_2_5,
                    ),
                ),
                (
                    "RawNum",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: _numeric_getter(row, "prob_over_2_5"),
                        _outcome_over_2_5,
                    ),
                ),
                  (
                      "Recal",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _numeric_getter(row, "prob_over_2_5", recalibrated=True),
                          _outcome_over_2_5,
                      ),
                  ),
                  (
                      "TrainML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _trainable_getter(row, "prob_over_2_5", trainable_model),
                          _outcome_over_2_5,
                      ),
                  ),
                  (
                      "HybridML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _hybrid_getter(row, "prob_over_2_5", trainable_model, hybrid_alpha_o25),
                          _outcome_over_2_5,
                      ),
                  ),
              ),
              (
                  "BTTS",
                (
                    "Market",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: row.get("market_prob_btts_yes"),
                        _outcome_btts,
                    ),
                ),
                (
                    "RawNum",
                    lambda split_rows: _brier_binary(
                        split_rows,
                        lambda row: _numeric_getter(row, "prob_btts_yes"),
                        _outcome_btts,
                    ),
                ),
                  (
                      "Recal",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _numeric_getter(row, "prob_btts_yes", recalibrated=True),
                          _outcome_btts,
                      ),
                  ),
                  (
                      "TrainML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _trainable_getter(row, "prob_btts_yes", trainable_model),
                          _outcome_btts,
                      ),
                  ),
                  (
                      "HybridML",
                      lambda split_rows: _brier_binary(
                          split_rows,
                          lambda row: _hybrid_getter(row, "prob_btts_yes", trainable_model, hybrid_alpha_btts),
                          _outcome_btts,
                      ),
                  ),
              ),
          ]

    for split in splits:
        print(
            f"  {split['label']:<8} rows={split['count']:<3} "
            f"period={split['date_from']} -> {split['date_to']}"
        )
        for metric_name, *models in metric_specs:
            parts = []
            for label, metric_fn in models:
                value, count = metric_fn(split["rows"])
                parts.append(f"{label} {_format_compact_metric(value, count)}")
            print(f"    {metric_name:<5} " + " | ".join(parts))


async def _run() -> int:
    args = _parse_args()
    await db.init_db()

    if args.dataset in {"predictions", "both"}:
        prediction_rows = await db.get_backtest_prediction_rows(limit=args.limit)
        prediction_clv_rows = await db.get_prediction_clv_rows(limit=args.limit)
        await _attach_recalibration_profiles(prediction_rows)
        _print_dataset_header("Resolved Predictions", prediction_rows)
        _print_prediction_metrics(prediction_rows)
        _print_time_split_backtest(prediction_rows, dataset_kind="predictions")
        _print_prediction_clv(prediction_clv_rows)

    if args.dataset in {"historical", "both"}:
        historical_rows = await db.get_historical_backtest_rows(limit=args.limit)
        await _attach_recalibration_profiles(historical_rows)
        global_family_profiles = [
            profile
            for profile in await db.get_market_family_quality_profiles()
            if str(profile.get("scope") or "").upper() == "GLOBAL"
        ]
        competition_family_profiles: list[dict] = []
        seen_competition_profiles: set[tuple[str, str]] = set()
        competition_names = sorted(
            {
                str(row.get("competition_name") or "").strip()
                for row in historical_rows
                if str(row.get("competition_name") or "").strip()
            }
        )
        for competition_name in competition_names:
            for profile in await db.get_market_family_quality_profiles(competition_name):
                if str(profile.get("scope") or "").upper() != "COMPETITION":
                    continue
                key = (
                    str(profile.get("competition_key") or "").strip(),
                    str(profile.get("family") or "").strip(),
                )
                if key in seen_competition_profiles:
                    continue
                seen_competition_profiles.add(key)
                competition_family_profiles.append(profile)
        _print_dataset_header("Historical Canonical Rows", historical_rows)
        _print_historical_metrics(historical_rows)
        _print_market_coverage(historical_rows, title="Historical Market Coverage")
        _print_market_family_trust("Global Market Family Trust", global_family_profiles)
        _print_market_family_trust("Competition Market Family Trust", competition_family_profiles)
        _print_family_gating_impact(_summarize_family_gating_impact(historical_rows, family="btts"))
        _print_action_core_preference_impact(_summarize_action_core_preference_impact(historical_rows))
        _print_top_pick_core_preference_impact(_summarize_top_pick_core_preference_impact(historical_rows))
        _print_time_split_backtest(historical_rows, dataset_kind="historical")

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
