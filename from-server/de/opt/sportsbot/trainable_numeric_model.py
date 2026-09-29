from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from models import PredictionFeatureSnapshot


FEATURE_NAMES = [
    "data_completeness",
    "is_team1_home",
    "is_neutral_venue",
    "market_prob_w1",
    "market_prob_draw",
    "market_prob_w2",
    "market_prob_over_2_5",
    "market_prob_btts_yes",
    "form_points_last5_diff",
    "form_goal_diff_last5_diff",
    "form_scored_avg_diff",
    "form_conceded_avg_diff",
    "h2h_team1_win_rate",
    "h2h_draw_rate",
    "h2h_avg_goals",
    "xg_diff",
    "xga_edge",
    "rating_diff",
    "possession_diff",
    "standings_point_gap",
    "standings_position_gap",
    "rest_gap_hours",
    "team1_short_rest",
    "team2_short_rest",
    "market_value_ratio_log2",
    "key_absence_diff",
    "shots_avg_diff",
    "shots_on_target_avg_diff",
    "corners_avg_diff",
    "shots_balance_diff",
    "shots_on_target_balance_diff",
    "corners_balance_diff",
]

BTTS_FEATURE_NAMES = (
    "data_completeness",
    "is_team1_home",
    "is_neutral_venue",
    "market_prob_draw",
    "market_prob_over_2_5",
    "market_prob_btts_yes",
    "form_points_last5_diff",
    "form_goal_diff_last5_diff",
    "form_scored_avg_diff",
    "form_conceded_avg_diff",
    "h2h_draw_rate",
    "h2h_avg_goals",
    "xg_diff",
    "xga_edge",
    "rest_gap_hours",
    "team1_short_rest",
    "team2_short_rest",
    "key_absence_diff",
    "shots_avg_diff",
    "shots_on_target_avg_diff",
    "corners_avg_diff",
    "shots_balance_diff",
    "shots_on_target_balance_diff",
    "corners_balance_diff",
)

BTTS_PROXY_FEATURE_NAMES = (
    "data_completeness",
    "market_prob_w1",
    "market_prob_draw",
    "market_prob_w2",
    "market_prob_over_2_5",
    "form_scored_avg_diff",
    "form_conceded_avg_diff",
    "h2h_avg_goals",
    "xg_diff",
    "xga_edge",
    "shots_on_target_avg_diff",
    "shots_on_target_balance_diff",
    "corners_balance_diff",
)


@dataclass(slots=True)
class TrainableNumericModel:
    feature_names: tuple[str, ...]
    means: np.ndarray
    scales: np.ndarray
    one_x_two_weights: Optional[np.ndarray]
    one_x_two_base_rates: np.ndarray
    over_weights: Optional[np.ndarray]
    over_base_rate: float
    btts_weights: Optional[np.ndarray]
    btts_base_rate: float
    btts_feature_indices: tuple[int, ...]
    btts_proxy_feature_indices: tuple[int, ...]
    btts_prior_proxy_weights: Optional[np.ndarray]
    trained_rows: int
    btts_trained_rows: int
    btts_market_rows: int
    blend_alpha: float
    btts_blend_alpha: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_log2(value: Optional[float]) -> float:
    if value is None or value <= 0:
        return 0.0
    return math.log(value, 2)


def _snapshot_from_row(row: dict) -> Optional[PredictionFeatureSnapshot]:
    if row.get("data_completeness") is None:
        return None
    try:
        return PredictionFeatureSnapshot.model_validate(row)
    except Exception:
        return None


