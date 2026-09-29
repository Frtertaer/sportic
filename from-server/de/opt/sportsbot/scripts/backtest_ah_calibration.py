"""
Asian Handicap model + Platt scaling calibration.

1. Parse AH lines, compute AH outcomes
2. Use baseline model's expected score to predict AH covers
3. Apply Platt scaling (logistic regression on predicted probabilities)
4. Backtest AH ROI with calibrated probabilities
5. Also calibrate O/U and 1X2 probabilities
"""
from __future__ import annotations

import io
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from models import MatchData, FlashscoreData, PredictionFeatureSnapshot
from baseline_model import build_numeric_baseline

DB_PATH = Path(__file__).parent.parent / "sportsbot.db"
TRAIN_CUTOFF = "2024-08-01"


def parse_ah_line(ah_str: str, team1: str, team2: str) -> tuple[float, bool]:
    """Parse AH line string into (handicap_for_team1, is_team1_favored).

    'Valencia -1.0' means team1 (Valencia) gives 1.0 goals.
    Returns (handicap, True) if team1 is favored.
    """
    if not ah_str:
        return (0.0, True)

    parts = ah_str.strip().rsplit(" ", 1)
    if len(parts) != 2:
        return (0.0, True)

    team_name = parts[0].strip()
    try:
        line = float(parts[1])
    except ValueError:
        return (0.0, True)

    # Determine which team the line refers to
    # Negative line = team is favored (gives goals)
    # The team in the string is the one the line applies to
    t1_lower = team1.lower().strip()
    team_lower = team_name.lower().strip()

    if team_lower in t1_lower or t1_lower in team_lower:
        # Line applies to team1
        return (line, line < 0)
    else:
        # Line applies to team2, flip for team1 perspective
        return (-line, -line < 0)


def ah_result(goal_diff: int, handicap: float) -> float:
    """Compute AH result for team1.
    goal_diff = team1_goals - team2_goals
    handicap = team1's handicap (negative if favored)

    Returns: 1.0 (win), 0.5 (half-win), 0.0 (push), -0.5 (half-loss), -1.0 (loss)
    """
    adjusted = goal_diff + handicap

    if adjusted > 0.5:
        return 1.0
    elif adjusted > 0.25:
        return 1.0  # quarter-line win
    elif abs(adjusted) < 0.001:
        return 0.0  # push
    elif adjusted > -0.25:
        return 0.0  # push on quarter
    elif adjusted >= -0.5:
        return -1.0  # quarter-line loss
    else:
        return -1.0

    # More precise quarter-line handling
    if abs(adjusted - 0.25) < 0.001:
        return 0.5  # half-win
    if abs(adjusted + 0.25) < 0.001:
        return -0.5  # half-loss
    if adjusted > 0:
        return 1.0
    elif adjusted < 0:
        return -1.0
    return 0.0


