"""
ROI sweep: find optimal edge threshold and Kelly fraction for O/U bets.

Tests every combination of:
  - Edge threshold: 1% to 15%
  - Kelly fraction: full, half, quarter
  - League filters: all, top-3 only, top-2 only

Also measures 1X2 and BTTS ROI at optimal thresholds.
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

from models import MatchData, FlashscoreData, PredictionFeatureSnapshot
from baseline_model import build_numeric_baseline

DB_PATH = Path(__file__).parent.parent / "sportsbot.db"
TRAIN_CUTOFF = "2024-08-01"

TOP_OU_LEAGUES = {"Bundesliga", "Primeira Liga", "La Liga", "Ligue 1", "Premier League"}
TOP2_OU_LEAGUES = {"Bundesliga", "Primeira Liga"}


def load_test_data() -> list[dict]:
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
          AND m.team1_score IS NOT NULL AND m.team2_score IS NOT NULL
          AND ml.implied_prob_w1 IS NOT NULL
        ORDER BY m.match_date_utc
    """, (TRAIN_CUTOFF,)).fetchall()
    conn.close()

    best: dict = {}
    for r in rows:
        key = (r["team1_name"], r["team2_name"], r["match_date_utc"])
        d = dict(r)
        if key not in best or (d.get("data_completeness") or 0) > (best[key].get("data_completeness") or 0):
            best[key] = d
    return sorted(best.values(), key=lambda x: x.get("match_date_utc", ""))


def build_snapshot(row: dict) -> PredictionFeatureSnapshot:
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

    market_btts_y = market_btts_n = None
    by, bn = row.get("odds_btts_yes"), row.get("odds_btts_no")
    if by and bn and float(by) > 1 and float(bn) > 1:
        inv_y, inv_n = 1.0 / float(by), 1.0 / float(bn)
        t = inv_y + inv_n
        if t > 0:
            market_btts_y = round(inv_y * 100 / t, 1)
            market_btts_n = round(inv_n * 100 / t, 1)

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
        market_prob_btts_yes=market_btts_y, market_prob_btts_no=market_btts_n,
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
        team1_short_rest=bool(row.get("team1_short_rest")) if row.get("team1_short_rest") is not None else None,
        team2_short_rest=bool(row.get("team2_short_rest")) if row.get("team2_short_rest") is not None else None,
        market_value_ratio=mv_ratio,
        key_absence_diff=int(row["key_absence_diff"]) if row.get("key_absence_diff") is not None else None,
    )


def kelly_stake(prob_model: float, odds: float, fraction: float = 1.0) -> float:
    """Kelly criterion: f* = (bp - q) / b where b=odds-1, p=prob, q=1-p."""
    if odds <= 1.0 or prob_model <= 0 or prob_model >= 1:
        return 0.0
    b = odds - 1.0
    p = prob_model
    q = 1.0 - p
    f = (b * p - q) / b
    if f <= 0:
        return 0.0
    return min(f * fraction, 0.10)  # cap at 10% of bankroll


