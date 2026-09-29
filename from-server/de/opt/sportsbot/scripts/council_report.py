#!/usr/bin/env python3
"""Read-only accuracy report for independent and debated four-AI forecasts."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_council.core import COUNCIL_MEMBERS, grade_pick
from paper_council.store import connect_readonly, store_status


DEFAULT_DB_PATH = Path(
    os.getenv("COUNCIL_DB_PATH", "/var/lib/sportsbot-paper-council/council.sqlite3")
)
PROBABILITY_FIELDS = (
    "prob_w1",
    "prob_draw",
    "prob_w2",
    "prob_over_2_5",
    "prob_under_2_5",
)
STAGES = ("independent", "debated")


def load_report_rows(conn) -> dict[str, list[dict[str, Any]]]:
    """Load the full settled designated denominator plus any child forecasts."""
    eligible = [
        dict(row)
        for row in conn.execute(
            """
            SELECT r.run_id,r.event_id,r.team1,r.team2,r.kickoff_utc,r.as_of_utc,
                    r.horizon_hours,r.status AS run_status,r.schema_version,r.prompt_version,
                    r.experiment_version,r.policy_version,r.input_builder_version,r.model_roster_json,
                    r.data_completeness,r.evidence_count,
                    rs.status AS resolution_status,
                    m.home_goals,m.away_goals,m.actual_1x2,m.actual_totals_2_5
            FROM council_runs r
            LEFT JOIN council_match_results m ON m.event_id=r.event_id
            LEFT JOIN council_resolution_status rs ON rs.event_id=r.event_id
            WHERE r.is_designated=1
            ORDER BY r.kickoff_utc,r.run_id
            """
        ).fetchall()
    ]
    consensus = [
        dict(row)
        for row in conn.execute(
            """
            SELECT r.run_id,r.event_id,r.team1,r.team2,r.kickoff_utc,r.as_of_utc,
                    r.horizon_hours,r.status AS run_status,r.schema_version,r.prompt_version,
                    r.experiment_version,r.policy_version,r.input_builder_version,r.model_roster_json,
                   c.stage,c.status,c.market_code,c.selection_code,
                   c.prob_w1,c.prob_draw,c.prob_w2,
                   c.prob_over_2_5,c.prob_under_2_5,
                   c.agreement_count,c.agreement_ratio,
                   m.home_goals,m.away_goals,m.actual_1x2,m.actual_totals_2_5
            FROM council_runs r
            JOIN council_consensus c ON c.run_id=r.run_id
            LEFT JOIN council_match_results m ON m.event_id=r.event_id
            WHERE r.is_designated=1
            ORDER BY r.kickoff_utc,c.stage
            """
        ).fetchall()
    ]
    members = [
        dict(row)
        for row in conn.execute(
            """
            SELECT r.run_id,r.event_id,r.team1,r.team2,r.kickoff_utc,r.as_of_utc,
                    r.horizon_hours,r.status AS run_status,r.schema_version,r.prompt_version,
                    r.experiment_version,r.policy_version,r.input_builder_version,r.model_roster_json,
                   f.round_no,f.member,f.status,f.forecast_json,
                   m.home_goals,m.away_goals,m.actual_1x2,m.actual_totals_2_5
            FROM council_runs r
            JOIN council_member_forecasts f ON f.run_id=r.run_id
            LEFT JOIN council_match_results m ON m.event_id=r.event_id
            WHERE r.is_designated=1
            ORDER BY r.kickoff_utc,f.round_no,f.member
            """
        ).fetchall()
    ]
    return {"eligible": eligible, "consensus": consensus, "members": members}


def clipped_probability(value: float) -> float:
    return min(1.0 - 1e-12, max(1e-12, float(value) / 100.0))


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _score_components(row: dict[str, Any]) -> dict[str, float | int]:
    probs = {
        "TEAM1": clipped_probability(row["prob_w1"]),
        "DRAW": clipped_probability(row["prob_draw"]),
        "TEAM2": clipped_probability(row["prob_w2"]),
    }
    actual = str(row["actual_1x2"])
    predicted = sorted(probs, key=lambda key: (-probs[key], key))[0]
    over_prob = clipped_probability(row["prob_over_2_5"])
    actual_over = row["actual_totals_2_5"] == "OVER"
    return {
        "1x2_accuracy": int(predicted == actual),
        "1x2_multiclass_brier": sum(
            (probability - float(key == actual)) ** 2 for key, probability in probs.items()
        ),
        "1x2_log_loss": -math.log(probs[actual]),
        "totals_2_5_accuracy": int((over_prob >= 0.5) == actual_over),
        "totals_2_5_brier": (over_prob - float(actual_over)) ** 2,
        "totals_2_5_log_loss": -math.log(over_prob if actual_over else 1.0 - over_prob),
    }


def evaluate(
    forecasts: list[dict[str, Any]],
    *,
    eligible_n: int | None = None,
    settled_n: int | None = None,
    quorum_n: int | None = None,
) -> dict[str, Any]:
    """Score valid vectors conditionally while keeping coverage on the eligible denominator."""
    valid_n = len(forecasts)
    denominator = valid_n if eligible_n is None else int(eligible_n)
    if denominator < valid_n:
        raise ValueError("eligible_n cannot be smaller than valid_n")
    stage_quorum_n = valid_n if quorum_n is None else int(quorum_n)
    if not 0 <= stage_quorum_n <= denominator:
        raise ValueError("quorum_n must be between zero and eligible_n")

    resolved_n = valid_n if settled_n is None else int(settled_n)
    if not valid_n <= resolved_n <= denominator:
        raise ValueError("settled_n must be between valid_n and eligible_n")
    scored = [_score_components(row) for row in forecasts]
    picks: list[int] = []
    market_picks: dict[str, list[int]] = {"1X2": [], "TOTALS_2_5": []}
    changed = 0
    for row in forecasts:
        market = str(row.get("market_code") or "NO_BET")
        selection = str(row.get("selection_code") or "NO_BET")
        outcome = grade_pick(market, selection, int(row["home_goals"]), int(row["away_goals"]))
        if outcome in {"WIN", "LOSS"}:
            won = int(outcome == "WIN")
            picks.append(won)
            if market in market_picks:
                market_picks[market].append(won)
        changed += int(bool(row.get("changed_mind")))

    evidence_level = "no valid settled forecasts"
    if valid_n:
        evidence_level = "QA only"
    if valid_n >= 500:
        evidence_level = "large scoring sample"
    elif valid_n >= 200:
        evidence_level = "preliminary scoring sample"

    result: dict[str, Any] = {
        "n": valid_n,
        "eligible_n": denominator,
        "valid_n": valid_n,
        "valid_coverage": _ratio(valid_n, denominator),
        "settled_n": resolved_n,
        "resolution_coverage": _ratio(resolved_n, denominator),
        "valid_coverage_among_settled": _ratio(valid_n, resolved_n),
        "quorum_n": stage_quorum_n,
        "quorum_coverage": _ratio(stage_quorum_n, denominator),
        "evidence_level": evidence_level,
        "score_denominator": "valid_n (conditional on settled result and parseable probability vector)",
        "pick_coverage_denominator": "eligible_n",
        "pick_coverage": _ratio(len(picks), denominator),
        "pick_n": len(picks),
        "pick_hit_rate": round(sum(picks) / len(picks), 4) if picks else None,
        "pick_hit_rate_note": "descriptive only; use per-market rates for comparisons",
        "pick_metrics_by_market": {
            market: {
                "pick_n": len(values),
                "coverage": _ratio(len(values), denominator),
                "hit_rate": round(sum(values) / len(values), 4) if values else None,
            }
            for market, values in market_picks.items()
        },
        "changed_mind_n": changed,
        "changed_mind_rate": _ratio(changed, valid_n),
    }
    metric_names = (
        "1x2_accuracy",
        "1x2_multiclass_brier",
        "1x2_log_loss",
        "totals_2_5_accuracy",
        "totals_2_5_brier",
        "totals_2_5_log_loss",
    )
    for metric in metric_names:
        result[metric] = (
            round(sum(float(item[metric]) for item in scored) / valid_n, 5)
            if valid_n
            else None
        )
    for metric in ("1x2_accuracy", "totals_2_5_accuracy"):
        if result[metric] is not None:
            result[metric] = round(float(result[metric]), 4)
    return result


def _valid_probability_vector(row: dict[str, Any]) -> bool:
    try:
        values = [float(row[key]) for key in PROBABILITY_FIELDS]
    except (KeyError, TypeError, ValueError):
        return False
    if any(not math.isfinite(value) or not 0.0 <= value <= 100.0 for value in values):
        return False
    return abs(sum(values[:3]) - 100.0) <= 0.05 and abs(sum(values[3:]) - 100.0) <= 0.05


def _is_settled(row: dict[str, Any]) -> bool:
    return all(
        row.get(key) is not None
        for key in ("home_goals", "away_goals", "actual_1x2", "actual_totals_2_5")
    )


def consensus_forecast(row: dict[str, Any]) -> dict[str, Any] | None:
    return dict(row) if _valid_probability_vector(row) and _is_settled(row) else None


def member_forecast(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("status") != "VALID" or not row.get("forecast_json") or not _is_settled(row):
        return None
    try:
        payload = json.loads(row["forecast_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not _valid_probability_vector(payload):
        return None
    payload.update(
        run_id=row.get("run_id"),
        event_id=row.get("event_id"),
        member=row.get("member"),
        round_no=row.get("round_no"),
        prompt_version=row.get("prompt_version"),
        horizon_hours=row.get("horizon_hours"),
        actual_1x2=row["actual_1x2"],
        actual_totals_2_5=row["actual_totals_2_5"],
        home_goals=row["home_goals"],
        away_goals=row["away_goals"],
    )
    return payload


def _run_key(row: dict[str, Any]) -> str:
    return str(row.get("run_id") or row.get("event_id") or "")


def _eligible_rows(rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    candidates = list(rows.get("eligible") or [])
    if not candidates:
        candidates = list(rows.get("consensus") or []) + list(rows.get("members") or [])
    deduplicated: dict[str, dict[str, Any]] = {}
    for row in candidates:
        key = _run_key(row)
        if key and key not in deduplicated:
            deduplicated[key] = dict(row)
    return list(deduplicated.values())


def _horizon_bucket(row: dict[str, Any]) -> str:
    try:
        horizon = round(float(row["horizon_hours"]), 3)
        if not math.isfinite(horizon):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return "unknown"
    if horizon < 12.0:
        return "06-12h"
    if horizon < 24.0:
        return "12-24h"
    return "24-40h"


def _protocol(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    schema_version = str(row.get("schema_version") or "unknown")
    prompt_version = str(row.get("prompt_version") or "unknown")
    experiment_version = str(row.get("experiment_version") or "unknown")
    policy_version = str(row.get("policy_version") or "unknown")
    input_builder_version = str(row.get("input_builder_version") or "unknown")
    try:
        roster = json.loads(row.get("model_roster_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        roster = {"invalid": True}
    roster_json = json.dumps(roster, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    key = (
        f"experiment={experiment_version}|schema={schema_version}|prompt={prompt_version}"
        f"|policy={policy_version}|input={input_builder_version}|models={roster_json}"
    )
    return key, {
        "experiment_version": experiment_version,
        "schema_version": schema_version,
        "prompt_version": prompt_version,
        "policy_version": policy_version,
        "input_builder_version": input_builder_version,
        "model_roster": roster,
        "pre_registered_horizon": "6-40h",
    }


def _subset_rows(
    rows: dict[str, list[dict[str, Any]]], run_ids: set[str]
) -> dict[str, list[dict[str, Any]]]:
    return {
        key: [row for row in rows.get(key, []) if _run_key(row) in run_ids]
        for key in ("eligible", "consensus", "members")
    }


def _quorum_runs(consensus_rows: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    result = {stage: set() for stage in STAGES}
    for row in consensus_rows:
        stage = str(row.get("stage") or "")
        if (
            stage in result
            and row.get("status") in {"OK", "NO_BET"}
            and _valid_probability_vector(row)
        ):
            result[stage].add(_run_key(row))
    return result


def _coverage(
    rows: dict[str, list[dict[str, Any]]], eligible: list[dict[str, Any]]
) -> dict[str, Any]:
    eligible_n = len(eligible)
    settled_runs = {
        _run_key(row)
        for row in [*eligible, *rows.get("consensus", []), *rows.get("members", [])]
        if _is_settled(row)
    }
    settled_n = len(settled_runs)
    quorum = _quorum_runs(rows.get("consensus", []))
    return {
        "eligible_n": eligible_n,
        "settled_n": settled_n,
        "resolution_coverage": _ratio(settled_n, eligible_n),
        "unresolved_expired_n": sum(
            1 for row in eligible if row.get("resolution_status") == "UNRESOLVED_EXPIRED"
        ),
        "independent_quorum_n": len(quorum["independent"]),
        "independent_quorum_coverage": _ratio(len(quorum["independent"]), eligible_n),
        "debated_quorum_n": len(quorum["debated"]),
        "debated_quorum_coverage": _ratio(len(quorum["debated"]), eligible_n),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 5) if values else None


def _member_dispersion(items: dict[str, dict[str, Any]]) -> dict[str, float] | None:
    if set(items) != set(COUNCIL_MEMBERS):
        return None
    values = list(items.values())
    one_x_two = statistics.mean(
        statistics.pstdev(float(item[field]) for item in values)
        for field in ("prob_w1", "prob_draw", "prob_w2")
    )
    totals = statistics.pstdev(float(item["prob_over_2_5"]) for item in values)
    return {
        "1x2_component_mean_stddev_pp": float(one_x_two),
        "totals_over_stddev_pp": float(totals),
        "combined_stddev_pp": float(statistics.mean((one_x_two, totals))),
    }


def _debate_effect(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    consensus_by_run: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows.get("consensus", []):
        parsed = consensus_forecast(row)
        stage = str(row.get("stage") or "")
        if parsed is not None and stage in STAGES:
            consensus_by_run[_run_key(row)][stage] = parsed

    score_deltas: dict[str, list[float]] = defaultdict(list)
    paired_consensus_n = 0
    for stages in consensus_by_run.values():
        if set(stages) != set(STAGES):
            continue
        paired_consensus_n += 1
        initial = _score_components(stages["independent"])
        debated = _score_components(stages["debated"])
        for metric in (
            "1x2_multiclass_brier",
            "1x2_log_loss",
            "totals_2_5_brier",
            "totals_2_5_log_loss",
        ):
            score_deltas[metric].append(float(debated[metric]) - float(initial[metric]))

    member_by_run_round: dict[str, dict[int, dict[str, dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows.get("members", []):
        parsed = member_forecast(row)
        if parsed is None:
            continue
        member_by_run_round[_run_key(row)][int(row["round_no"])][str(row["member"])] = parsed

    round1_dispersion: dict[str, list[float]] = defaultdict(list)
    round2_dispersion: dict[str, list[float]] = defaultdict(list)
    dispersion_delta: dict[str, list[float]] = defaultdict(list)
    paired_dispersion_n = 0
    for rounds in member_by_run_round.values():
        first = _member_dispersion(rounds.get(1, {}))
        second = _member_dispersion(rounds.get(2, {}))
        if first is None or second is None:
            continue
        paired_dispersion_n += 1
        for metric in first:
            round1_dispersion[metric].append(first[metric])
            round2_dispersion[metric].append(second[metric])
            dispersion_delta[metric].append(second[metric] - first[metric])

    return {
        "paired_n": paired_consensus_n,
        "paired_score_delta_debated_minus_independent": {
            metric: _mean(values) for metric, values in sorted(score_deltas.items())
        },
        "proper_score_delta_note": "negative means the debated probability vector improved",
        "paired_dispersion_n": paired_dispersion_n,
        "dispersion_pp": {
            "independent": {
                metric: _mean(values) for metric, values in sorted(round1_dispersion.items())
            },
            "debated": {
                metric: _mean(values) for metric, values in sorted(round2_dispersion.items())
            },
            "delta_debated_minus_independent": {
                metric: _mean(values) for metric, values in sorted(dispersion_delta.items())
            },
        },
        "dispersion_note": (
            "negative delta means convergence; convergence alone is not accuracy gain"
        ),
    }


def _metrics_for_rows(
    rows: dict[str, list[dict[str, Any]]], eligible: list[dict[str, Any]]
) -> dict[str, Any]:
    eligible_n = len(eligible)
    settled_runs = {
        _run_key(row)
        for row in [*eligible, *rows.get("consensus", []), *rows.get("members", [])]
        if _is_settled(row)
    }
    settled_n = len(settled_runs)
    quorum = _quorum_runs(rows.get("consensus", []))

    consensus_groups: dict[str, list[dict[str, Any]]] = {stage: [] for stage in STAGES}
    for row in rows.get("consensus", []):
        parsed = consensus_forecast(row)
        stage = str(row.get("stage") or "")
        if parsed is not None and stage in consensus_groups:
            consensus_groups[stage].append(parsed)

    parsed_members: list[dict[str, Any]] = []
    initial_picks: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows.get("members", []):
        parsed = member_forecast(row)
        if parsed is None:
            continue
        parsed_members.append(parsed)
        if int(row["round_no"]) == 1:
            initial_picks[(_run_key(row), str(row["member"]))] = (
                str(parsed.get("market_code")),
                str(parsed.get("selection_code")),
            )
    for parsed in parsed_members:
        if int(parsed.get("round_no") or 0) == 2:
            initial = initial_picks.get((_run_key(parsed), str(parsed.get("member"))))
            current = (str(parsed.get("market_code")), str(parsed.get("selection_code")))
            parsed["changed_mind"] = initial is not None and initial != current

    observed_members = {
        str(item.get("member")) for item in parsed_members if item.get("member")
    }
    all_members = sorted(set(COUNCIL_MEMBERS) | observed_members)
    member_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for parsed in parsed_members:
        stage = "independent" if int(parsed["round_no"]) == 1 else "debated"
        member_groups[f"{parsed['member']}:{stage}"].append(parsed)

    consensus_metrics = {
        stage: evaluate(
            consensus_groups[stage],
            eligible_n=eligible_n,
            settled_n=settled_n,
            quorum_n=len(quorum[stage]),
        )
        for stage in STAGES
    }
    member_metrics: dict[str, dict[str, Any]] = {}
    member_quorum: dict[str, set[str]] = defaultdict(set)
    for row in rows.get("members", []):
        if row.get("status") != "VALID" or not row.get("forecast_json"):
            continue
        try:
            payload = json.loads(row["forecast_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and _valid_probability_vector(payload):
            stage = "independent" if int(row.get("round_no") or 0) == 1 else "debated"
            member_quorum[f"{row.get('member')}:{stage}"].add(_run_key(row))
    for member in all_members:
        for stage in STAGES:
            key = f"{member}:{stage}"
            member_metrics[key] = evaluate(
                member_groups.get(key, []),
                eligible_n=eligible_n,
                settled_n=settled_n,
                quorum_n=len(member_quorum.get(key, set())),
            )
    return {
        "coverage": _coverage(rows, eligible),
        "consensus": consensus_metrics,
        "members": member_metrics,
        "debate_effect": _debate_effect(rows),
    }


def build_report(rows: dict[str, list[dict[str, Any]]], status: dict[str, Any]) -> dict[str, Any]:
    eligible = _eligible_rows(rows)
    strata_runs: dict[str, set[str]] = defaultdict(set)
    strata_metadata: dict[str, dict[str, Any]] = {}
    for row in eligible:
        key, metadata = _protocol(row)
        strata_runs[key].add(_run_key(row))
        strata_metadata[key] = metadata

    stratified: dict[str, dict[str, Any]] = {}
    for key in sorted(strata_runs):
        subset = _subset_rows(rows, strata_runs[key])
        subset_eligible = _eligible_rows(subset)
        stratified[key] = {
            "protocol": strata_metadata[key],
            **_metrics_for_rows(subset, subset_eligible),
        }

    horizon_runs: dict[str, set[str]] = defaultdict(set)
    for row in eligible:
        protocol_key, _ = _protocol(row)
        combined_key = f"{protocol_key}|horizon={_horizon_bucket(row)}"
        horizon_runs[combined_key].add(_run_key(row))
    horizon_strata = {
        bucket: _metrics_for_rows(_subset_rows(rows, run_ids), _eligible_rows(_subset_rows(rows, run_ids)))
        for bucket, run_ids in sorted(horizon_runs.items())
    }

    mixed_protocols = len(strata_runs) > 1
    if not mixed_protocols:
        aggregate = _metrics_for_rows(rows, eligible)
        consensus = aggregate["consensus"]
        members = aggregate["members"]
        debate_effect = aggregate["debate_effect"]
        ranking_status = "WITHIN_SINGLE_PROTOCOL" if eligible else "NO_DATA"
    else:
        consensus = {}
        members = {}
        debate_effect = {
            "status": "SUPPRESSED_MIXED_PROTOCOLS",
            "reason": "paired scoring is emitted only inside exact schema/prompt-version strata",
        }
        ranking_status = "SUPPRESSED_MIXED_PROTOCOLS"

    return {
        "store": status,
        "primary_final": "consensus:debated",
        "ranking_status": ranking_status,
        "protocol_guard": {
            "mixed_protocols": mixed_protocols,
            "strata_n": len(strata_runs),
            "rule": "never aggregate across experiment/schema/prompt/policy/input/model versions; report 6-12/12-24/24-40h separately",
        },
        "accuracy_note": (
            "Proper scores are conditional on settled valid probability vectors; coverage starts from "
            "all prospectively designated runs, including failures and unresolved matches. ROI is omitted "
            "because stored odds are non-executable benchmarks."
        ),
        "coverage": _coverage(rows, eligible),
        "consensus": consensus,
        "members": members,
        "debate_effect": debate_effect,
        "strata": stratified,
        "horizon_strata": horizon_strata,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Four-AI council accuracy report")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    conn = connect_readonly(args.db)
    try:
        report = build_report(load_report_rows(conn), store_status(conn))
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