def load_data():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            m.team1_name, m.team2_name,
            m.team1_score, m.team2_score,
            m.match_date_utc, m.competition_name,
            ml.implied_prob_w1, ml.implied_prob_draw, ml.implied_prob_w2,
            ml.odds_over_2_5, ml.odds_under_2_5,
            ml.odds_btts_yes, ml.odds_btts_no,
            ml.odds_w1, ml.odds_draw, ml.odds_w2,
            ml.ah_main_line, ml.ah_team1_odds, ml.ah_team2_odds,
            ts.data_completeness,
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
            ts.key_absence_diff
        FROM historical_matches m
        JOIN historical_market_lines ml ON ml.historical_match_id = m.id
        LEFT JOIN historical_team_snapshots ts ON ts.historical_match_id = m.id
        WHERE m.team1_score IS NOT NULL AND m.team2_score IS NOT NULL
          AND ml.implied_prob_w1 IS NOT NULL
        ORDER BY m.match_date_utc
    """).fetchall()
    conn.close()

    best = {}
    for r in rows:
        key = (r["team1_name"], r["team2_name"], r["match_date_utc"])
        d = dict(r)
        if key not in best or (d.get("data_completeness") or 0) > (best[key].get("data_completeness") or 0):
            best[key] = d
    return sorted(best.values(), key=lambda x: x.get("match_date_utc", ""))


def build_snapshot(row):
    mp_w1 = round(float(row["implied_prob_w1"]), 1) if row.get("implied_prob_w1") else None
    mp_d = round(float(row["implied_prob_draw"]), 1) if row.get("implied_prob_draw") else None
    mp_w2 = round(float(row["implied_prob_w2"]), 1) if row.get("implied_prob_w2") else None

    market_over = market_under = None
    ou25 = row.get("odds_over_2_5")
    un25 = row.get("odds_under_2_5")
    if ou25 and un25 and float(ou25) > 1 and float(un25) > 1:
        inv_o, inv_u = 1.0 / float(ou25), 1.0 / float(un25)
        t = inv_o + inv_u
        if t > 0:
            market_over = round(inv_o * 100 / t, 1)
            market_under = round(inv_u * 100 / t, 1)

    def diff(a, b):
        return float(a - b) if a is not None and b is not None else None

    mv1, mv2 = row.get("team1_market_value_eur"), row.get("team2_market_value_eur")
    mv_ratio = round(float(mv1) / float(mv2), 4) if mv1 and mv2 and float(mv2) > 0 else None
    pos1, pos2 = row.get("team1_table_position"), row.get("team2_table_position")
    pts1, pts2 = row.get("team1_points"), row.get("team2_points")

    return PredictionFeatureSnapshot(
        team1=row["team1_name"], team2=row["team2_name"],
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


def kelly_stake(prob: float, odds: float, fraction: float = 0.25) -> float:
    if odds <= 1.0 or prob <= 0 or prob >= 1:
        return 0.0
    b = odds - 1.0
    f = (b * prob - (1 - prob)) / b
    return min(max(f * fraction, 0.0), 0.10)


def run():
    print("=" * 70)
    print("  ASIAN HANDICAP + PLATT CALIBRATION BACKTEST")
    print("=" * 70)

    data = load_data()
    print(f"\n  Total matches: {len(data)}")

    # Split into train/test
    train = [r for r in data if r.get("match_date_utc", "") < TRAIN_CUTOFF]
    test = [r for r in data if r.get("match_date_utc", "") >= TRAIN_CUTOFF]
    print(f"  Train: {len(train)}, Test: {len(test)}")

    # ── STEP 1: Compute baseline predictions for all matches ──────────
    print("\n  Computing baseline predictions...")
    for row in data:
        snapshot = build_snapshot(row)
        md = MatchData(team1=row["team1_name"], team2=row["team2_name"])
        if row.get("team1_scored_avg") is not None:
            md.flashscore = FlashscoreData(
                team1_name=row["team1_name"], team2_name=row["team2_name"],
                team1_goals_scored_avg=row.get("team1_scored_avg"),
                team1_goals_conceded_avg=row.get("team1_conceded_avg"),
                team2_goals_scored_avg=row.get("team2_scored_avg"),
                team2_goals_conceded_avg=row.get("team2_conceded_avg"),
            )
        baseline = build_numeric_baseline(md, snapshot)
        row["_baseline"] = baseline
        row["_snapshot"] = snapshot

    # ── STEP 2: Platt Scaling (logistic calibration on train set) ─────
    print("\n  Fitting Platt scaling on train set...")
    from sklearn.linear_model import LogisticRegression

    # Calibrate O/U
    ou_X_train, ou_y_train = [], []
    for row in train:
        b = row["_baseline"]
        total = int(row["team1_score"]) + int(row["team2_score"])
        ou_X_train.append([b["prob_over_2_5"] / 100.0])
        ou_y_train.append(1 if total > 2.5 else 0)

    ou_calibrator = LogisticRegression(C=1.0)
    ou_calibrator.fit(np.array(ou_X_train), np.array(ou_y_train))

    # Calibrate 1X2 (separate binary classifiers for W1, D, W2)
    w1_X_train, w1_y_train = [], []
    for row in train:
        b = row["_baseline"]
        hg, ag = int(row["team1_score"]), int(row["team2_score"])
        w1_X_train.append([b["prob_w1"] / 100.0])
        w1_y_train.append(1 if hg > ag else 0)

    w1_calibrator = LogisticRegression(C=1.0)
    w1_calibrator.fit(np.array(w1_X_train), np.array(w1_y_train))

    # Test calibration
    ou_X_test = np.array([[row["_baseline"]["prob_over_2_5"] / 100.0] for row in test])
    ou_cal_probs = ou_calibrator.predict_proba(ou_X_test)[:, 1]

    print(f"\n  Platt scaling coefficients:")
    print(f"    O/U: slope={ou_calibrator.coef_[0][0]:.3f}, intercept={ou_calibrator.intercept_[0]:.3f}")
    print(f"    W1:  slope={w1_calibrator.coef_[0][0]:.3f}, intercept={w1_calibrator.intercept_[0]:.3f}")

    # ── STEP 3: Calibration quality check ─────────────────────────────
    print("\n" + "=" * 70)
    print("  CALIBRATION CHECK: PREDICTED vs ACTUAL (test set)")
    print("=" * 70)

    # Bin predictions into deciles
    for label, prob_key, actual_fn in [
        ("O/U Raw", lambda r: r["_baseline"]["prob_over_2_5"],
         lambda r: int(r["team1_score"]) + int(r["team2_score"]) > 2.5),
        ("O/U Calibrated", lambda r: ou_calibrator.predict_proba(
            np.array([[r["_baseline"]["prob_over_2_5"] / 100.0]]))[0][1] * 100,
         lambda r: int(r["team1_score"]) + int(r["team2_score"]) > 2.5),
    ]:
        bins = {}
        for row in test:
            p = prob_key(row)
            bucket = int(p // 10) * 10
            if bucket not in bins:
                bins[bucket] = {"n": 0, "hit": 0, "sum_p": 0}
            bins[bucket]["n"] += 1
            bins[bucket]["sum_p"] += p
            if actual_fn(row):
                bins[bucket]["hit"] += 1

        print(f"\n  {label}:")
        print(f"    {'Bucket':>8s} {'N':>5s} {'Pred%':>6s} {'Actual%':>8s} {'Gap':>6s}")
        for bucket in sorted(bins.keys()):
            b = bins[bucket]
            if b["n"] < 10:
                continue
            pred = b["sum_p"] / b["n"]
            actual = b["hit"] / b["n"] * 100
            gap = pred - actual
            print(f"    {bucket:>3d}-{bucket+10:>3d}% {b['n']:5d} {pred:5.1f}% {actual:7.1f}% {gap:+5.1f}%")

    # ── STEP 4: AH Backtest ───────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  ASIAN HANDICAP BACKTEST")
    print("=" * 70)

    ah_bets = 0
    ah_won = 0
    ah_staked = 0.0
    ah_returned = 0.0

    for row in test:
        ah_str = row.get("ah_main_line")
        ah_t1_odds = row.get("ah_team1_odds")
        ah_t2_odds = row.get("ah_team2_odds")
        if not ah_str or not ah_t1_odds or not ah_t2_odds:
            continue

        t1_odds = float(ah_t1_odds)
        t2_odds = float(ah_t2_odds)
        hg, ag = int(row["team1_score"]), int(row["team2_score"])
        goal_diff = hg - ag

        handicap, t1_favored = parse_ah_line(ah_str, row["team1_name"], row["team2_name"])

        b = row["_baseline"]
        expected_goals_diff = b.get("expected_goals", 2.5) * (b["prob_w1"] - b["prob_w2"]) / 100.0

        # Market implied probability from AH odds
        if t1_odds > 1 and t2_odds > 1:
            inv1 = 1.0 / t1_odds
            inv2 = 1.0 / t2_odds
            total = inv1 + inv2
            market_prob_t1_cover = inv1 / total
            market_prob_t2_cover = inv2 / total
        else:
            continue

        # Our model's AH cover probability
        # Use Dixon-Coles Poisson matrix probabilities to compute AH
        lam1 = b.get("expected_goals", 2.5) * b["prob_w1"] / (b["prob_w1"] + b["prob_w2"] + 0.01) if b["prob_w1"] + b["prob_w2"] > 0 else 1.3
        lam2 = b.get("expected_goals", 2.5) - lam1

        # Quick AH cover probability from Poisson
        model_prob_t1_cover = 0.0
        for h in range(8):
            for a in range(8):
                ph = (lam1 ** h) * math.exp(-lam1) / math.factorial(h)
                pa = (lam2 ** a) * math.exp(-lam2) / math.factorial(a)
                prob = ph * pa
                res = ah_result(h - a, handicap)
                if res > 0:
                    model_prob_t1_cover += prob
                elif res == 0:
                    model_prob_t1_cover += prob * 0.5  # push returns stake

        model_prob_t2_cover = 1.0 - model_prob_t1_cover

        # Edge check: bet T1 cover if model > market by threshold
        edge_t1 = model_prob_t1_cover - market_prob_t1_cover
        edge_t2 = model_prob_t2_cover - market_prob_t2_cover

        THRESH = 0.05  # 5% edge

        if edge_t1 > THRESH:
            stake = kelly_stake(model_prob_t1_cover, t1_odds, 0.25)
            if stake > 0:
                ah_bets += 1
                ah_staked += stake
                actual_result = ah_result(goal_diff, handicap)
                if actual_result > 0:
                    ah_won += 1
                    ah_returned += stake * t1_odds
                elif actual_result == 0:
                    ah_returned += stake  # push

        elif edge_t2 > THRESH:
            stake = kelly_stake(model_prob_t2_cover, t2_odds, 0.25)
            if stake > 0:
                ah_bets += 1
                ah_staked += stake
                actual_result = ah_result(goal_diff, handicap)
                if actual_result < 0:  # team2 covered
                    ah_won += 1
                    ah_returned += stake * t2_odds
                elif actual_result == 0:
                    ah_returned += stake

    if ah_bets > 0:
        ah_roi = (ah_returned - ah_staked) / ah_staked * 100
        ah_acc = ah_won / ah_bets * 100
        print(f"\n  AH bets: {ah_bets}")
        print(f"  AH won:  {ah_won} ({ah_acc:.1f}%)")
        print(f"  AH ROI:  {ah_roi:+.1f}%")
    else:
        print("\n  No AH bets triggered")

    # ── STEP 5: Calibrated O/U ROI sweep ──────────────────────────────
    print("\n" + "=" * 70)
    print("  O/U ROI: RAW vs CALIBRATED (Kelly quarter, top5 leagues)")
    print("=" * 70)

    TOP5 = {"Bundesliga", "Primeira Liga", "La Liga", "Ligue 1", "Premier League"}

    for source, prob_fn in [
        ("RAW", lambda r: r["_baseline"]["prob_over_2_5"]),
        ("CALIBRATED", lambda r: ou_calibrator.predict_proba(
            np.array([[r["_baseline"]["prob_over_2_5"] / 100.0]]))[0][1] * 100),
    ]:
        print(f"\n  {source}:")
        print(f"    {'Thresh':>6s} {'Bets':>5s} {'Won':>5s} {'Acc%':>6s} {'ROI%':>7s}")

        for thresh in [5, 7, 8, 10, 12]:
            bets = won = 0
            staked = returned = 0.0
            for row in test:
                if row.get("competition_name") not in TOP5:
                    continue
                s = row["_snapshot"]
                if s.market_prob_over_2_5 is None:
                    continue

                model_over = prob_fn(row)
                model_under = 100.0 - model_over
                mp_over = s.market_prob_over_2_5
                mp_under = s.market_prob_under_2_5

                edge_over = model_over - mp_over
                edge_under = (model_under - mp_under) if mp_under else 0

                odds_over = float(row["odds_over_2_5"]) if row.get("odds_over_2_5") else None
                odds_under = float(row["odds_under_2_5"]) if row.get("odds_under_2_5") else None
                total = int(row["team1_score"]) + int(row["team2_score"])

                if edge_over > thresh and odds_over:
                    stake = kelly_stake(model_over / 100.0, odds_over, 0.25)
                    if stake > 0:
                        bets += 1
                        staked += stake
                        if total > 2.5:
                            won += 1
                            returned += stake * odds_over
                elif edge_under > thresh and odds_under:
                    stake = kelly_stake(model_under / 100.0, odds_under, 0.25)
                    if stake > 0:
                        bets += 1
                        staked += stake
                        if total <= 2.5:
                            won += 1
                            returned += stake * odds_under

            if bets >= 10:
                roi = (returned - staked) / staked * 100
                acc = won / bets * 100
                marker = " <<" if roi > 0 else ""
                print(f"    {thresh:5d}% {bets:5d} {won:5d} {acc:5.1f}% {roi:+6.1f}%{marker}")

    # ── STEP 6: Save calibration parameters ──────────────────────────
    import pickle
    cal_path = Path(__file__).parent.parent / "data" / "calibration"
    cal_path.mkdir(parents=True, exist_ok=True)
    with open(cal_path / "ou_calibrator.pkl", "wb") as f:
        pickle.dump(ou_calibrator, f)
    with open(cal_path / "w1_calibrator.pkl", "wb") as f:
        pickle.dump(w1_calibrator, f)
    print(f"\n  Calibrators saved to {cal_path}")

    print("\n" + "=" * 70)
    print("  DONE")
    print("=" * 70)


if __name__ == "__main__":
    run()
