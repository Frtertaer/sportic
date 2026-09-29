"""
Out-of-sample backtest of the FULL numeric pipeline.

Uses historical_matches + historical_market_lines + historical_team_snapshots
to reconstruct PredictionFeatureSnapshot, runs build_numeric_baseline(),
and evaluates against actual results.

Train period: < 2024-08-01 (used for Elo training only)
Test period:  >= 2024-08-01 (predictions evaluated here)

Usage:
    python scripts/backtest_full_pipeline.py
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

from models import MatchData, FlashscoreData, OddsData, PredictionFeatureSnapshot
from baseline_model import build_numeric_baseline

DB_PATH = Path(__file__).parent.parent / "sportsbot.db"
TRAIN_CUTOFF = "2024-08-01"


@dataclass
class Results:
    total: int = 0
    # 1X2
    correct_1x2: int = 0
    correct_1x2_confident: int = 0
    total_confident: int = 0
    # O/U 2.5
    correct_ou: int = 0
    correct_ou_confident: int = 0
    total_ou_confident: int = 0
    # BTTS
    correct_btts: int = 0
    correct_btts_confident: int = 0
    total_btts_confident: int = 0
    # Calibration
    brier_sum: float = 0.0
    log_loss_sum: float = 0.0
    # ROI
    ou_bets: int = 0
    ou_staked: float = 0.0
    ou_returned: float = 0.0
    # Per-league
    league: dict = field(default_factory=lambda: defaultdict(lambda: {
        "n": 0, "c1x2": 0, "cou": 0, "cbtts": 0,
    }))
    # With vs without splits
    with_splits: int = 0
    without_splits: int = 0
    correct_ou_with_splits: int = 0
    correct_ou_without_splits: int = 0
    total_with_splits: int = 0
    total_without_splits: int = 0


def load_test_data() -> list[dict]:
    """Load test matches with odds and team snapshots."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT
            m.id AS match_id,
            m.team1_name, m.team2_name,
            m.team1_score, m.team2_score,
            m.actual_result,
            m.match_date_utc,
            m.competition_name,
            ml.implied_prob_w1, ml.implied_prob_draw, ml.implied_prob_w2,
            ml.odds_over_2_5, ml.odds_under_2_5,
            ml.odds_btts_yes, ml.odds_btts_no,
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
            ts.rest_gap_hours, ts.team1_short_rest, ts.team2_short_rest,
            ts.team1_market_value_eur, ts.team2_market_value_eur,
            ts.key_absence_diff
        FROM historical_matches m
        JOIN historical_market_lines ml ON ml.historical_match_id = m.id
        LEFT JOIN historical_team_snapshots ts ON ts.historical_match_id = m.id
        WHERE m.match_date_utc >= ?
          AND m.team1_score IS NOT NULL
          AND m.team2_score IS NOT NULL
          AND ml.implied_prob_w1 IS NOT NULL
        ORDER BY m.match_date_utc
    """, (TRAIN_CUTOFF,)).fetchall()
    conn.close()

    # Deduplicate: keep best snapshot per match (highest data_completeness)
    best: dict[int, dict] = {}
    for r in rows:
        mid = r["match_id"]
        d = dict(r)
        if mid not in best or (d.get("data_completeness") or 0) > (best[mid].get("data_completeness") or 0):
            best[mid] = d

    return sorted(best.values(), key=lambda x: x.get("match_date_utc", ""))


def build_snapshot(row: dict) -> PredictionFeatureSnapshot:
    """Reconstruct PredictionFeatureSnapshot from historical data."""
    # Market probabilities
    mp_w1 = row.get("implied_prob_w1")
    mp_d = row.get("implied_prob_draw")
    mp_w2 = row.get("implied_prob_w2")
    if mp_w1 is not None:
        mp_w1 = round(float(mp_w1), 1)
    if mp_d is not None:
        mp_d = round(float(mp_d), 1)
    if mp_w2 is not None:
        mp_w2 = round(float(mp_w2), 1)

    # O/U market
    market_over = market_under = None
    ou25 = row.get("odds_over_2_5")
    un25 = row.get("odds_under_2_5")
    if ou25 and un25 and float(ou25) > 1 and float(un25) > 1:
        inv_o = 1.0 / float(ou25)
        inv_u = 1.0 / float(un25)
        total = inv_o + inv_u
        if total > 0:
            market_over = round(inv_o * 100.0 / total, 1)
            market_under = round(inv_u * 100.0 / total, 1)

    # BTTS market
    market_btts_y = market_btts_n = None
    by = row.get("odds_btts_yes")
    bn = row.get("odds_btts_no")
    if by and bn and float(by) > 1 and float(bn) > 1:
        inv_y = 1.0 / float(by)
        inv_n = 1.0 / float(bn)
        total = inv_y + inv_n
        if total > 0:
            market_btts_y = round(inv_y * 100.0 / total, 1)
            market_btts_n = round(inv_n * 100.0 / total, 1)

    # Form features
    fp1 = row.get("team1_form_points_last5")
    fp2 = row.get("team2_form_points_last5")
    form_pts_diff = float(fp1 - fp2) if fp1 is not None and fp2 is not None else None

    gd1 = row.get("team1_goal_diff_last5")
    gd2 = row.get("team2_goal_diff_last5")
    gd_diff = float(gd1 - gd2) if gd1 is not None and gd2 is not None else None

    s1 = row.get("team1_scored_avg")
    s2 = row.get("team2_scored_avg")
    scored_diff = float(s1 - s2) if s1 is not None and s2 is not None else None

    c1 = row.get("team1_conceded_avg")
    c2 = row.get("team2_conceded_avg")
    conceded_diff = float(c1 - c2) if c1 is not None and c2 is not None else None

    # xG
    xg1 = row.get("team1_xg_for")
    xg2 = row.get("team2_xg_for")
    xg_diff = float(xg1 - xg2) if xg1 is not None and xg2 is not None else None

    xga1 = row.get("team1_xga")
    xga2 = row.get("team2_xga")
    xga_edge = float(xga2 - xga1) if xga1 is not None and xga2 is not None else None

    # Ratings
    r1 = row.get("team1_rating")
    r2 = row.get("team2_rating")
    rating_diff = float(r1 - r2) if r1 is not None and r2 is not None else None

    # Possession
    p1 = row.get("team1_possession")
    p2 = row.get("team2_possession")
    poss_diff = float(p1 - p2) if p1 is not None and p2 is not None else None

    # Standings
    pos1 = row.get("team1_table_position")
    pos2 = row.get("team2_table_position")
    position_gap = int(pos2 - pos1) if pos1 is not None and pos2 is not None else None

    pts1 = row.get("team1_points")
    pts2 = row.get("team2_points")
    point_gap = int(pts1 - pts2) if pts1 is not None and pts2 is not None else None

    # Market value
    mv1 = row.get("team1_market_value_eur")
    mv2 = row.get("team2_market_value_eur")
    mv_ratio = round(float(mv1) / float(mv2), 4) if mv1 and mv2 and float(mv2) > 0 else None

    return PredictionFeatureSnapshot(
        team1=row["team1_name"],
        team2=row["team2_name"],
        competition_name=row.get("competition_name"),
        data_completeness=float(row.get("data_completeness") or 0.3),
        is_team1_home=True,  # historical data = team1 is home
        is_neutral_venue=False,
        market_prob_w1=mp_w1,
        market_prob_draw=mp_d,
        market_prob_w2=mp_w2,
        market_prob_over_2_5=market_over,
        market_prob_under_2_5=market_under,
        market_prob_btts_yes=market_btts_y,
        market_prob_btts_no=market_btts_n,
        form_points_last5_diff=form_pts_diff,
        form_goal_diff_last5_diff=gd_diff,
        form_scored_avg_diff=scored_diff,
        form_conceded_avg_diff=conceded_diff,
        h2h_team1_win_rate=row.get("h2h_team1_win_rate"),
        h2h_draw_rate=row.get("h2h_draw_rate"),
        h2h_avg_goals=row.get("h2h_avg_goals"),
        xg_diff=xg_diff,
        xga_edge=xga_edge,
        rating_diff=rating_diff,
        possession_diff=poss_diff,
        standings_point_gap=point_gap,
        standings_position_gap=position_gap,
        rest_gap_hours=row.get("rest_gap_hours"),
        team1_short_rest=bool(row.get("team1_short_rest")) if row.get("team1_short_rest") is not None else None,
        team2_short_rest=bool(row.get("team2_short_rest")) if row.get("team2_short_rest") is not None else None,
        market_value_ratio=mv_ratio,
        key_absence_diff=int(row["key_absence_diff"]) if row.get("key_absence_diff") is not None else None,
    )


def actual_1x2(hg: int, ag: int) -> str:
    if hg > ag: return "W1"
    if ag > hg: return "W2"
    return "D"


def predicted_1x2(baseline: dict) -> str:
    pw1, pd, pw2 = baseline["prob_w1"], baseline["prob_draw"], baseline["prob_w2"]
    if pw1 >= pd and pw1 >= pw2: return "W1"
    if pw2 >= pw1 and pw2 >= pd: return "W2"
    return "D"


def brier(pw1: float, pd: float, pw2: float, actual: str) -> float:
    y1 = 1.0 if actual == "W1" else 0.0
    yd = 1.0 if actual == "D" else 0.0
    y2 = 1.0 if actual == "W2" else 0.0
    return ((pw1/100 - y1)**2 + (pd/100 - yd)**2 + (pw2/100 - y2)**2)


def run():
    print("=" * 70)
    print("  FULL PIPELINE OUT-OF-SAMPLE BACKTEST")
    print(f"  Test period: >= {TRAIN_CUTOFF}")
    print("=" * 70)

    print("\n[1/2] Loading test data...")
    test_data = load_test_data()
    print(f"  Matches with odds + snapshots: {len(test_data)}")
    if not test_data:
        print("ERROR: No test data!")
        return

    # Count matches with rich features
    with_features = sum(1 for r in test_data if r.get("team1_scored_avg") is not None)
    print(f"  With rich features (goals, xG, etc.): {with_features}")
    print(f"  Odds-only (basic): {len(test_data) - with_features}")

    print("\n[2/2] Running predictions...")
    res = Results()

    for row in test_data:
        hg = int(row["team1_score"])
        ag = int(row["team2_score"])
        actual = actual_1x2(hg, ag)
        total_goals = hg + ag
        actual_over = total_goals > 2.5
        actual_btts = hg > 0 and ag > 0
        league = row.get("competition_name", "Unknown")

        snapshot = build_snapshot(row)
        data = MatchData(team1=row["team1_name"], team2=row["team2_name"])

        # Build baseline with FlashscoreData if we have goal averages
        if row.get("team1_scored_avg") is not None:
            data.flashscore = FlashscoreData(
                team1_name=row["team1_name"],
                team2_name=row["team2_name"],
                team1_goals_scored_avg=row.get("team1_scored_avg"),
                team1_goals_conceded_avg=row.get("team1_conceded_avg"),
                team2_goals_scored_avg=row.get("team2_scored_avg"),
                team2_goals_conceded_avg=row.get("team2_conceded_avg"),
            )

        baseline = build_numeric_baseline(data, snapshot)
        res.total += 1

        # 1X2
        pred = predicted_1x2(baseline)
        if pred == actual:
            res.correct_1x2 += 1
            res.league[league]["c1x2"] += 1
        res.league[league]["n"] += 1

        # 1X2 confident (max prob > 50%)
        max_prob = max(baseline["prob_w1"], baseline["prob_draw"], baseline["prob_w2"])
        if max_prob > 50:
            res.total_confident += 1
            if pred == actual:
                res.correct_1x2_confident += 1

        # O/U 2.5
        pred_over = baseline["prob_over_2_5"] > 50.0
        if pred_over == actual_over:
            res.correct_ou += 1
            res.league[league]["cou"] += 1

        # O/U confident (prob > 55%)
        ou_prob = max(baseline["prob_over_2_5"], baseline["prob_under_2_5"])
        if ou_prob > 55:
            res.total_ou_confident += 1
            if pred_over == actual_over:
                res.correct_ou_confident += 1

        # BTTS
        pred_btts = baseline["prob_btts_yes"] > 50.0
        if pred_btts == actual_btts:
            res.correct_btts += 1
            res.league[league]["cbtts"] += 1

        # BTTS confident
        btts_prob = max(baseline["prob_btts_yes"], baseline["prob_btts_no"])
        if btts_prob > 55:
            res.total_btts_confident += 1
            if pred_btts == actual_btts:
                res.correct_btts_confident += 1

        # Brier score
        res.brier_sum += brier(baseline["prob_w1"], baseline["prob_draw"], baseline["prob_w2"], actual)

        # Log loss
        prob_actual = {"W1": baseline["prob_w1"], "D": baseline["prob_draw"], "W2": baseline["prob_w2"]}[actual]
        prob_actual = max(0.1, min(99.9, prob_actual)) / 100.0
        res.log_loss_sum += -math.log(prob_actual)

        # O/U ROI: bet when edge > 3% vs market
        if snapshot.market_prob_over_2_5 is not None:
            model_edge = baseline["prob_over_2_5"] - snapshot.market_prob_over_2_5
            if abs(model_edge) > 3.0:
                bet_over = model_edge > 0
                ou_odds = row.get("odds_over_2_5") if bet_over else row.get("odds_under_2_5")
                if ou_odds and float(ou_odds) > 1.0:
                    res.ou_bets += 1
                    res.ou_staked += 1.0
                    actual_matches = (bet_over and actual_over) or (not bet_over and not actual_over)
                    if actual_matches:
                        res.ou_returned += float(ou_odds)

        # Track with/without venue splits
        has_splits = snapshot.team1_home_scored_avg is not None
        if has_splits:
            res.total_with_splits += 1
            if pred_over == actual_over:
                res.correct_ou_with_splits += 1
        else:
            res.total_without_splits += 1
            if pred_over == actual_over:
                res.correct_ou_without_splits += 1

    # Results
    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    acc1x2 = res.correct_1x2 / res.total * 100
    acc_ou = res.correct_ou / res.total * 100
    acc_btts = res.correct_btts / res.total * 100
    avg_brier = res.brier_sum / res.total
    avg_ll = res.log_loss_sum / res.total

    print(f"\n  Matches tested:      {res.total}")
    print(f"\n  1X2 accuracy:        {res.correct_1x2}/{res.total} = {acc1x2:.1f}%")
    if res.total_confident:
        print(f"  1X2 confident(>50%): {res.correct_1x2_confident}/{res.total_confident} = {res.correct_1x2_confident/res.total_confident*100:.1f}%")

    print(f"\n  O/U 2.5 accuracy:    {res.correct_ou}/{res.total} = {acc_ou:.1f}%")
    if res.total_ou_confident:
        print(f"  O/U confident(>55%): {res.correct_ou_confident}/{res.total_ou_confident} = {res.correct_ou_confident/res.total_ou_confident*100:.1f}%")

    print(f"\n  BTTS accuracy:       {res.correct_btts}/{res.total} = {acc_btts:.1f}%")
    if res.total_btts_confident:
        print(f"  BTTS confident(>55%):{res.correct_btts_confident}/{res.total_btts_confident} = {res.correct_btts_confident/res.total_btts_confident*100:.1f}%")

    print(f"\n  Avg Brier score:     {avg_brier:.4f}  (random=0.667, market~0.42)")
    print(f"  Avg Log loss:        {avg_ll:.4f}  (random=1.099, market~0.95)")

    if res.ou_bets:
        roi = (res.ou_returned - res.ou_staked) / res.ou_staked * 100
        print(f"\n  O/U value bets (edge>3%):")
        print(f"    Bets={res.ou_bets}, Staked={res.ou_staked:.0f}, Returned={res.ou_returned:.1f}")
        print(f"    ROI = {roi:+.1f}%")

    # With vs without venue splits
    if res.total_with_splits > 0 and res.total_without_splits > 0:
        acc_with = res.correct_ou_with_splits / res.total_with_splits * 100
        acc_without = res.correct_ou_without_splits / res.total_without_splits * 100
        print(f"\n  O/U accuracy WITH venue splits:    {res.correct_ou_with_splits}/{res.total_with_splits} = {acc_with:.1f}%")
        print(f"  O/U accuracy WITHOUT venue splits: {res.correct_ou_without_splits}/{res.total_without_splits} = {acc_without:.1f}%")
        print(f"  Venue splits lift: {acc_with - acc_without:+.1f}pp")

    print(f"\n  Per-league breakdown:")
    for league, d in sorted(res.league.items(), key=lambda x: -x[1]["n"]):
        n = d["n"]
        if n < 10:
            continue
        a1 = d["c1x2"] / n * 100
        ao = d["cou"] / n * 100
        ab = d["cbtts"] / n * 100
        print(f"    {league:35s} n={n:4d}  1X2={a1:5.1f}%  O/U={ao:5.1f}%  BTTS={ab:5.1f}%")

    print("\n" + "=" * 70)
    print("  BENCHMARKS:")
    print(f"    Random:            33.3% (1X2), 50.0% (O/U), 50.0% (BTTS)")
    print(f"    Home-always:      ~46% (1X2)")
    print(f"    Market consensus:  ~52% (1X2), ~55% (O/U)")
    print(f"    FULL PIPELINE:     {acc1x2:.1f}% (1X2), {acc_ou:.1f}% (O/U), {acc_btts:.1f}% (BTTS)")
    if acc_ou > 55:
        print(f"    >> O/U BEATS MARKET BASELINE!")
    print("=" * 70)


if __name__ == "__main__":
    run()
