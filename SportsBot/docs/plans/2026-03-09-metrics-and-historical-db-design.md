# SportsBot - Metrics and Historical Database Design
**Date:** 2026-03-09
**Status:** Draft

---

## Overview

This document defines the first foundation step for taking SportsBot from a
live-prediction assistant into a measurable prediction system:

1. Fix the target metrics so "better" has a precise meaning.
2. Add a historical data layer for offline training, calibration, and backtests.
3. Keep the current live bot schema intact while adding a separate research
   dataset that can grow over time.

The current project already stores:
- `predictions`: live bot outputs and resolved results
- `prediction_features`: frozen feature snapshot captured at prediction time
- lessons, calibration, and memory tables

That is enough for online self-learning, but not enough for:
- training a strong numeric model
- comparing against market closing lines
- backfilling older seasons
- evaluating new markets at scale

This design adds those missing capabilities without replacing the current live
workflow.

---

## Current Baseline

### Already available in the codebase

- Live prediction persistence in `predictions`
- Frozen online feature snapshots in `prediction_features`
- Offline evaluation scaffold in `scripts/backtest_baseline.py`
- Resolved results with 1X2, goals, and BTTS derivation

### Current gaps

- No full historical match store independent of bot-generated predictions
- No opening/closing market history table
- No per-match provider coverage history outside the live prediction path
- No reusable team-state snapshot dataset for model training
- No clean separation between:
  - online bot telemetry
  - research/training data

---

## Product Success Metrics

The bot should not be judged by "looks smart" or "found a pick". It should be
judged by stable, measurable outcomes.

### Primary metrics

1. `1X2 Brier score`
   - Measures probability quality for win/draw/loss.
   - Primary probability metric for the core market.

2. `Over 2.5 Brier score`
   - Measures total-goals calibration.

3. `BTTS Yes Brier score`
   - Measures BTTS calibration.

4. `Calibration error by bucket`
   - Compare predicted probabilities against actual hit rate in buckets:
     `40-45`, `45-50`, `50-55`, ... `75-80`.
   - Needed to tell whether the model is overconfident or underconfident.

5. `Top-pick ROI`
   - Flat stake ROI on the top recommendation only.
   - Must be split by market type and by recommendation mode:
     `strict`, `balanced`, `action`.

6. `CLV against closing line`
   - Closing line value is the strongest market-quality sanity check.
   - Even before ROI becomes reliable, CLV shows whether picks beat the market.

### Secondary metrics

1. `No-bet rate`
   - Overall and by league.
   - Too high means the bot is overly conservative.
   - Too low means the bot is forcing weak picks.

2. `Coverage rate`
   - Share of matches where the bot has enough data to produce a normal pick.
   - Split into:
     - raw provider coverage
     - applicable coverage

3. `Prediction cost`
   - Average AI cost per prediction.
   - Average AI cost per accepted pick.

4. `Latency`
   - Scraping time
   - AI time
   - End-to-end time

### Target thresholds for the next major version

These are not final production SLAs. They are development gates.

| Metric | Initial target |
|------|------|
| 1X2 Brier vs market prior | equal or better |
| O/U 2.5 Brier vs market prior | equal or better |
| BTTS Brier vs market prior | equal or better |
| Calibration drift in main buckets | within +/-5 percentage points |
| Top-pick CLV | positive on average |
| Top-pick ROI | non-negative over large enough sample |
| No-bet rate in balanced mode | 25% to 55% |
| Predictions with feature snapshot | 100% |
| Cost per prediction | controlled and reported |

---

## Design Principles

1. Keep the current bot working.
   - `predictions` and `prediction_features` remain the online source of truth
     for live bot behavior.

2. Additive schema only.
   - No destructive migration.
   - Historical tables can be added incrementally.

3. Separate live telemetry from research data.
   - A historical match may exist even if the bot never predicted it.
   - A live prediction may exist even if the historical importer has not yet
     backfilled that league/date.

4. Prefer reproducibility over cleverness.
   - Every offline backtest row should be reconstructable from stored historical
     market lines, result, and team-state snapshot.

5. Closing market is the reference baseline.
   - The bot must prove it adds value versus market prices, not just versus
     intuition.

---

## Proposed Historical Data Layer

### Table 1: `historical_matches`

Purpose:
- Canonical match results table for offline training and evaluation.