def _float_or_default(value: Optional[float], default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


def _feature_vector(snapshot: PredictionFeatureSnapshot) -> np.ndarray:
    is_home = 1.0 if snapshot.is_team1_home is True else (-1.0 if snapshot.is_team1_home is False else 0.0)
    is_neutral = 1.0 if snapshot.is_neutral_venue is True else 0.0
    team1_short_rest = 1.0 if snapshot.team1_short_rest is True else 0.0
    team2_short_rest = 1.0 if snapshot.team2_short_rest is True else 0.0

    values = [
        _float_or_default(snapshot.data_completeness, 0.0),
        is_home,
        is_neutral,
        _float_or_default(snapshot.market_prob_w1, 33.3),
        _float_or_default(snapshot.market_prob_draw, 33.3),
        _float_or_default(snapshot.market_prob_w2, 33.3),
        _float_or_default(snapshot.market_prob_over_2_5, 50.0),
        _float_or_default(snapshot.market_prob_btts_yes, 50.0),
        _float_or_default(snapshot.form_points_last5_diff, 0.0),
        _float_or_default(snapshot.form_goal_diff_last5_diff, 0.0),
        _float_or_default(snapshot.form_scored_avg_diff, 0.0),
        _float_or_default(snapshot.form_conceded_avg_diff, 0.0),
        _float_or_default(snapshot.h2h_team1_win_rate, 50.0),
        _float_or_default(snapshot.h2h_draw_rate, 27.0),
        _float_or_default(snapshot.h2h_avg_goals, 2.6),
        _float_or_default(snapshot.xg_diff, 0.0),
        _float_or_default(snapshot.xga_edge, 0.0),
        _float_or_default(snapshot.rating_diff, 0.0),
        _float_or_default(snapshot.possession_diff, 0.0),
        _float_or_default(snapshot.standings_point_gap, 0.0),
        _float_or_default(snapshot.standings_position_gap, 0.0),
        _float_or_default(snapshot.rest_gap_hours, 0.0),
        team1_short_rest,
        team2_short_rest,
        _safe_log2(snapshot.market_value_ratio),
        _float_or_default(float(snapshot.key_absence_diff) if snapshot.key_absence_diff is not None else None, 0.0),
        _float_or_default(snapshot.shots_avg_diff, 0.0),
        _float_or_default(snapshot.shots_on_target_avg_diff, 0.0),
        _float_or_default(snapshot.corners_avg_diff, 0.0),
        _float_or_default(snapshot.shots_balance_diff, 0.0),
        _float_or_default(snapshot.shots_on_target_balance_diff, 0.0),
        _float_or_default(snapshot.corners_balance_diff, 0.0),
    ]
    return np.array(values, dtype=np.float64)


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=1, keepdims=True)
    exp_values = np.exp(np.clip(shifted, -30.0, 30.0))
    return exp_values / exp_values.sum(axis=1, keepdims=True)


def _safe_binary_logit(probability: float) -> float:
    bounded = _clamp(float(probability), 1e-4, 1.0 - 1e-4)
    return math.log(bounded / (1.0 - bounded))


def _base_triplet_probs(snapshot: PredictionFeatureSnapshot, fallback: np.ndarray) -> np.ndarray:
    if None not in (snapshot.market_prob_w1, snapshot.market_prob_draw, snapshot.market_prob_w2):
        probs = np.array(
            [
                float(snapshot.market_prob_w1) / 100.0,
                float(snapshot.market_prob_draw) / 100.0,
                float(snapshot.market_prob_w2) / 100.0,
            ],
            dtype=np.float64,
        )
        total = probs.sum()
        if total > 0:
            return probs / total
    return fallback.copy()


def _base_binary_prob(value_pct: Optional[float], fallback: float) -> float:
    if value_pct is None:
        return fallback
    return _clamp(float(value_pct) / 100.0, 1e-4, 1.0 - 1e-4)


