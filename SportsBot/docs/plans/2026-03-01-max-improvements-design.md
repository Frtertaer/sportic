# SportsBot — Maximum Improvements Design
Date: 2026-03-01

## Overview
Comprehensive upgrade across 4 blocks: data quality fixes, UX improvements,
team alias expansion, and AI prompt engineering.

## Block 1 — Data (Critical Fixes)

### 1.1 Asian Handicap from Betexplorer
- Add 4th odds endpoint: `/match-odds/{id}/0/ah/bestOdds/`
- Parse `data-handicap` attributes, pick most common line (0.5, -0.5, -1, +1)
- Populate `OddsData.asian_handicap` as string: `"Arsenal -0.5 (1.85/1.95)"`
- Files: `scrapers/oddsportal.py`, `models.py`

### 1.2 Expand Betexplorer League Coverage 12 → 25
- Add: Championship, Serie B, 2. Bundesliga, Ligue 2, MLS, Brasileirao,
  Liga Conferencia, J-League, K-League, Liga Profesional Argentina, Allsvenskan, Scottish Premiership, Belgian Pro League
- Change sequential search to parallel `asyncio.gather` on all leagues simultaneously
- Files: `scrapers/oddsportal.py`

### 1.3 Brier O/U and BTTS
- In `mark_result`: parse goals from score "2-1" → store `actual_goals_home`, `actual_goals_away`
- Derive `actual_over_2_5` and `actual_btts` from goals
- In `_recompute_calibration_sync`: remove `pass` stubs, compute real Brier O/U and BTTS
- Files: `database.py`

## Block 2 — UX

### 2.1 Live Scraping Progress
- Use `asyncio.Queue` + progress updater coroutine
- Message updates every 2s showing per-scraper status (⏳/✅/❌)
- Files: `bot.py`, `scrapers/__init__.py`

### 2.2 /history Command
- `"/history Arsenal Chelsea"` → last 5 predictions for this pair with results
- Query `predictions` table ORDER BY predicted_at DESC LIMIT 5
- Files: `bot.py`, `database.py`, `formatter.py`

### 2.3 Date-Aware Cache
- Cache key: `{team1}_{team2}_{YYYY-MM-DD}` instead of just `{team1}_{team2}`
- Predictions from yesterday don't return for today's match
- Files: `cache.py`, `bot.py`

### 2.4 Persistent Rate Limit
- Move `_USER_LAST_REQUEST` from in-memory dict to SQLite `rate_limits` table
- Survives bot restarts
- Files: `database.py`, `bot.py`

## Block 3 — Team Aliases

### 3.1 Expanded Alias Dict (~50 entries)
English variants: Man City, Man Utd, PSG, Atletico, Spurs, Barca, etc.
Russian variants: Реал → Real Madrid, Барса → Barcelona, МЮ → Manchester United, etc.
Applied in both `team_parser.py` and scraper `_SEARCH_ALIASES`
- Files: `team_parser.py`, `scrapers/flashscore.py`

## Block 4 — AI Prompt

### 4.1 Improved System Prompt
- League-specific weights (EPL: weaker home factor; La Liga: possession → less BTTS)
- AH mapping table: win_prob > 65% → -0.5, > 75% → -1, < 40% → +0.5
- "Trap match" detection instruction
- Weighted xG formula: `adjusted_xG = base_xG × home_factor × form_weight × absence_penalty`
- Files: `analyzer.py`

## Files Changed
| File | Changes |
|------|---------|
| scrapers/oddsportal.py | +AH endpoint, parallel league search, +13 leagues |
| models.py | +asian_handicap_line, home/away AH odds fields |
| database.py | +actual_goals_home/away, actual_btts, actual_over_2_5; Brier O/U+BTTS; /history query; rate_limits table |
| bot.py | +/history handler, live progress, persistent rate limit |
| formatter.py | +progress block, updated AH block with real odds |
| analyzer.py | improved system prompt |
| team_parser.py | +50 aliases |
| cache.py | date in cache key |
| scrapers/__init__.py | progress queue integration |

## Constraints
- No new Python dependencies
- All scrapers still never raise (scrape_success=False on failure)
- Telegram 4096 char message limit respected
- asyncio throughout (no blocking calls on main thread)