Key fields:
- `id INTEGER PRIMARY KEY`
- `provider_match_key TEXT UNIQUE NOT NULL`
- `provider_name TEXT NOT NULL`
- `competition_name TEXT`
- `competition_country TEXT`
- `season_label TEXT`
- `stage_name TEXT`
- `match_date_utc TEXT NOT NULL`
- `kickoff_utc TEXT`
- `team1_name TEXT NOT NULL`
- `team2_name TEXT NOT NULL`
- `team1_normalized TEXT NOT NULL`
- `team2_normalized TEXT NOT NULL`
- `is_neutral_venue INTEGER`
- `venue_name TEXT`
- `team1_score INTEGER`
- `team2_score INTEGER`
- `actual_result TEXT`
- `went_extra_time INTEGER DEFAULT 0`
- `went_penalties INTEGER DEFAULT 0`
- `status TEXT NOT NULL DEFAULT 'FINISHED'`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Notes:
- `provider_match_key` is the dedupe anchor for imports.
- `team*_normalized` uses the same normalization strategy as the bot so that
  offline and online paths compare like-for-like.
- Knockout and penalty context should be stored explicitly because some cup
  matches are poor training targets for normal 1X2 modeling.

### Table 2: `historical_market_lines`

Purpose:
- Store bookmaker market information per historical match.
- Supports value detection, closing-line evaluation, and alternative markets.

Key fields:
- `id INTEGER PRIMARY KEY`
- `historical_match_id INTEGER NOT NULL`
- `snapshot_kind TEXT NOT NULL`
  - expected values: `OPEN`, `MID`, `CLOSE`
- `captured_at TEXT`
- `bookmaker_count INTEGER`
- `source_provider TEXT NOT NULL`
- `odds_w1 REAL`
- `odds_draw REAL`
- `odds_w2 REAL`
- `implied_prob_w1 REAL`
- `implied_prob_draw REAL`
- `implied_prob_w2 REAL`
- `odds_over_1_5 REAL`
- `odds_under_1_5 REAL`
- `odds_over_2_5 REAL`
- `odds_under_2_5 REAL`
- `odds_over_3_5 REAL`
- `odds_under_3_5 REAL`
- `odds_btts_yes REAL`
- `odds_btts_no REAL`
- `odds_dnb_team1 REAL`
- `odds_dnb_team2 REAL`
- `odds_dc_1x REAL`
- `odds_dc_12 REAL`
- `odds_dc_x2 REAL`
- `ah_main_line TEXT`
- `ah_team1_odds REAL`
- `ah_team2_odds REAL`
- `consensus_favourite TEXT`
- `movement_score_team1 REAL`
- `movement_score_team2 REAL`
- `dispersion_1x2 REAL`
- `dispersion_totals REAL`
- `created_at TEXT NOT NULL`

Constraints:
- Unique on `(historical_match_id, snapshot_kind, source_provider)`.

Notes:
- The first version does not need every alternative handicap line.
- It must at least store one main AH line plus the directly supported markets
  already used by the bot.

### Table 3: `historical_team_snapshots`

Purpose:
- Freeze the state of both teams as it was known before kickoff.
- This is the training base for a future numeric model.

Key fields:
- `id INTEGER PRIMARY KEY`
- `historical_match_id INTEGER NOT NULL UNIQUE`
- `snapshot_source TEXT NOT NULL`
- `captured_at TEXT`
- `data_completeness REAL`
- `successful_source_count INTEGER`
- `total_source_count INTEGER`
- `team1_form_points_last5 REAL`
- `team2_form_points_last5 REAL`
- `team1_goal_diff_last5 REAL`
- `team2_goal_diff_last5 REAL`
- `team1_scored_avg REAL`
- `team2_scored_avg REAL`
- `team1_conceded_avg REAL`
- `team2_conceded_avg REAL`
- `h2h_team1_win_rate REAL`
- `h2h_draw_rate REAL`
- `h2h_avg_goals REAL`
- `team1_xg_for REAL`
- `team2_xg_for REAL`
- `team1_xga REAL`
- `team2_xga REAL`
- `team1_rating REAL`
- `team2_rating REAL`
- `team1_possession REAL`
- `team2_possession REAL`
- `team1_table_position INTEGER`
- `team2_table_position INTEGER`
- `team1_points INTEGER`
- `team2_points INTEGER`
- `rest_gap_hours REAL`
- `team1_short_rest INTEGER`
- `team2_short_rest INTEGER`
- `team1_market_value_eur REAL`
- `team2_market_value_eur REAL`
- `key_absence_diff INTEGER`
- `competition_name TEXT`
- `schedule_density_note TEXT`
- `created_at TEXT NOT NULL`

Notes:
- This table intentionally mirrors the shape of `PredictionFeatureSnapshot`
  where useful, but stores raw pre-feature values instead of only derived diffs.
- A later training pipeline can derive both raw and engineered features from it.

### Table 4: `historical_provider_coverage`

Purpose:
- Preserve provider health and coverage context for each historical match.
- Distinguish true missing data from provider-specific blind spots.

Key fields:
- `id INTEGER PRIMARY KEY`
- `historical_match_id INTEGER NOT NULL`
- `source_key TEXT NOT NULL`
- `source_label TEXT NOT NULL`
- `status TEXT NOT NULL`
  - expected values: `OK`, `FAILED`, `UNSUPPORTED`, `PARTIAL`