def _recalibrated_btts_base_prob(
    row: Optional[dict],
    snapshot: PredictionFeatureSnapshot,
    *,
    fallback: float,
) -> float:
    if snapshot.model_prob_btts_yes is not None:
        return _base_binary_prob(snapshot.model_prob_btts_yes, fallback)
    if row is not None:
        model_prob = row.get("model_prob_btts_yes")
        if model_prob is not None:
            return _base_binary_prob(model_prob, fallback)
        profiles = row.get("_recalibration_profiles")
        if profiles:
            try:
                from baseline_model import build_numeric_baseline
                from models import MatchData

                recalibrated = build_numeric_baseline(
                    MatchData(team1=snapshot.team1, team2=snapshot.team2),
                    snapshot,
                    recalibration_profiles=profiles,
                )
                return _base_binary_prob(recalibrated.get("prob_btts_yes"), fallback)
            except Exception:
                pass
    return _base_binary_prob(snapshot.market_prob_btts_yes, fallback)


def _standardize_fit(feature_matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    means = feature_matrix.mean(axis=0)
    scales = feature_matrix.std(axis=0)
    scales = np.where(scales < 1e-6, 1.0, scales)
    return means, scales


def _standardize_apply(feature_matrix: np.ndarray, means: np.ndarray, scales: np.ndarray) -> np.ndarray:
    return (feature_matrix - means) / scales


def _add_bias(feature_matrix: np.ndarray) -> np.ndarray:
    return np.concatenate([np.ones((feature_matrix.shape[0], 1)), feature_matrix], axis=1)


def _feature_indices(feature_names: tuple[str, ...], selected_names: tuple[str, ...]) -> tuple[int, ...]:
    index_map = {name: idx for idx, name in enumerate(feature_names)}
    return tuple(index_map[name] for name in selected_names if name in index_map)


def _fit_binary_logistic(
    feature_matrix: np.ndarray,
    outcomes: np.ndarray,
    base_probs: np.ndarray,
    *,
    learning_rate: float = 0.04,
    iterations: int = 800,
    l2: float = 0.08,
) -> Optional[np.ndarray]:
    unique = np.unique(outcomes)
    if feature_matrix.shape[0] == 0 or unique.size < 2:
        return None

    x = _add_bias(feature_matrix)
    y = outcomes.astype(np.float64)
    weights = np.zeros(x.shape[1], dtype=np.float64)
    base_logits = np.array([_safe_binary_logit(prob) for prob in base_probs], dtype=np.float64)

    for _ in range(iterations):
        logits = np.clip(base_logits + (x @ weights), -30.0, 30.0)
        probs = 1.0 / (1.0 + np.exp(-logits))
        gradient = (x.T @ (probs - y)) / x.shape[0]
        gradient[1:] += l2 * weights[1:]
        weights -= learning_rate * gradient
    return weights


def _fit_softmax(
    feature_matrix: np.ndarray,
    outcomes: np.ndarray,
    base_probs: np.ndarray,
    *,
    learning_rate: float = 0.03,
    iterations: int = 1000,
    l2: float = 0.08,
) -> Optional[np.ndarray]:
    if feature_matrix.shape[0] == 0 or np.unique(outcomes).size < 2:
        return None

    x = _add_bias(feature_matrix)
    classes = 3
    targets = np.eye(classes, dtype=np.float64)[outcomes.astype(np.int64)]
    weights = np.zeros((x.shape[1], classes), dtype=np.float64)
    base_logits = np.log(np.clip(base_probs, 1e-6, 1.0))

    for _ in range(iterations):
        logits = base_logits + (x @ weights)
        probs = _softmax(logits)
        gradient = (x.T @ (probs - targets)) / x.shape[0]
        gradient[1:, :] += l2 * weights[1:, :]
        weights -= learning_rate * gradient
    return weights


def _fit_logit_ridge(
    feature_matrix: np.ndarray,
    target_probs: np.ndarray,
    *,
    l2: float = 0.12,
) -> Optional[np.ndarray]:
    if feature_matrix.shape[0] == 0:
        return None
    x = _add_bias(feature_matrix)
    y = np.array([_safe_binary_logit(probability) for probability in target_probs], dtype=np.float64)
    ridge = np.eye(x.shape[1], dtype=np.float64) * l2
    ridge[0, 0] = 0.0
    lhs = x.T @ x
    rhs = x.T @ y
    try:
        return np.linalg.solve(lhs + ridge, rhs)
    except np.linalg.LinAlgError:
        return None


def _predict_logit_ridge_probability(
    feature_matrix: np.ndarray,
    weights: Optional[np.ndarray],
    *,
    fallback: float,
) -> np.ndarray:
    if weights is None:
        return np.full(feature_matrix.shape[0], _clamp(fallback, 1e-4, 1.0 - 1e-4), dtype=np.float64)
    x = _add_bias(feature_matrix)
    logits = np.clip(x @ weights, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-logits))


