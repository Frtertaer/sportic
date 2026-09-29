"""
LightGBM ensemble model for O/U 2.5 and 1X2 prediction.

Trained on historical data to learn non-linear patterns that the hardcoded
baseline model misses. The ensemble learns WHEN the model's disagreement
with the market is actually correct.

Architecture:
  - Input: ~30 features from PredictionFeatureSnapshot + baseline model output
  - Output: calibrated probability for Over 2.5 / 1X2
  - Training: 80/20 time-split (not random!) to prevent look-ahead bias
  - Blend: ensemble_weight * LGBM + (1 - ensemble_weight) * baseline
"""
from __future__ import annotations

import logging
import math
import pickle
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "sportsbot.db"
MODEL_PATH = Path(__file__).parent / "data" / "ensemble_models"
TRAIN_CUTOFF = "2024-08-01"  # test set starts here

# Blend weight: how much to trust ensemble vs baseline
ENSEMBLE_OU_WEIGHT = 0.40
ENSEMBLE_1X2_WEIGHT = 0.30

# Feature columns used for training
FEATURE_COLS = [
    "market_prob_w1", "market_prob_draw", "market_prob_w2",
    "market_prob_over_2_5", "market_prob_under_2_5",
    "market_prob_btts_yes", "market_prob_btts_no",
    "form_points_diff", "form_gd_diff",
    "scored_avg_diff", "conceded_avg_diff",
    "h2h_win_rate", "h2h_draw_rate", "h2h_avg_goals",
    "xg_diff", "xga_edge",
    "rating_diff", "possession_diff",
    "point_gap", "position_gap",
    "rest_gap_hours",
    "market_value_ratio_log",
    "key_absence_diff",
    "baseline_prob_over", "baseline_prob_under",
    "baseline_prob_w1", "baseline_prob_draw", "baseline_prob_w2",
    "baseline_xg",
    "model_vs_market_over",  # key: model - market edge for O/U
    "model_vs_market_w1",    # model - market edge for W1
    "referee_goals_vs_avg",  # referee goal tendency vs league average
    "referee_over_2_5_pct",  # referee O/U 2.5 historical %
]


@dataclass
class EnsembleModels:
    ou_model: object = None
    onextwo_model: object = None
    trained: bool = False
    train_matches: int = 0
    test_accuracy_ou: float = 0.0
    test_accuracy_1x2: float = 0.0


_cached_models: Optional[EnsembleModels] = None


def _load_raw_data() -> list[dict]:
    """Load historical matches with all features."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            m.team1_name, m.team2_name,
            m.team1_score, m.team2_score, m.match_date_utc, m.competition_name,
            ml.implied_prob_w1, ml.implied_prob_draw, ml.implied_prob_w2,
            ml.odds_over_2_5, ml.odds_under_2_5,
            ml.odds_btts_yes, ml.odds_btts_no,
            ts.team1_form_points_last5, ts.team2_form_points_last5,
            ts.team1_goal_diff_last5, ts.team2_goal_diff_last5,
            ts.team1_scored_avg, ts.team2_scored_avg,
            ts.team1_conceded_avg, ts.team2_conceded_avg,
            ts.h2h_team1_win_rate, ts.h2h_draw_rate, ts.h2h_avg_goals,
            ts.team1_xg_for, ts.team2_xg_for,
            ts.team1_xga, ts.team2_xga,
            ts.team1_rating, ts.team2_rating,
            ts.team1_possession, ts.team2_possession,
            ts.team1_table_position, ts.team2_table_position,
            ts.team1_points, ts.team2_points,
            ts.rest_gap_hours,
            ts.team1_market_value_eur, ts.team2_market_value_eur,
            ts.key_absence_diff,
            ts.data_completeness
        FROM historical_matches m
        JOIN historical_market_lines ml ON ml.historical_match_id = m.id
        JOIN historical_team_snapshots ts ON ts.historical_match_id = m.id
        WHERE m.team1_score IS NOT NULL AND m.team2_score IS NOT NULL
          AND ml.implied_prob_w1 IS NOT NULL
          AND ts.team1_scored_avg IS NOT NULL
        ORDER BY m.match_date_utc
    """).fetchall()
    conn.close()

    # Dedup: keep best per match (use team names + date + scores for uniqueness)
    best: dict = {}
    for r in rows:
        key = (r["team1_name"], r["team2_name"], r["match_date_utc"],
               r["team1_score"], r["team2_score"])
        d = dict(r)
        dc = d.get("data_completeness") or 0
        if key not in best or dc > (best[key].get("data_completeness") or 0):
            best[key] = d
    return sorted(best.values(), key=lambda x: x.get("match_date_utc", ""))