- `error_type TEXT`
- `error_message TEXT`
- `duration_ms INTEGER`
- `provider_name TEXT`
- `created_at TEXT NOT NULL`

Constraints:
- Unique on `(historical_match_id, source_key)`.

### Table 5: `historical_import_runs`

Purpose:
- Track importer runs, failures, and backfill progress.

Key fields:
- `id INTEGER PRIMARY KEY`
- `run_type TEXT NOT NULL`
  - expected values: `MATCHES`, `MARKETS`, `SNAPSHOTS`, `BACKFILL`
- `provider_name TEXT NOT NULL`
- `started_at TEXT NOT NULL`
- `finished_at TEXT`
- `status TEXT NOT NULL`
  - expected values: `RUNNING`, `SUCCESS`, `PARTIAL`, `FAILED`
- `season_label TEXT`
- `competition_name TEXT`
- `requested_from TEXT`
- `requested_to TEXT`
- `records_inserted INTEGER DEFAULT 0`
- `records_updated INTEGER DEFAULT 0`
- `records_failed INTEGER DEFAULT 0`
- `error_summary TEXT`

---

## Relationship to Existing Live Tables

### Keep as-is

- `predictions`
- `prediction_features`
- `lessons`
- `calibration`
- vector memory state

### Intended division of responsibility

`predictions` / `prediction_features`
- Online bot telemetry
- Real bot outputs
- Used for online evaluation and live self-learning

`historical_*`
- Offline research dataset
- Backfill from older seasons
- Training and reproducible backtests
- Coverage analytics by league/provider/date

### Optional future bridge

Later we may add:
- `predictions.historical_match_id`

This would join a live prediction to a canonical historical row once that match
exists in the history layer. It should not be part of the first migration.

---

## Ingestion and Backfill Plan

### Phase A: Minimal viable history

Backfill the following for top leagues first:
- Premier League
- La Liga
- Serie A
- Bundesliga
- Ligue 1

Time range:
- last 3 complete seasons minimum

Imported artifacts:
- final match result
- closing market line
- basic team-state snapshot

### Phase B: Improve market depth

Add:
- opening line
- mid snapshot if available
- DNB / Double Chance
- alt totals 1.5 / 3.5
- main AH line

### Phase C: Improve feature depth

Add:
- stronger injury/lineup coverage
- better weak-league fallback providers
- richer standings/context snapshots

### Importer constraints

1. Import must be idempotent.
2. Duplicate rows must merge by provider match key.
3. Partial imports must not corrupt finished rows.
4. Import status must be queryable from `historical_import_runs`.

---

## Evaluation Plan

### Offline reports to add

1. `backtest_probabilities`
   - 1X2, O/U 2.5, BTTS Brier
   - compare:
     - market prior
     - numeric model
     - blended model
     - live bot

2. `backtest_calibration`
   - confidence bucket tables
   - expected vs actual plots or text summaries

3. `backtest_picks`
   - top-pick ROI
   - hit rate
   - no-bet rate
   - split by market
   - split by recommendation mode

4. `backtest_clv`
   - open vs picked vs close
   - average closing line gain/loss

### Minimum acceptance gate before new model rollout

A new numeric/blended model should not replace the current one unless:
- 1X2 Brier is better than or equal to current baseline
- CLV is positive on top picks
- no-bet rate stays inside the chosen mode band
- there is no severe calibration blow-up in high-confidence buckets

---

## Implementation Sequence

### Step 1
- Add new schema and DB migration helpers
- No runtime behavior change yet

### Step 2
- Add importer scaffolding and import-run tracking
- Start with one provider and one competition

### Step 3
- Extend backtest scripts to read historical tables
- Produce the first real offline metrics report

### Step 4
- Train numeric baseline from historical tables
- Compare against current live feature-snapshot baseline

### Step 5
- Bridge live predictions to canonical historical matches
- Start measuring CLV and ROI with stronger provenance

---

## Out of Scope for This Step

- Training the final numeric model
- Replacing the agent chain
- New user commands
- New prompt engineering
- Full multi-provider import coverage
- Stake sizing

This step is only about measurement discipline and data foundations.

---

## Recommended Next Implementation Task

After this design is accepted, the next code step should be:

1. Add additive SQLite migrations for:
   - `historical_matches`
   - `historical_market_lines`
   - `historical_team_snapshots`
   - `historical_provider_coverage`
   - `historical_import_runs`
2. Add thin async DB helpers for insert/upsert/select.
3. Add one importer scaffold that can populate a small slice of
   `historical_matches` plus `historical_market_lines`.

That creates the minimum base needed for the next roadmap point.