def run():
    print("=" * 70)
    print("  ROI SWEEP: THRESHOLD + KELLY + LEAGUE FILTER")
    print("=" * 70)

    print("\nLoading data...")
    data = load_test_data()
    print(f"  Test matches: {len(data)}")

    # Pre-compute all predictions
    predictions = []
    for row in data:
        hg, ag = int(row["team1_score"]), int(row["team2_score"])
        total = hg + ag
        league = row.get("competition_name", "")

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

        predictions.append({
            "league": league,
            "hg": hg, "ag": ag, "total": total,
            "actual_over": total > 2.5,
            "actual_btts": hg > 0 and ag > 0,
            "actual_1x2": "W1" if hg > ag else ("W2" if ag > hg else "D"),
            "baseline": baseline,
            "snapshot": snapshot,
            "odds_over": float(row["odds_over_2_5"]) if row.get("odds_over_2_5") else None,
            "odds_under": float(row["odds_under_2_5"]) if row.get("odds_under_2_5") else None,
            "odds_btts_y": float(row["odds_btts_yes"]) if row.get("odds_btts_yes") else None,
            "odds_btts_n": float(row["odds_btts_no"]) if row.get("odds_btts_no") else None,
            "odds_w1": float(row["odds_w1"]) if row.get("odds_w1") else None,
            "odds_draw": float(row["odds_draw"]) if row.get("odds_draw") else None,
            "odds_w2": float(row["odds_w2"]) if row.get("odds_w2") else None,
        })

    print(f"  Predictions computed: {len(predictions)}")

    # ── O/U SWEEP ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  O/U 2.5 — EDGE THRESHOLD SWEEP")
    print("=" * 70)
    print(f"  {'Thresh':>6s} {'Kelly':>6s} {'Leagues':>10s} {'Bets':>5s} {'Won':>5s} {'Acc%':>6s} {'Staked':>8s} {'Return':>8s} {'ROI%':>7s} {'MaxDD':>7s}")
    print("  " + "-" * 78)

    best_roi = -999.0
    best_config = ""

    for league_filter_name, league_set in [("all", None), ("top5", TOP_OU_LEAGUES), ("top2", TOP2_OU_LEAGUES)]:
        for thresh in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15]:
            for kelly_name, kelly_frac in [("full", 1.0), ("half", 0.5), ("quarter", 0.25)]:
                bankroll = 100.0
                peak = 100.0
                max_dd = 0.0
                bets = 0
                won = 0
                total_staked = 0.0
                total_returned = 0.0

                for p in predictions:
                    if league_set and p["league"] not in league_set:
                        continue
                    mp_over = p["snapshot"].market_prob_over_2_5
                    mp_under = p["snapshot"].market_prob_under_2_5
                    if mp_over is None:
                        continue

                    model_over = p["baseline"]["prob_over_2_5"]
                    model_under = p["baseline"]["prob_under_2_5"]

                    # Check if we have an edge on Over
                    edge_over = model_over - mp_over
                    edge_under = model_under - mp_under if mp_under else 0

                    bet_over = edge_over > thresh
                    bet_under = edge_under > thresh

                    if bet_over and p["odds_over"]:
                        stake = kelly_stake(model_over / 100.0, p["odds_over"], kelly_frac) * bankroll
                        if stake > 0:
                            bets += 1
                            total_staked += stake
                            if p["actual_over"]:
                                won += 1
                                payout = stake * p["odds_over"]
                                total_returned += payout
                                bankroll += payout - stake
                            else:
                                bankroll -= stake
                            peak = max(peak, bankroll)
                            dd = (peak - bankroll) / peak * 100
                            max_dd = max(max_dd, dd)

                    elif bet_under and p["odds_under"]:
                        stake = kelly_stake(model_under / 100.0, p["odds_under"], kelly_frac) * bankroll
                        if stake > 0:
                            bets += 1
                            total_staked += stake
                            if not p["actual_over"]:
                                won += 1
                                payout = stake * p["odds_under"]
                                total_returned += payout
                                bankroll += payout - stake
                            else:
                                bankroll -= stake
                            peak = max(peak, bankroll)
                            dd = (peak - bankroll) / peak * 100
                            max_dd = max(max_dd, dd)

                if bets >= 20:
                    roi = (total_returned - total_staked) / total_staked * 100 if total_staked > 0 else 0
                    acc = won / bets * 100
                    label = f"  {thresh:5d}% {kelly_name:>6s} {league_filter_name:>10s} {bets:5d} {won:5d} {acc:5.1f}% {total_staked:7.1f} {total_returned:7.1f} {roi:+6.1f}% {max_dd:6.1f}%"
                    if roi > 0:
                        label += "  <<<<<"
                    print(label)
                    if roi > best_roi:
                        best_roi = roi
                        best_config = f"thresh={thresh}% kelly={kelly_name} leagues={league_filter_name} bets={bets} roi={roi:+.1f}%"

    print(f"\n  BEST O/U CONFIG: {best_config}")

    # ── 1X2 SWEEP ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  1X2 — EDGE THRESHOLD SWEEP (Kelly half)")
    print("=" * 70)
    print(f"  {'Thresh':>6s} {'Bets':>5s} {'Won':>5s} {'Acc%':>6s} {'ROI%':>7s}")
    print("  " + "-" * 40)

    for thresh in [3, 5, 7, 10, 12, 15]:
        bets = won = 0
        total_staked = total_returned = 0.0
        for p in predictions:
            mp_w1 = p["snapshot"].market_prob_w1
            mp_d = p["snapshot"].market_prob_draw
            mp_w2 = p["snapshot"].market_prob_w2
            if mp_w1 is None:
                continue

            b = p["baseline"]
            options = [
                (b["prob_w1"] - mp_w1, b["prob_w1"], p["odds_w1"], p["actual_1x2"] == "W1"),
                (b["prob_draw"] - mp_d, b["prob_draw"], p["odds_draw"], p["actual_1x2"] == "D"),
                (b["prob_w2"] - mp_w2, b["prob_w2"], p["odds_w2"], p["actual_1x2"] == "W2"),
            ]
            for edge, prob, odds, hit in options:
                if edge > thresh and odds and odds > 1:
                    stake = kelly_stake(prob / 100.0, odds, 0.5)
                    if stake > 0:
                        bets += 1
                        total_staked += stake
                        if hit:
                            won += 1
                            total_returned += stake * odds

        if bets >= 10:
            roi = (total_returned - total_staked) / total_staked * 100 if total_staked > 0 else 0
            acc = won / bets * 100
            marker = "  <<" if roi > 0 else ""
            print(f"  {thresh:5d}% {bets:5d} {won:5d} {acc:5.1f}% {roi:+6.1f}%{marker}")

    # ── BTTS SWEEP ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  BTTS — EDGE THRESHOLD SWEEP (Kelly half)")
    print("=" * 70)
    print(f"  {'Thresh':>6s} {'Bets':>5s} {'Won':>5s} {'Acc%':>6s} {'ROI%':>7s}")
    print("  " + "-" * 40)

    for thresh in [3, 5, 7, 10, 12, 15]:
        bets = won = 0
        total_staked = total_returned = 0.0
        for p in predictions:
            mp_y = p["snapshot"].market_prob_btts_yes
            mp_n = p["snapshot"].market_prob_btts_no
            if mp_y is None:
                continue

            b = p["baseline"]
            options = [
                (b["prob_btts_yes"] - mp_y, b["prob_btts_yes"], p["odds_btts_y"], p["actual_btts"]),
                (b["prob_btts_no"] - mp_n, b["prob_btts_no"], p["odds_btts_n"], not p["actual_btts"]),
            ]
            for edge, prob, odds, hit in options:
                if edge > thresh and odds and odds > 1:
                    stake = kelly_stake(prob / 100.0, odds, 0.5)
                    if stake > 0:
                        bets += 1
                        total_staked += stake
                        if hit:
                            won += 1
                            total_returned += stake * odds

        if bets >= 10:
            roi = (total_returned - total_staked) / total_staked * 100 if total_staked > 0 else 0
            acc = won / bets * 100
            marker = "  <<" if roi > 0 else ""
            print(f"  {thresh:5d}% {bets:5d} {won:5d} {acc:5.1f}% {roi:+6.1f}%{marker}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    run()
