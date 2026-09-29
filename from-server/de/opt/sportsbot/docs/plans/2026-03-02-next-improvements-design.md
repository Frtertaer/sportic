# SportsBot — Next Improvements Design
**Date:** 2026-03-02
**Status:** Approved

---

## Overview

Five targeted improvements to the Football Analyst Telegram Bot, addressing:
1. League auto-detection — makes existing AI baselines functional
2. Value bets — compares AI probabilities vs bookmaker implied odds
3. AH line display — shows scraped market line alongside AI recommendation
4. `/schedule` command — browse today's matches
5. Push notifications — notify users when results are auto-resolved

---

## Feature 1: League Auto-Detection

### Problem
`analyzer.py` system prompt contains league-specific baselines (9 leagues with avg goals, BTTS rate, draw rate), but Claude never receives the league name. Those baselines are dead weight.

### Solution
Fotmob returns tournament/league data inside each team's `overview.teamForm` entries. Extract it in `flashscore.py` and propagate through the data pipeline.

### Data Flow
```
Fotmob API → teamForm[0].tooltipText.tournament (or leagueName)
  → FlashscoreData.league_name: str | None
  → analyzer._build_data_context():
      "League: Premier League"
  → Claude applies correct baseline from system prompt
```

### Changes
| File | Change |
|------|--------|
| `models.py` | `FlashscoreData` + `league_name: str \| None = None` |
| `scrapers/flashscore.py` | Extract tournament name from `teamForm` entries |
| `analyzer.py` | Add `League: {name}` line to data context section 1 |

### Edge Cases
- If no league name found → `None`, data context simply omits the line
- Neutral venue matches (no home/away) → label as-is

---

## Feature 2: Value Bets

### Problem
The bot predicts probabilities but never identifies when they diverge significantly from bookmaker implied odds — the key signal for finding value.

### Solution
After `analyze_match()` receives Claude's JSON output, compare `prob_w1/draw/w2` to `OddsData.implied_prob_w1/draw/w2`. Any divergence > 5% becomes a value bet entry.

### Logic
```python
# In analyzer.py, after json.loads():
for market, claude_pct, implied_pct, label in [
    ("W1", report.prob_w1, odds.implied_prob_w1, team1),
    ("Draw", report.prob_draw, odds.implied_prob_draw, "Ничья"),
    ("W2", report.prob_w2, odds.implied_prob_w2, team2),
]:
    edge = claude_pct - implied_pct
    if edge >= 5.0:
        value_bets.append(f"{label}: {claude_pct:.0f}% vs {implied_pct:.0f}% бук. (+{edge:.0f}% edge)")
```

### Display in `format_report()`
```
💎 ЦЕННЫЕ СТАВКИ
  ✅ Arsenal Win — 52% vs 40% бук. (+12% edge)
  ✅ Ничья — 28% vs 22% бук. (+6% edge)
```
If no value bets found, section is omitted.

### Changes
| File | Change |
|------|--------|
| `models.py` | `PredictionReport` + `value_bets: list[str] = []` |
| `analyzer.py` | Post-process: compute value bets from odds comparison |
| `formatter.py` | New `💎 ЦЕННЫЕ СТАВКИ` section (conditional) |

---

## Feature 3: AH Line Display in Report

### Problem
`OddsData.asian_handicap` contains the scraped market AH line (e.g. `"-0.5"`) and `OddsData.asian_handicap_odds` has the price, but `format_report()` only shows Claude's recommended line — ignoring the actual market.

### Solution
Pass market AH data through `PredictionReport`. Set it in `analyze_match()` from `match_data.odds`. Display alongside Claude's recommendation.

### Display
```
🏴 АЗИАТСКИЙ ГАНДИКАП
  Линия рынка:  Arsenal -0.5 @ 1.87    ← from OddsData
  Рекомендация: Arsenal -0.5           ← from Claude
  Уверенность: 🟡 MEDIUM
```

### Changes
| File | Change |
|------|--------|
| `models.py` | `PredictionReport` + `market_ah_line: str \| None = None`, `market_ah_odds: float \| None = None` |
| `analyzer.py` | Set fields from `match_data.odds` before returning report |
| `formatter.py` | Show "Линия рынка" line when `market_ah_line` is not None |

---

## Feature 4: `/schedule` Command

### Problem
Users must know exact team names and spellings in advance. A `/schedule` command lets them discover today's matches and copy-paste the predict command.

### Solution
Fotmob's public endpoint `https://www.fotmob.com/api/matches?date=YYYYMMDD` returns all matches grouped by league. Filter to top leagues (PL, Bundesliga, LaLiga, Serie A, Ligue 1, UCL, UEL).

### Display
```
⚽ МАТЧИ СЕГОДНЯ — 02.03.2026

🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League
  15:00  Arsenal vs Chelsea
  17:30  Man City vs Liverpool

🇩🇪 Bundesliga
  19:30  Bayern Munich vs Borussia Dortmund

💡 Для прогноза: /predict Arsenal vs Chelsea
```

