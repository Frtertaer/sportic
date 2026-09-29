"""
Out-of-sample Elo backtest using local historical_matches DB.

Train: matches before 2024-08-01
Test:  matches from 2024-08-01 onward (current season)

Measures:
  - 1X2 accuracy (Elo-only prediction vs actual result)
  - O/U 2.5 accuracy (Elo expected goals vs actual total)
  - Brier score for 1X2 calibration
  - Log-loss for probability quality
  - ROI simulation at various thresholds

Usage:
    python scripts/backtest_elo.py
"""
from __future__ import annotations

import io
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from elo import DEFAULT_ELO, EloSystem, _normalize_team_name

DB_PATH = Path(__file__).parent.parent / "sportsbot.db"
TRAIN_CUTOFF = "2024-08-01"


@dataclass
class BacktestResult:
    total: int = 0
    correct_1x2: int = 0
    correct_ou: int = 0
    brier_sum: float = 0.0
    log_loss_sum: float = 0.0
    bets_placed_confident: int = 0
    staked_confident: float = 0.0
    returned_confident: float = 0.0
    bets_placed_value: int = 0
    staked_value: float = 0.0
    returned_value: float = 0.0
    league_results: dict = field(default_factory=lambda: defaultdict(lambda: {"total": 0, "correct_1x2": 0, "correct_ou": 0}))
    confident_correct: int = 0
    confident_total: int = 0
    high_conf_correct: int = 0
    high_conf_total: int = 0


def load_matches() -> tuple[list[dict], list[dict]]:
    """Load matches from DB, split into train and test."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT team1_name, team2_name, team1_score, team2_score,
               match_date_utc, competition_name, actual_result
        FROM historical_matches
        WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL
              AND actual_result IS NOT NULL
        ORDER BY match_date_utc
    """).fetchall()
    conn.close()

    train, test = [], []
    for r in rows:
        m = {
            "home": _normalize_team_name(r["team1_name"]),
            "away": _normalize_team_name(r["team2_name"]),
            "hg": int(r["team1_score"]),
            "ag": int(r["team2_score"]),
            "date": r["match_date_utc"] or "",
            "league": r["competition_name"] or "",
            "actual": r["actual_result"],
        }
        if m["date"] < TRAIN_CUTOFF:
            train.append(m)
        else:
            test.append(m)

    return train, test


def predict_match(elo: EloSystem, home: str, away: str) -> dict:
    """Make prediction using Elo system."""
    pw1, pd, pw2 = elo.win_probability(home, away, is_home=True)
    xg_adj = elo.elo_xg_adjustment(home, away, is_home=True)
    expected_goals = 2.6 + xg_adj

    if pw1 > pw2 and pw1 > pd:
        predicted = "W1"
    elif pw2 > pw1 and pw2 > pd:
        predicted = "W2"
    else:
        predicted = "D"

    return {
        "pw1": pw1, "pd": pd, "pw2": pw2,
        "predicted": predicted,
        "expected_goals": expected_goals,
        "confidence": max(pw1, pd, pw2),
    }


def actual_result(hg: int, ag: int) -> str:
    if hg > ag: return "W1"
    elif ag > hg: return "W2"
    return "D"


def brier_score(pw1: float, pd: float, pw2: float, actual: str) -> float:
    y1 = 1.0 if actual == "W1" else 0.0
    yd = 1.0 if actual == "D" else 0.0
    y2 = 1.0 if actual == "W2" else 0.0
    return (pw1 - y1) ** 2 + (pd - yd) ** 2 + (pw2 - y2) ** 2


def log_loss_single(prob: float) -> float:
    prob = max(0.001, min(0.999, prob))
    return -math.log(prob)