def fit_trainable_numeric_model(rows: list[dict], *, min_rows: int = 12) -> Optional[TrainableNumericModel]:
    feature_vectors: list[np.ndarray] = []
    one_x_two_targets: list[int] = []
    over_targets: list[int] = []
    btts_targets: list[int] = []
    snapshots: list[PredictionFeatureSnapshot] = []
    training_rows: list[dict] = []
    btts_feature_vectors: list[np.ndarray] = []
    btts_targets_market: list[int] = []
    btts_snapshots_market: list[PredictionFeatureSnapshot] = []

    for row in rows:
        snapshot = _snapshot_from_row(row)
        actual_result = str(row.get("actual_result") or "").strip().upper()
        home_goals = row.get("actual_goals_home")
        away_goals = row.get("actual_goals_away")
        if snapshot is None or actual_result not in {"W1", "DRAW", "W2"} or home_goals is None or away_goals is None:
            continue
        try:
            home_goals_i = int(home_goals)
            away_goals_i = int(away_goals)
        except (TypeError, ValueError):
            continue

        feature_vectors.append(_feature_vector(snapshot))
        snapshots.append(snapshot)
        training_rows.append(row)
        one_x_two_targets.append({"W1": 0, "DRAW": 1, "W2": 2}[actual_result])
        over_targets.append(1 if (home_goals_i + away_goals_i) > 2 else 0)
        btts_outcome = 1 if (home_goals_i > 0 and away_goals_i > 0) else 0
        btts_targets.append(btts_outcome)
        if snapshot.market_prob_btts_yes is not None and snapshot.market_prob_btts_no is not None:
            btts_feature_vectors.append(feature_vectors[-1])
            btts_targets_market.append(btts_outcome)
            btts_snapshots_market.append(snapshot)

    if len(feature_vectors) < min_rows:
        return None

    matrix = np.vstack(feature_vectors)
    means, scales = _standardize_fit(matrix)
    standardized = _standardize_apply(matrix, means, scales)

    one_x_two_y = np.array(one_x_two_targets, dtype=np.int64)
    over_y = np.array(over_targets, dtype=np.int64)
    btts_y = np.array(btts_targets, dtype=np.int64)

    one_x_two_base = np.bincount(one_x_two_y, minlength=3).astype(np.float64)
    one_x_two_base /= max(float(one_x_two_base.sum()), 1.0)
    over_base = float(over_y.mean()) if over_y.size else 0.5
    btts_base = float(btts_y.mean()) if btts_y.size else 0.5

    one_x_two_base_probs = np.vstack([
        _base_triplet_probs(snapshot, one_x_two_base)
        for snapshot in snapshots
    ])
    over_base_probs = np.array(
        [_base_binary_prob(snapshot.market_prob_over_2_5, over_base) for snapshot in snapshots],
        dtype=np.float64,
    )
    btts_feature_indices = _feature_indices(tuple(FEATURE_NAMES), BTTS_FEATURE_NAMES)
    btts_proxy_feature_indices = _feature_indices(tuple(FEATURE_NAMES), BTTS_PROXY_FEATURE_NAMES)

    one_x_two_weights = _fit_softmax(standardized, one_x_two_y, one_x_two_base_probs)
    over_weights = _fit_binary_logistic(standardized, over_y, over_base_probs)
    btts_weights = None
    btts_trained_rows = 0
    btts_market_rows = len(btts_feature_vectors)
    btts_prior_proxy_weights = None
    if len(btts_feature_vectors) >= max(40, min_rows):
        btts_matrix_full = np.vstack(btts_feature_vectors)
        btts_standardized = _standardize_apply(btts_matrix_full, means, scales)
        btts_market_only_matrix = (
            btts_standardized[:, list(btts_feature_indices)]
            if btts_feature_indices else btts_standardized
        )
        btts_y_market = np.array(btts_targets_market, dtype=np.int64)
        btts_base = float(btts_y_market.mean()) if btts_y_market.size else btts_base
        btts_market_base_probs = np.array(
            [_base_binary_prob(snapshot.market_prob_btts_yes, btts_base) for snapshot in btts_snapshots_market],
            dtype=np.float64,
        )
        btts_proxy_matrix = (
            btts_standardized[:, list(btts_proxy_feature_indices)]
            if btts_proxy_feature_indices else btts_standardized
        )
        btts_prior_proxy_weights = _fit_logit_ridge(
            btts_proxy_matrix,
            btts_market_base_probs,
            l2=0.18,
        )

    if len(feature_vectors) >= max(40, min_rows):
        btts_full_matrix = standardized[:, list(btts_feature_indices)] if btts_feature_indices else standardized
        if btts_prior_proxy_weights is not None:
            btts_proxy_full_matrix = (
                standardized[:, list(btts_proxy_feature_indices)]
                if btts_proxy_feature_indices else standardized
            )
            btts_proxy_probs = _predict_logit_ridge_probability(
                btts_proxy_full_matrix,
                btts_prior_proxy_weights,
                fallback=btts_base,
            )
        else:
            btts_proxy_probs = np.full(len(snapshots), _clamp(btts_base, 1e-4, 1.0 - 1e-4), dtype=np.float64)
        btts_base_probs = np.array(
            [
                _recalibrated_btts_base_prob(
                    row,
                    snapshot,
                    fallback=float(proxy_prob),
                )
                for row, snapshot, proxy_prob in zip(training_rows, snapshots, btts_proxy_probs)
            ],
            dtype=np.float64,
        )
        btts_weights = _fit_binary_logistic(
            btts_full_matrix,
            btts_y,
            btts_base_probs,
            learning_rate=0.018,
            iterations=700,
            l2=0.30,
        )
        btts_trained_rows = len(feature_vectors)

    return TrainableNumericModel(
        feature_names=tuple(FEATURE_NAMES),
        means=means,
        scales=scales,
        one_x_two_weights=one_x_two_weights,
        one_x_two_base_rates=one_x_two_base,
        over_weights=over_weights,
        over_base_rate=over_base,
        btts_weights=btts_weights,
        btts_base_rate=btts_base,
        btts_feature_indices=btts_feature_indices,
        btts_proxy_feature_indices=btts_proxy_feature_indices,
        btts_prior_proxy_weights=btts_prior_proxy_weights,
        trained_rows=len(feature_vectors),
        btts_market_rows=btts_market_rows,
        btts_trained_rows=btts_trained_rows,
        blend_alpha=round(_clamp(len(feature_vectors) / 300.0, 0.2, 0.45), 3),
        btts_blend_alpha=round(_clamp(btts_market_rows / 1800.0, 0.03, 0.10), 3) if btts_market_rows else 0.0,
    )