### Architecture
```
scrapers/fotmob.py
  + async fetch_today_matches(date: str) -> list[LeagueSchedule]
    LeagueSchedule: dataclass(league_name, country_flag, matches: list[ScheduledMatch])
    ScheduledMatch: dataclass(time_utc, home, away)

formatter.py
  + format_schedule(leagues: list[LeagueSchedule]) -> str

bot.py
  + schedule_handler()
  + CommandHandler("schedule", schedule_handler)
  + /start and /help updated
```

### Supported Leagues (hardcoded filter list)
Premier League, Bundesliga, LaLiga, Serie A, Ligue 1, Champions League, Europa League, Championship, Eredivisie, Primeira Liga

### Changes
| File | Change |
|------|--------|
| `scrapers/fotmob.py` | `fetch_today_matches()` + dataclasses |
| `formatter.py` | `format_schedule()` |
| `bot.py` | `schedule_handler()` + CommandHandler + help text |

---

## Feature 5: Push Notifications

### Problem
`result_fetcher.py` silently auto-resolves results every 3h. Users have no way to know their prediction was evaluated.

### Solution
Store `user_id` in `predictions` table (idempotent migration). When `check_pending_results()` resolves a result, return `(user_id, team1, team2, score, top_rec, is_correct)`. `check_results_job()` sends a Telegram notification to each user.

### Notification Message
```
📬 Результат матча!

Arsenal 2-1 Chelsea
Ваша ставка: Arsenal Win — ✅ ВЕРНО!

📊 Точность: 15/31 матчей (48.4%)

/stats — полная статистика
```

If wrong:
```
📬 Результат матча!

Arsenal 0-2 Chelsea
Ваша ставка: Arsenal Win — ❌ Неверно

📊 Точность: 14/31 матчей (45.2%)
```

### Data Flow
```
predictions table
  + user_id INTEGER            ← stored on /predict
  + notification_sent INTEGER DEFAULT 0  ← prevents double-notify

check_pending_results() → list[ResolvedResult(user_id, team1, team2, ...)]

check_results_job(context):
  for r in check_pending_results():
    await context.bot.send_message(r.user_id, format_result_notification(r))
    await db.mark_notification_sent(r.prediction_id)
```

### Changes
| File | Change |
|------|--------|
| `database.py` | Migration: add `user_id`, `notification_sent`; update `save_prediction()`, `get_unresolved()`, new `mark_notification_sent()` |
| `result_fetcher.py` | `check_pending_results()` returns list of resolved results |
| `formatter.py` | `format_result_notification(resolved)` |
| `bot.py` | Pass `user_id` to `save_prediction()`; send messages in `check_results_job()` |

---

## Implementation Order

1. **Feature 3** (AH line display) — pure display change, no new scraping, no schema change. Quick win.
2. **Feature 1** (league auto-detection) — models.py + flashscore.py + analyzer.py
3. **Feature 2** (value bets) — models.py + analyzer.py post-processing + formatter.py
4. **Feature 5** (push notifications) — database.py migration + result_fetcher.py + formatter.py + bot.py
5. **Feature 4** (/schedule command) — new fotmob function + formatter + bot handler

## Dependencies Between Features
- Features 1, 2, 3 all touch `models.py` — implement together to avoid migration conflicts
- Feature 5 depends on existing `result_fetcher.py` structure
- Feature 4 is fully independent (new endpoint + new handler)

## Verification

```bash
# Feature 1: league in context
python -c "
import asyncio
from scrapers import scrape_all_parallel
async def t():
    d = await scrape_all_parallel('Arsenal', 'Chelsea')
    print('League:', d.flashscore.league_name if d.flashscore else 'N/A')
asyncio.run(t())
"

# Feature 2+3: value bets + AH
python -c "
import asyncio
from scrapers import scrape_all_parallel
from analyzer import analyze_match
async def t():
    d = await scrape_all_parallel('Arsenal', 'Chelsea')
    r = await analyze_match(d)
    print('Value bets:', r.value_bets)
    print('Market AH:', r.market_ah_line, '@', r.market_ah_odds)
asyncio.run(t())
"

# Feature 4: schedule
python -c "
import asyncio
from scrapers.fotmob import fetch_today_matches
async def t():
    leagues = await fetch_today_matches()
    for l in leagues[:3]:
        print(l.league_name, ':', len(l.matches), 'matches')
asyncio.run(t())
"

# Feature 5: notifications in DB
python -c "
import asyncio
from database import init_db, save_prediction
async def t():
    await init_db()
    print('DB schema OK with user_id column')
asyncio.run(t())
"

# Full bot: /schedule, /predict (value bets visible), /stats, /history
python bot.py
```