def run_backtest():
    print("=" * 70)
    print("  ELO OUT-OF-SAMPLE BACKTEST")
    print(f"  Train: < {TRAIN_CUTOFF}  |  Test: >= {TRAIN_CUTOFF}")
    print("=" * 70)

    # Phase 1: Load data
    print("\n[1/3] Loading data from local DB...")
    train, test = load_matches()
    print(f"  Training matches: {len(train)}")
    print(f"  Test matches:     {len(test)}")

    if not test:
        print("ERROR: No test matches found!")
        return

    # Phase 2: Train Elo on historical data
    print("\n[2/3] Training Elo system...")
    elo = EloSystem()
    for m in train:
        elo.update(m["home"], m["away"], m["hg"], m["ag"], m["date"], m["league"])

    # Apply season reversion before test
    elo.apply_season_reversion()
    print(f"  Teams after training: {len(elo.ratings)}")
    print(f"  Top 10:")
    for team, rating in elo.top_teams(10):
        print(f"    {team:30s} {rating:.1f}")

    # Phase 3: Predict and evaluate
    print("\n[3/3] Running predictions on test set...")
    result = BacktestResult()

    for m in test:
        pred = predict_match(elo, m["home"], m["away"])
        actual = actual_result(m["hg"], m["ag"])
        total_goals = m["hg"] + m["ag"]
        league = m["league"]

        result.total += 1

        # 1X2 accuracy
        if pred["predicted"] == actual:
            result.correct_1x2 += 1
            result.league_results[league]["correct_1x2"] += 1
        result.league_results[league]["total"] += 1

        # O/U 2.5 accuracy
        pred_over = pred["expected_goals"] > 2.5
        actual_over = total_goals > 2.5
        if pred_over == actual_over:
            result.correct_ou += 1
            result.league_results[league]["correct_ou"] += 1

        # Brier score
        result.brier_sum += brier_score(pred["pw1"], pred["pd"], pred["pw2"], actual)

        # Log loss
        prob_actual = {"W1": pred["pw1"], "D": pred["pd"], "W2": pred["pw2"]}[actual]
        result.log_loss_sum += log_loss_single(prob_actual)

        # Confidence buckets
        if pred["confidence"] > 0.45:
            result.confident_total += 1
            if pred["predicted"] == actual:
                result.confident_correct += 1

        if pred["confidence"] > 0.55:
            result.high_conf_total += 1
            if pred["predicted"] == actual:
                result.high_conf_correct += 1

        # ROI simulation 1: confident bets at ~1.85 odds
        if pred["confidence"] > 0.45:
            result.bets_placed_confident += 1
            result.staked_confident += 1.0
            if pred["predicted"] == actual:
                fair_odds = 1.0 / pred["confidence"]
                bookie_odds = fair_odds * 0.93
                result.returned_confident += bookie_odds

        # ROI simulation 2: value bets (Elo says >50% but typical odds suggest lower)
        if pred["confidence"] > 0.50:
            implied_odds = 1.0 / pred["confidence"]
            # Assume bookie offers ~10% worse odds
            bookie_odds = implied_odds * 0.90
            result.bets_placed_value += 1
            result.staked_value += 1.0
            if pred["predicted"] == actual:
                result.returned_value += bookie_odds

        # Update Elo with actual result (rolling — simulates live updates)
        elo.update(m["home"], m["away"], m["hg"], m["ag"], m["date"], league)

    # Print results
    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    acc_1x2 = result.correct_1x2 / result.total * 100 if result.total else 0
    acc_ou = result.correct_ou / result.total * 100 if result.total else 0
    brier = result.brier_sum / result.total if result.total else 0
    log_loss = result.log_loss_sum / result.total if result.total else 0

    print(f"\n  Total test matches:  {result.total}")
    print(f"  1X2 accuracy:        {result.correct_1x2}/{result.total} = {acc_1x2:.1f}%")
    print(f"  O/U 2.5 accuracy:    {result.correct_ou}/{result.total} = {acc_ou:.1f}%")
    print(f"  Avg Brier score:     {brier:.4f}  (random=0.667, market~0.42)")
    print(f"  Avg Log loss:        {log_loss:.4f}  (random=1.099, market~0.95)")

    if result.confident_total:
        conf_acc = result.confident_correct / result.confident_total * 100
        print(f"\n  Confident (>45%):    {result.confident_correct}/{result.confident_total} = {conf_acc:.1f}%")

    if result.high_conf_total:
        hc_acc = result.high_conf_correct / result.high_conf_total * 100
        print(f"  High-conf (>55%):    {result.high_conf_correct}/{result.high_conf_total} = {hc_acc:.1f}%")

    if result.bets_placed_confident:
        roi = (result.returned_confident - result.staked_confident) / result.staked_confident * 100
        print(f"\n  ROI (conf>45%, 7% margin):")
        print(f"    Bets={result.bets_placed_confident}, ROI={roi:+.1f}%")

    if result.bets_placed_value:
        roi = (result.returned_value - result.staked_value) / result.staked_value * 100
        print(f"  ROI (conf>50%, 10% margin):")
        print(f"    Bets={result.bets_placed_value}, ROI={roi:+.1f}%")

    print(f"\n  Per-league 1X2 accuracy:")
    for league, data in sorted(result.league_results.items(), key=lambda x: -x[1]["total"]):
        n = data["total"]
        c = data["correct_1x2"]
        co = data["correct_ou"]
        pct = c / n * 100 if n else 0
        pct_ou = co / n * 100 if n else 0
        print(f"    {league:35s} 1X2={c:3d}/{n:3d}={pct:5.1f}%  O/U={co:3d}/{n:3d}={pct_ou:5.1f}%")

    print(f"\n  Final top 10 after full backtest:")
    for team, rating in elo.top_teams(10):
        print(f"    {team:30s} {rating:.1f}")

    print("\n" + "=" * 70)
    print("  BENCHMARKS:")
    print(f"    Random:           33.3% (1X2), 50.0% (O/U)")
    print(f"    Home-always:     ~46.0% (1X2)")
    print(f"    Market consensus: ~52% (1X2), ~55% (O/U)")
    print(f"    OUR ELO MODEL:    {acc_1x2:.1f}% (1X2), {acc_ou:.1f}% (O/U)")
    if acc_1x2 > 46:
        print(f"    >> Beats home-always by +{acc_1x2 - 46:.1f}pp")
    if acc_1x2 > 50:
        print(f"    >> Approaching market-level accuracy!")
    print("=" * 70)


if __name__ == "__main__":
    run_backtest()