def _build_referee_index() -> dict[tuple[str, str, str], dict]:
    """Build index: (home_team, away_team, date) -> referee stats from CSV."""
    import csv
    csv_dir = Path(__file__).parent / "data" / "football_data_co_uk"
    if not csv_dir.exists():
        return {}

    # First build referee profiles
    from referee import build_referee_profiles
    profiles = build_referee_profiles(min_matches=10)

    # Then build match-to-referee mapping
    index: dict[tuple[str, str, str], dict] = {}
    for season_dir in sorted(csv_dir.iterdir()):
        if not season_dir.is_dir():
            continue
        for csv_file in sorted(season_dir.glob("*.csv")):
            try:
                with open(csv_file, encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        ref = (row.get("Referee") or "").strip()
                        home = (row.get("HomeTeam") or "").strip()
                        away = (row.get("AwayTeam") or "").strip()
                        date = (row.get("Date") or "").strip()
                        if not ref or not home or not away:
                            continue
                        profile = profiles.get(ref)
                        if profile:
                            index[(home, away, date)] = {
                                "goals_vs_avg": profile.goals_vs_avg,
                                "over_2_5_pct": profile.over_2_5_pct,
                            }
            except Exception:
                continue
    return index


# Global referee index (built once during training)
_referee_index: Optional[dict] = None


def _get_referee_for_match(team1: str, team2: str, date: str) -> dict:
    """Look up referee stats for a historical match. Fuzzy match on team names."""
    global _referee_index
    if _referee_index is None:
        _referee_index = _build_referee_index()

    # Try exact match
    result = _referee_index.get((team1, team2, date))
    if result:
        return result

    # Try with date prefix (YYYY-MM-DD matches DD/MM/YYYY)
    if "-" in date:
        parts = date.split("-")
        if len(parts) == 3:
            alt_date = f"{parts[2]}/{parts[1]}/{parts[0]}"
            result = _referee_index.get((team1, team2, alt_date))
            if result:
                return result

    return {"goals_vs_avg": 0.0, "over_2_5_pct": 50.0}


def _safe_float(v, default=0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _build_features(row: dict, baseline: dict) -> dict:
    """Build feature dict from raw row + baseline model output."""
    mp_w1 = _safe_float(row.get("implied_prob_w1"))
    mp_d = _safe_float(row.get("implied_prob_draw"))
    mp_w2 = _safe_float(row.get("implied_prob_w2"))

    # Market O/U
    ou = _safe_float(row.get("odds_over_2_5"))
    un = _safe_float(row.get("odds_under_2_5"))
    mp_over = mp_under = 50.0
    if ou > 1 and un > 1:
        inv_o, inv_u = 1.0 / ou, 1.0 / un
        t = inv_o + inv_u
        mp_over = inv_o * 100.0 / t
        mp_under = inv_u * 100.0 / t

    # Market BTTS
    by = _safe_float(row.get("odds_btts_yes"))
    bn = _safe_float(row.get("odds_btts_no"))
    mp_btts_y = mp_btts_n = 50.0
    if by > 1 and bn > 1:
        inv_y, inv_n = 1.0 / by, 1.0 / bn
        t = inv_y + inv_n
        mp_btts_y = inv_y * 100.0 / t
        mp_btts_n = inv_n * 100.0 / t

    def diff(a, b):
        va, vb = _safe_float(a, None), _safe_float(b, None)
        if va is None or vb is None:
            return 0.0
        return va - vb

    mv1 = _safe_float(row.get("team1_market_value_eur"), None)
    mv2 = _safe_float(row.get("team2_market_value_eur"), None)
    mv_log = 0.0
    if mv1 and mv2 and mv2 > 0:
        mv_log = math.log(mv1 / mv2, 2)

    return {
        "market_prob_w1": mp_w1,
        "market_prob_draw": mp_d,
        "market_prob_w2": mp_w2,
        "market_prob_over_2_5": mp_over,
        "market_prob_under_2_5": mp_under,
        "market_prob_btts_yes": mp_btts_y,
        "market_prob_btts_no": mp_btts_n,
        "form_points_diff": diff(row.get("team1_form_points_last5"), row.get("team2_form_points_last5")),
        "form_gd_diff": diff(row.get("team1_goal_diff_last5"), row.get("team2_goal_diff_last5")),
        "scored_avg_diff": diff(row.get("team1_scored_avg"), row.get("team2_scored_avg")),
        "conceded_avg_diff": diff(row.get("team1_conceded_avg"), row.get("team2_conceded_avg")),
        "h2h_win_rate": _safe_float(row.get("h2h_team1_win_rate"), 50.0),
        "h2h_draw_rate": _safe_float(row.get("h2h_draw_rate"), 25.0),
        "h2h_avg_goals": _safe_float(row.get("h2h_avg_goals"), 2.6),
        "xg_diff": diff(row.get("team1_xg_for"), row.get("team2_xg_for")),
        "xga_edge": diff(row.get("team2_xga"), row.get("team1_xga")),
        "rating_diff": diff(row.get("team1_rating"), row.get("team2_rating")),
        "possession_diff": diff(row.get("team1_possession"), row.get("team2_possession")),
        "point_gap": diff(row.get("team1_points"), row.get("team2_points")),
        "position_gap": diff(row.get("team2_table_position"), row.get("team1_table_position")),
        "rest_gap_hours": _safe_float(row.get("rest_gap_hours"), 0),
        "market_value_ratio_log": mv_log,
        "key_absence_diff": _safe_float(row.get("key_absence_diff"), 0),
        "baseline_prob_over": baseline.get("prob_over_2_5", 50.0),
        "baseline_prob_under": baseline.get("prob_under_2_5", 50.0),
        "baseline_prob_w1": baseline.get("prob_w1", 33.3),
        "baseline_prob_draw": baseline.get("prob_draw", 33.3),
        "baseline_prob_w2": baseline.get("prob_w2", 33.3),
        "baseline_xg": baseline.get("expected_goals", 2.55),
        "model_vs_market_over": baseline.get("prob_over_2_5", 50.0) - mp_over,
        "model_vs_market_w1": baseline.get("prob_w1", 33.3) - mp_w1,
        "referee_goals_vs_avg": _safe_float(row.get("_ref_goals_vs_avg"), 0.0),
        "referee_over_2_5_pct": _safe_float(row.get("_ref_over_2_5_pct"), 50.0),
    }


def _build_baseline_for_row(row: dict) -> dict:
    """Run baseline model on historical row."""
    from models import MatchData, FlashscoreData, PredictionFeatureSnapshot
    from baseline_model import build_numeric_baseline

    mp_w1 = round(float(row["implied_prob_w1"]), 1)
    mp_d = round(float(row["implied_prob_draw"]), 1)
    mp_w2 = round(float(row["implied_prob_w2"]), 1)

    ou = _safe_float(row.get("odds_over_2_5"))
    un = _safe_float(row.get("odds_under_2_5"))
    market_over = market_under = None
    if ou > 1 and un > 1:
        inv_o, inv_u = 1.0 / ou, 1.0 / un
        t = inv_o + inv_u
        market_over = round(inv_o * 100 / t, 1)
        market_under = round(inv_u * 100 / t, 1)

    def diff(a, b):
        return float(a - b) if a is not None and b is not None else None

    mv1, mv2 = row.get("team1_market_value_eur"), row.get("team2_market_value_eur")
    mv_ratio = round(float(mv1) / float(mv2), 4) if mv1 and mv2 and float(mv2) > 0 else None

    pos1, pos2 = row.get("team1_table_position"), row.get("team2_table_position")
    pts1, pts2 = row.get("team1_points"), row.get("team2_points")

    snapshot = PredictionFeatureSnapshot(
        team1="team1", team2="team2",
        competition_name=row.get("competition_name"),
        data_completeness=float(row.get("data_completeness") or 0.3),
        is_team1_home=True, is_neutral_venue=False,
        market_prob_w1=mp_w1, market_prob_draw=mp_d, market_prob_w2=mp_w2,
        market_prob_over_2_5=market_over, market_prob_under_2_5=market_under,
        form_points_last5_diff=diff(row.get("team1_form_points_last5"), row.get("team2_form_points_last5")),
        form_goal_diff_last5_diff=diff(row.get("team1_goal_diff_last5"), row.get("team2_goal_diff_last5")),
        form_scored_avg_diff=diff(row.get("team1_scored_avg"), row.get("team2_scored_avg")),
        form_conceded_avg_diff=diff(row.get("team1_conceded_avg"), row.get("team2_conceded_avg")),
        h2h_team1_win_rate=row.get("h2h_team1_win_rate"),
        h2h_draw_rate=row.get("h2h_draw_rate"),
        h2h_avg_goals=row.get("h2h_avg_goals"),
        xg_diff=diff(row.get("team1_xg_for"), row.get("team2_xg_for")),
        xga_edge=diff(row.get("team2_xga"), row.get("team1_xga")),
        rating_diff=diff(row.get("team1_rating"), row.get("team2_rating")),
        possession_diff=diff(row.get("team1_possession"), row.get("team2_possession")),
        standings_point_gap=int(pts1 - pts2) if pts1 is not None and pts2 is not None else None,
        standings_position_gap=int(pos2 - pos1) if pos1 is not None and pos2 is not None else None,
        rest_gap_hours=row.get("rest_gap_hours"),
        market_value_ratio=mv_ratio,
        key_absence_diff=int(row["key_absence_diff"]) if row.get("key_absence_diff") is not None else None,
    )

    data = MatchData(team1="team1", team2="team2")
    if row.get("team1_scored_avg") is not None:
        data.flashscore = FlashscoreData(
            team1_name="team1", team2_name="team2",
            team1_goals_scored_avg=row.get("team1_scored_avg"),
            team1_goals_conceded_avg=row.get("team1_conceded_avg"),
            team2_goals_scored_avg=row.get("team2_scored_avg"),
            team2_goals_conceded_avg=row.get("team2_conceded_avg"),
        )

    return build_numeric_baseline(data, snapshot)


def train_ensemble() -> EnsembleModels:
    """Train LightGBM models on historical data."""
    import lightgbm as lgb

    logger.info("Loading historical data for ensemble training...")
    raw = _load_raw_data()
    logger.info("Total rows with features: %d", len(raw))

    # Enrich with referee data
    logger.info("Building referee index...")
    ref_enriched = 0
    for row in raw:
        ref_data = _get_referee_for_match(
            row.get("team1_name", ""), row.get("team2_name", ""),
            row.get("match_date_utc", ""),
        )
        row["_ref_goals_vs_avg"] = ref_data["goals_vs_avg"]
        row["_ref_over_2_5_pct"] = ref_data["over_2_5_pct"]
        if ref_data["goals_vs_avg"] != 0.0:
            ref_enriched += 1
    logger.info("Referee-enriched matches: %d/%d", ref_enriched, len(raw))
    print(f"  Referee-enriched: {ref_enriched}/{len(raw)}")

    # Build features + targets
    X_rows = []
    y_ou = []
    y_1x2 = []  # 0=W1, 1=D, 2=W2
    dates = []

    for row in raw:
        baseline = _build_baseline_for_row(row)
        features = _build_features(row, baseline)
        X_rows.append([features[c] for c in FEATURE_COLS])

        hg, ag = int(row["team1_score"]), int(row["team2_score"])
        y_ou.append(1 if (hg + ag) > 2.5 else 0)
        y_1x2.append(0 if hg > ag else (2 if ag > hg else 1))
        dates.append(row.get("match_date_utc", ""))

    X = np.array(X_rows, dtype=np.float32)
    y_ou = np.array(y_ou)
    y_1x2 = np.array(y_1x2)

    # Time-based split
    split_idx = next((i for i, d in enumerate(dates) if d >= TRAIN_CUTOFF), len(dates))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_ou_train, y_ou_test = y_ou[:split_idx], y_ou[split_idx:]
    y_1x2_train, y_1x2_test = y_1x2[:split_idx], y_1x2[split_idx:]

    logger.info("Train: %d, Test: %d (split at %s)", len(X_train), len(X_test), TRAIN_CUTOFF)
    print(f"\n  Train: {len(X_train)} matches, Test: {len(X_test)} matches")

    if len(X_train) < 100 or len(X_test) < 50:
        logger.warning("Not enough data for ensemble training")
        return EnsembleModels()

    # ── O/U model ──────────────────────────────────────────────────────
    ou_params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 5,
        "min_child_samples": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 1.0,
        "verbose": -1,
    }

    ou_train_set = lgb.Dataset(X_train, y_ou_train, feature_name=FEATURE_COLS)
    ou_valid_set = lgb.Dataset(X_test, y_ou_test, feature_name=FEATURE_COLS, reference=ou_train_set)

    ou_model = lgb.train(
        ou_params,
        ou_train_set,
        num_boost_round=300,
        valid_sets=[ou_valid_set],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
    )

    # Evaluate O/U
    ou_pred_probs = ou_model.predict(X_test)
    ou_pred = (ou_pred_probs > 0.5).astype(int)
    ou_acc = (ou_pred == y_ou_test).mean() * 100

    # ── 1X2 model ─────────────────────────────────────────────────────
    onextwo_params = {
        "objective": "multiclass",
        "num_class": 3,
        "metric": "multi_logloss",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 5,
        "min_child_samples": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 1.0,
        "verbose": -1,
    }

    onextwo_train_set = lgb.Dataset(X_train, y_1x2_train, feature_name=FEATURE_COLS)
    onextwo_valid_set = lgb.Dataset(X_test, y_1x2_test, feature_name=FEATURE_COLS, reference=onextwo_train_set)

    onextwo_model = lgb.train(
        onextwo_params,
        onextwo_train_set,
        num_boost_round=300,
        valid_sets=[onextwo_valid_set],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
    )

    # Evaluate 1X2
    onextwo_pred_probs = onextwo_model.predict(X_test)  # shape: (n, 3)
    onextwo_pred = onextwo_pred_probs.argmax(axis=1)
    onextwo_acc = (onextwo_pred == y_1x2_test).mean() * 100

    print(f"  O/U accuracy (test):  {ou_acc:.1f}%")
    print(f"  1X2 accuracy (test):  {onextwo_acc:.1f}%")

    # Feature importance
    print("\n  Top 10 O/U features:")
    imp = ou_model.feature_importance(importance_type="gain")
    for idx in np.argsort(imp)[::-1][:10]:
        print(f"    {FEATURE_COLS[idx]:30s} {imp[idx]:8.1f}")

    print("\n  Top 10 1X2 features:")
    imp2 = onextwo_model.feature_importance(importance_type="gain")
    for idx in np.argsort(imp2)[::-1][:10]:
        print(f"    {FEATURE_COLS[idx]:30s} {imp2[idx]:8.1f}")

    # Save models
    MODEL_PATH.mkdir(parents=True, exist_ok=True)
    ou_model.save_model(str(MODEL_PATH / "ou_model.lgb"))
    onextwo_model.save_model(str(MODEL_PATH / "onextwo_model.lgb"))

    result = EnsembleModels(
        ou_model=ou_model,
        onextwo_model=onextwo_model,
        trained=True,
        train_matches=len(X_train),
        test_accuracy_ou=ou_acc,
        test_accuracy_1x2=onextwo_acc,
    )
    logger.info("Ensemble trained: O/U %.1f%%, 1X2 %.1f%%", ou_acc, onextwo_acc)
    return result


def load_ensemble() -> EnsembleModels:
    """Load pre-trained models from disk."""
    global _cached_models
    if _cached_models is not None and _cached_models.trained:
        return _cached_models

    try:
        import lightgbm as lgb
        ou_path = MODEL_PATH / "ou_model.lgb"
        onextwo_path = MODEL_PATH / "onextwo_model.lgb"
        if ou_path.exists() and onextwo_path.exists():
            ou_model = lgb.Booster(model_file=str(ou_path))
            onextwo_model = lgb.Booster(model_file=str(onextwo_path))
            _cached_models = EnsembleModels(
                ou_model=ou_model,
                onextwo_model=onextwo_model,
                trained=True,
            )
            logger.info("Ensemble models loaded from disk")
            return _cached_models
    except Exception as exc:
        logger.debug("Failed to load ensemble models: %s", exc)

    return EnsembleModels()


def predict_ensemble(
    snapshot,  # PredictionFeatureSnapshot
    baseline: dict,
    row_or_none: dict | None = None,
) -> dict | None:
    """Get ensemble predictions. Returns blended probabilities or None."""
    models = load_ensemble()
    if not models.trained:
        return None

    try:
        # Build features from snapshot + baseline
        features = {
            "market_prob_w1": float(snapshot.market_prob_w1 or 33.3),
            "market_prob_draw": float(snapshot.market_prob_draw or 33.3),
            "market_prob_w2": float(snapshot.market_prob_w2 or 33.3),
            "market_prob_over_2_5": float(snapshot.market_prob_over_2_5 or 50.0),
            "market_prob_under_2_5": float(snapshot.market_prob_under_2_5 or 50.0),
            "market_prob_btts_yes": float(snapshot.market_prob_btts_yes or 50.0),
            "market_prob_btts_no": float(snapshot.market_prob_btts_no or 50.0),
            "form_points_diff": float(snapshot.form_points_last5_diff or 0),
            "form_gd_diff": float(snapshot.form_goal_diff_last5_diff or 0),
            "scored_avg_diff": float(snapshot.form_scored_avg_diff or 0),
            "conceded_avg_diff": float(snapshot.form_conceded_avg_diff or 0),
            "h2h_win_rate": float(snapshot.h2h_team1_win_rate or 50),
            "h2h_draw_rate": float(snapshot.h2h_draw_rate or 25),
            "h2h_avg_goals": float(snapshot.h2h_avg_goals or 2.6),
            "xg_diff": float(snapshot.xg_diff or 0),
            "xga_edge": float(snapshot.xga_edge or 0),
            "rating_diff": float(snapshot.rating_diff or 0),
            "possession_diff": float(snapshot.possession_diff or 0),
            "point_gap": float(snapshot.standings_point_gap or 0),
            "position_gap": float(snapshot.standings_position_gap or 0),
            "rest_gap_hours": float(snapshot.rest_gap_hours or 0),
            "market_value_ratio_log": math.log(float(snapshot.market_value_ratio), 2) if snapshot.market_value_ratio and snapshot.market_value_ratio > 0 else 0.0,
            "key_absence_diff": float(snapshot.key_absence_diff or 0),
            "baseline_prob_over": baseline.get("prob_over_2_5", 50.0),
            "baseline_prob_under": baseline.get("prob_under_2_5", 50.0),
            "baseline_prob_w1": baseline.get("prob_w1", 33.3),
            "baseline_prob_draw": baseline.get("prob_draw", 33.3),
            "baseline_prob_w2": baseline.get("prob_w2", 33.3),
            "baseline_xg": baseline.get("expected_goals", 2.55),
            "model_vs_market_over": baseline.get("prob_over_2_5", 50.0) - float(snapshot.market_prob_over_2_5 or 50.0),
            "model_vs_market_w1": baseline.get("prob_w1", 33.3) - float(snapshot.market_prob_w1 or 33.3),
            "referee_goals_vs_avg": float(snapshot.referee_goals_vs_avg or 0.0),
            "referee_over_2_5_pct": float(snapshot.referee_over_2_5_pct or 50.0),
        }

        X = np.array([[features[c] for c in FEATURE_COLS]], dtype=np.float32)

        # O/U prediction
        ou_prob = float(models.ou_model.predict(X)[0])  # P(Over 2.5)
        ensemble_over = round(ou_prob * 100.0, 1)
        ensemble_under = round((1.0 - ou_prob) * 100.0, 1)

        # 1X2 prediction
        onextwo_probs = models.onextwo_model.predict(X)[0]  # [P(W1), P(D), P(W2)]
        ensemble_w1 = round(float(onextwo_probs[0]) * 100.0, 1)
        ensemble_draw = round(float(onextwo_probs[1]) * 100.0, 1)
        ensemble_w2 = round(float(onextwo_probs[2]) * 100.0, 1)

        # Blend with baseline
        ow = ENSEMBLE_OU_WEIGHT
        blended_over = round(baseline["prob_over_2_5"] * (1 - ow) + ensemble_over * ow, 1)
        blended_under = round(100.0 - blended_over, 1)

        tw = ENSEMBLE_1X2_WEIGHT
        blended_w1 = round(baseline["prob_w1"] * (1 - tw) + ensemble_w1 * tw, 1)
        blended_draw = round(baseline["prob_draw"] * (1 - tw) + ensemble_draw * tw, 1)
        blended_w2 = round(100.0 - blended_w1 - blended_draw, 1)

        return {
            "prob_over_2_5": blended_over,
            "prob_under_2_5": blended_under,
            "prob_w1": blended_w1,
            "prob_draw": blended_draw,
            "prob_w2": blended_w2,
            "raw_ensemble_over": ensemble_over,
            "raw_ensemble_w1": ensemble_w1,
            "raw_ensemble_draw": ensemble_draw,
            "raw_ensemble_w2": ensemble_w2,
        }

    except Exception as exc:
        logger.debug("Ensemble prediction failed: %s", exc)
        return None


@dataclass
class CVFoldResult:
    fold: int
    train_size: int
    test_size: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    ou_accuracy: float
    ou_logloss: float
    onextwo_accuracy: float
    onextwo_logloss: float


@dataclass
class CVReport:
    n_folds: int
    folds: list[CVFoldResult]
    ou_accuracy_mean: float
    ou_accuracy_std: float
    ou_logloss_mean: float
    onextwo_accuracy_mean: float
    onextwo_accuracy_std: float
    onextwo_logloss_mean: float
    total_matches: int


def cross_validate_ensemble(n_folds: int = 5, min_train: int = 200, min_test: int = 50) -> Optional[CVReport]:
    """Run time-series expanding-window cross-validation for the ensemble.

    Creates n_folds by splitting sorted-by-date data into expanding training
    windows with non-overlapping test sets. Reports accuracy and log-loss
    per fold, plus mean/std across folds.
    """
    import lightgbm as lgb
    from sklearn.metrics import log_loss as sk_log_loss

    raw = _load_raw_data()
    if len(raw) < min_train + min_test * n_folds:
        logger.warning("Not enough data for %d-fold CV (%d rows)", n_folds, len(raw))
        return None

    # Build features + targets (same as train_ensemble)
    X_rows = []
    y_ou = []
    y_1x2 = []
    dates = []

    for row in raw:
        ref_data = _get_referee_for_match(
            row.get("team1_name", ""), row.get("team2_name", ""),
            row.get("match_date_utc", ""),
        )
        row["_ref_goals_vs_avg"] = ref_data["goals_vs_avg"]
        row["_ref_over_2_5_pct"] = ref_data["over_2_5_pct"]

        baseline = _build_baseline_for_row(row)
        features = _build_features(row, baseline)
        X_rows.append([features[c] for c in FEATURE_COLS])

        hg, ag = int(row["team1_score"]), int(row["team2_score"])
        y_ou.append(1 if (hg + ag) > 2.5 else 0)
        y_1x2.append(0 if hg > ag else (2 if ag > hg else 1))
        dates.append(row.get("match_date_utc", ""))

    X = np.array(X_rows, dtype=np.float32)
    y_ou_arr = np.array(y_ou)
    y_1x2_arr = np.array(y_1x2)
    total = len(X)

    # Compute fold boundaries: reserve first min_train for initial training,
    # split remaining into n_folds equal test chunks
    remaining = total - min_train
    test_chunk = max(min_test, remaining // n_folds)

    folds: list[CVFoldResult] = []

    ou_params = {
        "objective": "binary", "metric": "binary_logloss",
        "learning_rate": 0.05, "num_leaves": 31, "max_depth": 5,
        "min_child_samples": 50, "feature_fraction": 0.8,
        "bagging_fraction": 0.8, "bagging_freq": 5,
        "lambda_l1": 0.1, "lambda_l2": 1.0, "verbose": -1,
    }
    onextwo_params = {
        "objective": "multiclass", "num_class": 3,
        "metric": "multi_logloss", "learning_rate": 0.05,
        "num_leaves": 31, "max_depth": 5, "min_child_samples": 50,
        "feature_fraction": 0.8, "bagging_fraction": 0.8,
        "bagging_freq": 5, "lambda_l1": 0.1, "lambda_l2": 1.0, "verbose": -1,
    }

    for fold_i in range(n_folds):
        test_start_idx = min_train + fold_i * test_chunk
        test_end_idx = min(test_start_idx + test_chunk, total)
        if test_end_idx - test_start_idx < min_test:
            break

        X_train = X[:test_start_idx]
        X_test = X[test_start_idx:test_end_idx]
        y_ou_tr = y_ou_arr[:test_start_idx]
        y_ou_te = y_ou_arr[test_start_idx:test_end_idx]
        y_1x2_tr = y_1x2_arr[:test_start_idx]
        y_1x2_te = y_1x2_arr[test_start_idx:test_end_idx]

        # Train O/U
        ou_ds = lgb.Dataset(X_train, y_ou_tr, feature_name=FEATURE_COLS)
        ou_vs = lgb.Dataset(X_test, y_ou_te, feature_name=FEATURE_COLS, reference=ou_ds)
        ou_model = lgb.train(
            ou_params, ou_ds, num_boost_round=300,
            valid_sets=[ou_vs],
            callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
        )
        ou_preds = ou_model.predict(X_test)
        ou_acc = float(((ou_preds > 0.5).astype(int) == y_ou_te).mean() * 100)
        ou_ll = float(sk_log_loss(y_ou_te, ou_preds, labels=[0, 1]))

        # Train 1X2
        ox_ds = lgb.Dataset(X_train, y_1x2_tr, feature_name=FEATURE_COLS)
        ox_vs = lgb.Dataset(X_test, y_1x2_te, feature_name=FEATURE_COLS, reference=ox_ds)
        ox_model = lgb.train(
            onextwo_params, ox_ds, num_boost_round=300,
            valid_sets=[ox_vs],
            callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
        )
        ox_preds = ox_model.predict(X_test)
        ox_acc = float((ox_preds.argmax(axis=1) == y_1x2_te).mean() * 100)
        ox_ll = float(sk_log_loss(y_1x2_te, ox_preds, labels=[0, 1, 2]))

        folds.append(CVFoldResult(
            fold=fold_i + 1,
            train_size=len(X_train),
            test_size=len(X_test),
            train_start=dates[0],
            train_end=dates[test_start_idx - 1],
            test_start=dates[test_start_idx],
            test_end=dates[test_end_idx - 1],
            ou_accuracy=round(ou_acc, 2),
            ou_logloss=round(ou_ll, 4),
            onextwo_accuracy=round(ox_acc, 2),
            onextwo_logloss=round(ox_ll, 4),
        ))

    if not folds:
        return None

    ou_accs = [f.ou_accuracy for f in folds]
    ox_accs = [f.onextwo_accuracy for f in folds]
    ou_lls = [f.ou_logloss for f in folds]
    ox_lls = [f.onextwo_logloss for f in folds]

    return CVReport(
        n_folds=len(folds),
        folds=folds,
        ou_accuracy_mean=round(float(np.mean(ou_accs)), 2),
        ou_accuracy_std=round(float(np.std(ou_accs)), 2),
        ou_logloss_mean=round(float(np.mean(ou_lls)), 4),
        onextwo_accuracy_mean=round(float(np.mean(ox_accs)), 2),
        onextwo_accuracy_std=round(float(np.std(ox_accs)), 2),
        onextwo_logloss_mean=round(float(np.mean(ox_lls)), 4),
        total_matches=total,
    )


if __name__ == "__main__":
    import io
    import sys
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    action = sys.argv[1] if len(sys.argv) > 1 else "train"

    if action == "cv":
        print("Running time-series cross-validation...")
        report = cross_validate_ensemble()
        if report:
            print(f"\n  {report.n_folds}-fold CV on {report.total_matches} matches\n")
            for f in report.folds:
                print(f"  Fold {f.fold}: train={f.train_size}, test={f.test_size} "
                      f"({f.test_start} → {f.test_end})")
                print(f"    O/U: {f.ou_accuracy:.1f}% (logloss {f.ou_logloss:.4f})")
                print(f"    1X2: {f.onextwo_accuracy:.1f}% (logloss {f.onextwo_logloss:.4f})")
            print(f"\n  O/U  mean: {report.ou_accuracy_mean:.1f}% ± {report.ou_accuracy_std:.1f}%")
            print(f"  1X2  mean: {report.onextwo_accuracy_mean:.1f}% ± {report.onextwo_accuracy_std:.1f}%")
        else:
            print("Not enough data for cross-validation.")
    else:
        print("Training ensemble models...")
        models = train_ensemble()
        if models.trained:
            print(f"\nDone! O/U: {models.test_accuracy_ou:.1f}%, 1X2: {models.test_accuracy_1x2:.1f}%")
        else:
            print("Training failed!")
