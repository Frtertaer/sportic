"""
Monte Carlo bankroll simulation.

Uses historical backtest results to simulate 10,000 seasons of betting.
Answers: what's the probability of ruin? Expected bankroll after N bets?
What's the realistic ROI distribution?
"""
from __future__ import annotations

import io
import math
import random
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
TOP5_LEAGUES = {"Bundesliga", "Primeira Liga", "La Liga", "Ligue 1", "Premier League"}

N_SIMULATIONS = 10_000
INITIAL_BANKROLL = 100.0
RUIN_THRESHOLD = 10.0  # bankrupt if bankroll drops below this


def kelly_stake(prob: float, odds: float, fraction: float = 0.25) -> float:
    if odds <= 1.0 or prob <= 0 or prob >= 1:
        return 0.0
    b = odds - 1.0
    f = (b * prob - (1 - prob)) / b
    return min(max(f * fraction, 0.0), 0.10)


def load_bet_outcomes() -> list[dict]:
    """Load test-set matches, compute model predictions, identify qualifying bets."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            m.team1_name, m.team2_name,
            m.team1_score, m.team2_score,
            m.match_date_utc, m.competition_name,
            ml.implied_prob_w1, ml.implied_prob_draw, ml.implied_prob_w2,
            ml.odds_over_2_5, ml.odds_under_2_5,
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
            ts.rest_gap_hours,
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

    best = {}
    for r in rows:
        key = (r["team1_name"], r["team2_name"], r["match_date_utc"])
        d = dict(r)
        if key not in best or (d.get("data_completeness") or 0) > (best[key].get("data_completeness") or 0):
            best[key] = d
    data = sorted(best.values(), key=lambda x: x.get("match_date_utc", ""))

    bets = []
    for row in data:
        league = row.get("competition_name", "")
        if league not in TOP5_LEAGUES:
            continue

        hg, ag = int(row["team1_score"]), int(row["team2_score"])
        total = hg + ag

        mp_w1 = round(float(row["implied_prob_w1"]), 1)
        mp_d = round(float(row["implied_prob_draw"]), 1)
        mp_w2 = round(float(row["implied_prob_w2"]), 1)

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

        snapshot = PredictionFeatureSnapshot(
            team1=row["team1_name"], team2=row["team2_name"],
            competition_name=league,
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

        if market_over is None:
            continue

        # O/U bet check (threshold=8%)
        model_over = baseline["prob_over_2_5"]
        model_under = baseline["prob_under_2_5"]
        edge_over = model_over - market_over
        edge_under = (model_under - market_under) if market_under else 0

        odds_over = float(ou25) if ou25 else None
        odds_under = float(un25) if un25 else None

        if edge_over > 8 and odds_over:
            stake_pct = kelly_stake(model_over / 100.0, odds_over, 0.25)
            if stake_pct > 0:
                bets.append({
                    "stake_pct": stake_pct,
                    "odds": odds_over,
                    "won": total > 2.5,
                    "market": "O/U Over",
                })
        elif edge_under > 8 and odds_under:
            stake_pct = kelly_stake(model_under / 100.0, odds_under, 0.25)
            if stake_pct > 0:
                bets.append({
                    "stake_pct": stake_pct,
                    "odds": odds_under,
                    "won": total <= 2.5,
                    "market": "O/U Under",
                })

    return bets


def simulate(bets: list[dict], n_sims: int = N_SIMULATIONS) -> dict:
    """Run Monte Carlo simulation."""
    final_bankrolls = []
    max_drawdowns = []
    ruin_count = 0
    peak_counts = []

    for _ in range(n_sims):
        bankroll = INITIAL_BANKROLL
        peak = INITIAL_BANKROLL
        max_dd = 0.0
        ruined = False

        # Shuffle bets (simulate different bet ordering)
        sim_bets = random.sample(bets, len(bets))

        for bet in sim_bets:
            if bankroll < RUIN_THRESHOLD:
                ruined = True
                break

            stake = bet["stake_pct"] * bankroll
            if bet["won"]:
                bankroll += stake * (bet["odds"] - 1)
            else:
                bankroll -= stake

            peak = max(peak, bankroll)
            dd = (peak - bankroll) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        final_bankrolls.append(bankroll)
        max_drawdowns.append(max_dd)
        if ruined:
            ruin_count += 1

    return {
        "final_bankrolls": np.array(final_bankrolls),
        "max_drawdowns": np.array(max_drawdowns),
        "ruin_count": ruin_count,
        "n_sims": n_sims,
    }


def run():
    print("=" * 70)
    print("  MONTE CARLO BANKROLL SIMULATION")
    print("=" * 70)

    print("\n  Loading qualifying bets from test set...")
    bets = load_bet_outcomes()
    won = sum(1 for b in bets if b["won"])
    print(f"  Qualifying bets: {len(bets)}")
    print(f"  Win rate: {won}/{len(bets)} = {won/len(bets)*100:.1f}%")
    print(f"  Avg stake: {np.mean([b['stake_pct'] for b in bets])*100:.2f}% of bankroll")
    print(f"  Avg odds: {np.mean([b['odds'] for b in bets]):.2f}")

    if len(bets) < 20:
        print("\n  Not enough bets for meaningful simulation.")
        return

    print(f"\n  Running {N_SIMULATIONS:,} simulations...")
    results = simulate(bets)

    fb = results["final_bankrolls"]
    dd = results["max_drawdowns"]

    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    roi = (fb.mean() - INITIAL_BANKROLL) / INITIAL_BANKROLL * 100
    print(f"\n  Starting bankroll: {INITIAL_BANKROLL:.0f}")
    print(f"  Bets per simulation: {len(bets)}")
    print(f"\n  Final bankroll distribution:")
    print(f"    Mean:     {fb.mean():.1f} (ROI: {roi:+.1f}%)")
    print(f"    Median:   {np.median(fb):.1f}")
    print(f"    Std Dev:  {fb.std():.1f}")
    print(f"    5th pct:  {np.percentile(fb, 5):.1f}")
    print(f"    25th pct: {np.percentile(fb, 25):.1f}")
    print(f"    75th pct: {np.percentile(fb, 75):.1f}")
    print(f"    95th pct: {np.percentile(fb, 95):.1f}")
    print(f"    Min:      {fb.min():.1f}")
    print(f"    Max:      {fb.max():.1f}")

    print(f"\n  Risk metrics:")
    print(f"    Ruin probability: {results['ruin_count']}/{N_SIMULATIONS} = {results['ruin_count']/N_SIMULATIONS*100:.2f}%")
    print(f"    P(profit):        {(fb > INITIAL_BANKROLL).sum()}/{N_SIMULATIONS} = {(fb > INITIAL_BANKROLL).mean()*100:.1f}%")
    print(f"    P(lose > 20%):    {(fb < INITIAL_BANKROLL * 0.8).sum()}/{N_SIMULATIONS} = {(fb < INITIAL_BANKROLL * 0.8).mean()*100:.1f}%")

    print(f"\n  Max drawdown distribution:")
    print(f"    Mean:    {dd.mean():.1f}%")
    print(f"    Median:  {np.median(dd):.1f}%")
    print(f"    95th:    {np.percentile(dd, 95):.1f}%")
    print(f"    Max:     {dd.max():.1f}%")

    # ROI distribution
    rois = (fb - INITIAL_BANKROLL) / INITIAL_BANKROLL * 100
    print(f"\n  ROI distribution:")
    print(f"    Mean:     {rois.mean():+.1f}%")
    print(f"    Median:   {np.median(rois):+.1f}%")
    print(f"    5th pct:  {np.percentile(rois, 5):+.1f}%")
    print(f"    95th pct: {np.percentile(rois, 95):+.1f}%")

    # Histogram
    print(f"\n  ROI histogram:")
    bins = [(-50, -20), (-20, -10), (-10, -5), (-5, 0), (0, 5), (5, 10), (10, 20), (20, 50)]
    for lo, hi in bins:
        count = ((rois >= lo) & (rois < hi)).sum()
        pct = count / N_SIMULATIONS * 100
        bar = "#" * int(pct / 2)
        print(f"    {lo:+4d}% to {hi:+3d}%: {pct:5.1f}% {bar}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    run()