def predict_trainable_numeric_model(
    model: Optional[TrainableNumericModel],
    row_or_snapshot: dict | PredictionFeatureSnapshot,
) -> Optional[dict[str, float]]:
    if model is None:
        return None
    snapshot = row_or_snapshot if isinstance(row_or_snapshot, PredictionFeatureSnapshot) else _snapshot_from_row(row_or_snapshot)
    if snapshot is None:
        return None

    features = _feature_vector(snapshot).reshape(1, -1)
    standardized = _standardize_apply(features, model.means, model.scales)
    biased = _add_bias(standardized)
    base_triplet = _base_triplet_probs(snapshot, model.one_x_two_base_rates)
    base_logits = np.log(np.clip(base_triplet.reshape(1, -1), 1e-6, 1.0))

    if model.one_x_two_weights is not None:
        logits = base_logits + (biased @ model.one_x_two_weights)
        one_x_two_probs = _softmax(logits)[0]
    else:
        one_x_two_probs = model.one_x_two_base_rates
    one_x_two_probs = ((1.0 - model.blend_alpha) * base_triplet) + (model.blend_alpha * one_x_two_probs)
    one_x_two_probs = one_x_two_probs / max(float(one_x_two_probs.sum()), 1e-6)

    over_base_prob = _base_binary_prob(snapshot.market_prob_over_2_5, model.over_base_rate)
    if model.over_weights is not None:
        over_logit = _safe_binary_logit(over_base_prob) + float((biased @ model.over_weights).reshape(-1)[0])
        over_prob = 1.0 / (1.0 + math.exp(-_clamp(over_logit, -30.0, 30.0)))
    else:
        over_prob = over_base_prob
    over_prob = ((1.0 - model.blend_alpha) * over_base_prob) + (model.blend_alpha * over_prob)

    if isinstance(row_or_snapshot, dict):
        btts_base_prob = _recalibrated_btts_base_prob(
            row_or_snapshot,
            snapshot,
            fallback=model.btts_base_rate,
        )
    elif snapshot.model_prob_btts_yes is not None:
        btts_base_prob = _base_binary_prob(snapshot.model_prob_btts_yes, model.btts_base_rate)
    elif snapshot.market_prob_btts_yes is not None:
        btts_base_prob = _base_binary_prob(snapshot.market_prob_btts_yes, model.btts_base_rate)
    elif model.btts_prior_proxy_weights is not None:
        btts_proxy_features = (
            standardized[:, list(model.btts_proxy_feature_indices)]
            if model.btts_proxy_feature_indices else standardized
        )
        btts_base_prob = float(
            _predict_logit_ridge_probability(
                btts_proxy_features,
                model.btts_prior_proxy_weights,
                fallback=model.btts_base_rate,
            )[0]
        )
    else:
        btts_base_prob = _base_binary_prob(None, model.btts_base_rate)
    if model.btts_weights is not None:
        btts_features = (
            standardized[:, list(model.btts_feature_indices)]
            if model.btts_feature_indices else standardized
        )
        btts_biased = _add_bias(btts_features)
        btts_logit = _safe_binary_logit(btts_base_prob) + float((btts_biased @ model.btts_weights).reshape(-1)[0])
        btts_prob = 1.0 / (1.0 + math.exp(-_clamp(btts_logit, -30.0, 30.0)))
    else:
        btts_prob = btts_base_prob
    btts_prob = ((1.0 - model.btts_blend_alpha) * btts_base_prob) + (model.btts_blend_alpha * btts_prob)

    prob_w1 = round(float(one_x_two_probs[0]) * 100.0, 1)
    prob_draw = round(float(one_x_two_probs[1]) * 100.0, 1)
    prob_w2 = round(float(one_x_two_probs[2]) * 100.0, 1)
    triplet_total = prob_w1 + prob_draw + prob_w2
    if triplet_total != 100.0:
        prob_w1 = round(prob_w1 + (100.0 - triplet_total), 1)

    prob_over = round(_clamp(over_prob * 100.0, 1.0, 99.0), 1)
    prob_under = round(100.0 - prob_over, 1)
    prob_btts_yes = round(_clamp(btts_prob * 100.0, 1.0, 99.0), 1)
    prob_btts_no = round(100.0 - prob_btts_yes, 1)

    return {
        "prob_w1": prob_w1,
        "prob_draw": prob_draw,
        "prob_w2": prob_w2,
        "prob_over_2_5": prob_over,
        "prob_under_2_5": prob_under,
        "prob_btts_yes": prob_btts_yes,
        "prob_btts_no": prob_btts_no,
    }
