# Next Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add 5 improvements: league auto-detection, value bets, AH market line display, `/schedule` command, and push notifications for auto-resolved results.

**Architecture:** All 5 features extend existing modules without introducing new dependencies. Features 1-4 touch `models.py`, `analyzer.py`, `formatter.py` in a deliberate order to avoid conflicts. Feature 5 (notifications) extends the DB schema + result_fetcher pipeline. Feature 6 (schedule) adds a new Fotmob endpoint + handler.

**Tech Stack:** Python 3.11, asyncio, SQLite (via `asyncio.to_thread`), python-telegram-bot v22.5, aiohttp, Pydantic v2

---

## Project Layout

```
SportsBot/
├── models.py            ← shared Pydantic contracts (touch first)
├── analyzer.py          ← Claude integration, data context builder
├── formatter.py         ← Telegram HTML message formatter
├── database.py          ← SQLite async wrapper
├── result_fetcher.py    ← background Fotmob result checker
├── bot.py               ← Telegram entry point + handlers
├── scrapers/
│   ├── flashscore.py    ← Fotmob form/H2H scraper
│   └── fotmob.py        ← shared Fotmob helpers (add schedule here)
└── tests/
    ├── test_formatter.py
    ├── test_analyzer.py
    └── test_fotmob_helper.py
```

Run tests with: `python -m pytest tests/ -v` (from `C:\Users\user\Desktop\SportsBot`)

---

## Task 1: Extend Data Models

**Files:**
- Modify: `models.py`
- Test: `tests/test_models.py` (create)

### Step 1: Write failing tests

Create `tests/test_models.py`:

```python
from __future__ import annotations
import unittest
from models import FlashscoreData, PredictionReport


class ModelFieldTests(unittest.TestCase):
    def test_flashscore_has_league_name(self):
        fs = FlashscoreData(team1_name="Arsenal", team2_name="Chelsea")
        self.assertIsNone(fs.league_name)

    def test_flashscore_league_name_set(self):
        fs = FlashscoreData(
            team1_name="Arsenal", team2_name="Chelsea",
            league_name="Premier League"
        )
        self.assertEqual(fs.league_name, "Premier League")

    def _make_report(self, **kwargs) -> PredictionReport:
        defaults = dict(
            team1="A", team2="B",
            prob_w1=50.0, prob_draw=25.0, prob_w2=25.0, result_confidence="MEDIUM",
            prob_over_2_5=55.0, prob_under_2_5=45.0, expected_goals=2.5,
            goals_confidence="MEDIUM", asian_handicap_rec="A -0.5",
            asian_handicap_confidence="MEDIUM", prob_btts_yes=50.0,
            prob_btts_no=50.0, btts_confidence="MEDIUM",
            top_recommendation="A Win", top_confidence_pct=55.0,
            risk_level="MEDIUM", analysis_summary="Test",
        )
        defaults.update(kwargs)
        return PredictionReport(**defaults)

    def test_prediction_report_has_value_bets(self):
        r = self._make_report()
        self.assertEqual(r.value_bets, [])

    def test_prediction_report_value_bets_set(self):
        r = self._make_report(value_bets=["Arsenal Win: 55% vs 42% бук. (+13% edge)"])
        self.assertEqual(len(r.value_bets), 1)

    def test_prediction_report_has_market_ah_fields(self):
        r = self._make_report()
        self.assertIsNone(r.market_ah_line)
        self.assertIsNone(r.market_ah_odds)

    def test_prediction_report_market_ah_set(self):
        r = self._make_report(market_ah_line="Arsenal -0.5", market_ah_odds=1.87)
        self.assertEqual(r.market_ah_line, "Arsenal -0.5")
        self.assertAlmostEqual(r.market_ah_odds, 1.87)


if __name__ == "__main__":
    unittest.main()
```

### Step 2: Run to verify failure

```
cd C:\Users\user\Desktop\SportsBot
python -m pytest tests/test_models.py -v
```

Expected: FAIL — `FlashscoreData` and `PredictionReport` missing fields.

### Step 3: Add fields to `models.py`

In `FlashscoreData` (after `error_message` field, around line 68):

```python
    league_name: Optional[str] = None
```

In `PredictionReport` (after `data_warnings` field, at the end of the class):

```python
    # New: enriched output fields
    value_bets: list[str] = Field(default_factory=list)
    market_ah_line: Optional[str] = None
    market_ah_odds: Optional[float] = None
```

### Step 4: Run tests to verify pass

```
python -m pytest tests/test_models.py -v
```

Expected: 7 tests PASS.

### Step 5: Commit

```bash
git add models.py tests/test_models.py
git commit -m "feat: extend models with league_name, value_bets, market_ah fields"
```

---

## Task 2: AH Market Line Display (Feature 3)

Show the scraped bookmaker AH line alongside Claude's recommendation in the report.

**Files:**
- Modify: `analyzer.py` lines ~603-608 (inside `analyze_match()`, before `return PredictionReport(...)`)
- Modify: `formatter.py` lines ~91-97 (AH section in `format_report()`)
- Test: `tests/test_formatter.py`

### Step 1: Write failing test

Add to `tests/test_formatter.py`:

```python
from models import PredictionReport
from formatter import format_report

def _base_report(**kwargs):
    defaults = dict(
        team1="Arsenal", team2="Chelsea",
        prob_w1=52.0, prob_draw=24.0, prob_w2=24.0, result_confidence="MEDIUM",
        prob_over_2_5=61.0, prob_under_2_5=39.0, expected_goals=2.8,
        goals_confidence="MEDIUM",
        asian_handicap_rec="Arsenal -0.5", asian_handicap_confidence="MEDIUM",
        prob_btts_yes=58.0, prob_btts_no=42.0, btts_confidence="MEDIUM",
        top_recommendation="Arsenal Win", top_confidence_pct=74.0,
        risk_level="LOW", analysis_summary="Arsenal in strong form.",
    )
    defaults.update(kwargs)
    return PredictionReport(**defaults)

class AHLineDisplayTest(unittest.TestCase):
    def test_market_ah_line_shown_when_present(self):
        report = _base_report(market_ah_line="Arsenal -0.5", market_ah_odds=1.87)
        text = format_report(report)
        self.assertIn("Линия рынка", text)
        self.assertIn("Arsenal -0.5", text)
        self.assertIn("1.87", text)

    def test_market_ah_line_hidden_when_absent(self):
        report = _base_report()  # market_ah_line=None
        text = format_report(report)
        self.assertNotIn("Линия рынка", text)
```

Run: `python -m pytest tests/test_formatter.py::AHLineDisplayTest -v`
Expected: FAIL.

### Step 2: Update `analyzer.py` — inject market AH into report_dict

In `analyze_match()`, find the block around line 603-608:

```python
        report_dict["team1"] = data.team1
        report_dict["team2"] = data.team2
        if not report_dict.get("data_warnings"):
            report_dict["data_warnings"] = warnings

        return PredictionReport(**report_dict)
```

Replace with:

```python
        report_dict["team1"] = data.team1
        report_dict["team2"] = data.team2
        if not report_dict.get("data_warnings"):
            report_dict["data_warnings"] = warnings

        # Inject scraped AH market line into report
        if data.odds and data.odds.scrape_success and data.odds.asian_handicap:
            report_dict.setdefault("market_ah_line", data.odds.asian_handicap)
            report_dict.setdefault(
                "market_ah_odds", data.odds.asian_handicap_home_odds
            )

        return PredictionReport(**report_dict)
```

### Step 3: Update `formatter.py` — show market AH line

Find the AH section in `format_report()` (around lines 91-97):

```python
    # Asian handicap
    lines += [
        "🏴 <b>Азиатский гандикап</b>",
        f"  Рекомендация: <b>{report.asian_handicap_rec}</b>",
        f"  Уверенность: {_badge(report.asian_handicap_confidence)}",
        "",
    ]
```

Replace with:

```python
    # Asian handicap
    ah_lines = ["🏴 <b>Азиатский гандикап</b>"]
    if report.market_ah_line:
        odds_str = (
            f" @ <b>{report.market_ah_odds:.2f}</b>"
            if report.market_ah_odds else ""
        )
        ah_lines.append(f"  Линия рынка: <b>{report.market_ah_line}</b>{odds_str}")
    ah_lines += [
        f"  Рекомендация: <b>{report.asian_handicap_rec}</b>",
        f"  Уверенность: {_badge(report.asian_handicap_confidence)}",
        "",
    ]
    lines += ah_lines
```

### Step 4: Run tests

```
python -m pytest tests/test_formatter.py -v
```

Expected: all formatter tests PASS.

### Step 5: Commit

```bash
git add analyzer.py formatter.py tests/test_formatter.py
git commit -m "feat: show scraped AH market line in prediction report"
```

---

## Task 3: League Auto-Detection (Feature 1)

Extract the league/tournament name from Fotmob's `teamForm` data and inject it into the AI prompt.

**Files:**
- Modify: `scrapers/flashscore.py` — extract `league_name` in `scrape()`
- Modify: `analyzer.py` — add "League:" line in `_build_data_context()`
- Test: `tests/test_fotmob_helper.py` (add one test)

### Step 1: Write failing test

Add to `tests/test_fotmob_helper.py`:

```python
class LeagueExtractionTest(unittest.TestCase):
    def test_league_name_extracted_from_team_form(self):
        """Simulate the teamForm structure Fotmob returns."""
        mock_team_data = {
            "overview": {
                "teamForm": [
                    {
                        "tournament": {"name": "Premier League"},
                        "resultString": "W",
                        "tooltipText": {
                            "homeTeam": "Arsenal", "awayTeam": "Chelsea",
                            "homeScore": 2, "awayScore": 1,
                        },
                        "home": {"id": 9825},
                        "date": {"utcTime": "2026-02-28T15:00:00Z"},
                    }
                ]
            }
        }
        # Import and call the extraction helper directly
        from scrapers.flashscore import FlashscoreScraper
        league = FlashscoreScraper._extract_league_name(mock_team_data)
        self.assertEqual(league, "Premier League")

    def test_league_name_none_when_missing(self):
        from scrapers.flashscore import FlashscoreScraper
        self.assertIsNone(FlashscoreScraper._extract_league_name({}))
```

Run: `python -m pytest tests/test_fotmob_helper.py -v -k league`
Expected: FAIL — `_extract_league_name` does not exist.

### Step 2: Add `_extract_league_name` to `scrapers/flashscore.py`

Add a new static method to `FlashscoreScraper`, after `_goal_averages` (around line 180):

```python
    @staticmethod
    def _extract_league_name(data: dict) -> Optional[str]:
        """Extract tournament/league name from first teamForm entry."""
        team_form = data.get("overview", {}).get("teamForm", [])
        if not team_form:
            return None
        entry = team_form[0]
        # Try multiple paths Fotmob may use
        return (
            entry.get("tournament", {}).get("name")
            or entry.get("leagueName")
            or entry.get("competition")
            or None
        )
```

Also add `from typing import Optional` to imports if not already present (it is).

### Step 3: Use it in `scrape()` and return via `FlashscoreData`

In `scrape()`, find the line that sets `t1_name` (around line 215):

```python
            t1_name = data1.get("details", {}).get("name", self.team1)
            t2_name = data2.get("details", {}).get("name", self.team2)
```

Add after those two lines:

```python
            league_name = self._extract_league_name(data1)
```

Then in the `return FlashscoreData(...)` block, add the new field:

```python
            return FlashscoreData(
                team1_name=t1_name,
                team2_name=t2_name,
                team1_form=t1_form,
                team2_form=t2_form,
                h2h_matches=h2h,
                team1_goals_scored_avg=t1_scored,
                team1_goals_conceded_avg=t1_conceded,
                team2_goals_scored_avg=t2_scored,
                team2_goals_conceded_avg=t2_conceded,
                league_name=league_name,      # ← new
                scrape_success=True,
            )
```

### Step 4: Inject league into data context in `analyzer.py`

In `_build_data_context()`, find the lines that print goal averages (around lines 237-244):

```python
        lines.append(
            f"\n{data.team1} goals per game: "
            f"{fs.team1_goals_scored_avg} scored, {fs.team1_goals_conceded_avg} conceded"
        )
```

Insert BEFORE these goal average lines:

```python
        if fs.league_name:
            lines.append(f"\nLeague/Competition: {fs.league_name}")
```

### Step 5: Run all tests

```
python -m pytest tests/ -v
```

Expected: all tests PASS.

### Step 6: Commit

```bash
git add scrapers/flashscore.py analyzer.py tests/test_fotmob_helper.py
git commit -m "feat: extract league name from Fotmob and inject into AI prompt"
```

---

## Task 4: Value Bets (Feature 2)

Compare Claude's probabilities to bookmaker implied odds and display any edge ≥ 5%.

**Files:**
- Modify: `analyzer.py` — compute `value_bets` after probability normalisation
- Modify: `formatter.py` — add conditional `💎 ЦЕННЫЕ СТАВКИ` section
- Test: `tests/test_formatter.py` and `tests/test_analyzer.py`

Note: `OddsData.implied_prob_w1/draw/w2` are floats in range **0.0–1.0** (fractions).
Claude's probabilities in `report_dict` are in range **0–100** (percentages).

### Step 1: Write failing formatter test

Add to `tests/test_formatter.py`:

```python
class ValueBetsTest(unittest.TestCase):
    def test_value_bets_section_shown_when_present(self):
        report = _base_report(
            value_bets=["Arsenal Win: 55% vs 42% бук. (+13% edge)"]
        )
        text = format_report(report)
        self.assertIn("ЦЕННЫЕ СТАВКИ", text)
        self.assertIn("+13%", text)

    def test_value_bets_section_hidden_when_empty(self):
        report = _base_report()  # value_bets=[]
        text = format_report(report)
        self.assertNotIn("ЦЕННЫЕ СТАВКИ", text)
```

Run: `python -m pytest tests/test_formatter.py::ValueBetsTest -v`
Expected: FAIL.

### Step 2: Add `💎 ЦЕННЫЕ СТАВКИ` section to `formatter.py`

In `format_report()`, find the data-warnings block (around lines 119-124):

```python
    # ── Data warnings ─────────────────────────────────────────────────────────
    if report.data_warnings:
```

Insert BEFORE the data-warnings section (after the BTTS closing `""`):

```python
    # ── Value bets ────────────────────────────────────────────────────────────
    if report.value_bets:
        lines += [sep, "💎 <b>ЦЕННЫЕ СТАВКИ</b>", ""]
        for vb in report.value_bets:
            lines.append(f"  ✅ {vb}")
        lines.append("")
```

### Step 3: Run formatter test to verify

```
python -m pytest tests/test_formatter.py -v
```

Expected: all PASS.

### Step 4: Write analyzer test for value bet computation

Add to `tests/test_analyzer.py`:

```python
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from models import MatchData, OddsData, FlashscoreData


class ValueBetComputationTest(unittest.IsolatedAsyncioTestCase):
    async def test_value_bets_computed_when_edge_exceeds_5pct(self):
        """When Claude says 55% but bookie implies 42%, that's a +13% edge → value bet."""
        from analyzer import analyze_match

        mock_odds = OddsData(
            implied_prob_w1=0.42,    # 42%
            implied_prob_draw=0.28,
            implied_prob_w2=0.30,
            scrape_success=True,
        )
        match_data = MatchData(team1="Arsenal", team2="Chelsea", odds=mock_odds)

        mock_report = {
            "prob_w1": 55.0, "prob_draw": 25.0, "prob_w2": 20.0,
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 55.0, "prob_under_2_5": 45.0, "expected_goals": 2.5,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Arsenal -0.5", "asian_handicap_confidence": "MEDIUM",
            "prob_btts_yes": 50.0, "prob_btts_no": 50.0, "btts_confidence": "MEDIUM",
            "top_recommendation": "Arsenal Win", "top_confidence_pct": 65.0,
            "risk_level": "MEDIUM", "analysis_summary": "Arsenal strong.",
            "team1_strengths": [], "team1_weaknesses": [],
            "team2_strengths": [], "team2_weaknesses": [],
        }

        with patch("analyzer._generate_report_dict", new=AsyncMock(return_value=mock_report)):
            with patch("analyzer._calibration_block", new=AsyncMock(return_value="")):
                with patch("analyzer._team_memory_block", new=AsyncMock(return_value="")):
                    with patch("analyzer._save_team_memory", new=AsyncMock()):
                        report = await analyze_match(match_data)

        # 55% vs 42% = +13% edge → should be flagged
        self.assertTrue(any("Arsenal" in vb for vb in report.value_bets))
        self.assertTrue(any("+13" in vb for vb in report.value_bets))

    async def test_no_value_bets_when_edge_below_5pct(self):
        """When difference is < 5%, no value bet."""
        from analyzer import analyze_match

        mock_odds = OddsData(
            implied_prob_w1=0.50,   # 50%
            implied_prob_draw=0.28,
            implied_prob_w2=0.22,
            scrape_success=True,
        )
        match_data = MatchData(team1="Arsenal", team2="Chelsea", odds=mock_odds)

        mock_report = {
            "prob_w1": 53.0, "prob_draw": 25.0, "prob_w2": 22.0,   # only +3% edge
            "result_confidence": "MEDIUM",
            "prob_over_2_5": 55.0, "prob_under_2_5": 45.0, "expected_goals": 2.5,
            "goals_confidence": "MEDIUM",
            "asian_handicap_rec": "Level", "asian_handicap_confidence": "LOW",
            "prob_btts_yes": 50.0, "prob_btts_no": 50.0, "btts_confidence": "LOW",
            "top_recommendation": "Draw", "top_confidence_pct": 50.0,
            "risk_level": "HIGH", "analysis_summary": "Tight match.",
            "team1_strengths": [], "team1_weaknesses": [],
            "team2_strengths": [], "team2_weaknesses": [],
        }

        with patch("analyzer._generate_report_dict", new=AsyncMock(return_value=mock_report)):
            with patch("analyzer._calibration_block", new=AsyncMock(return_value="")):
                with patch("analyzer._team_memory_block", new=AsyncMock(return_value="")):
                    with patch("analyzer._save_team_memory", new=AsyncMock()):
                        report = await analyze_match(match_data)

        self.assertEqual(report.value_bets, [])
```

Run: `python -m pytest tests/test_analyzer.py::ValueBetComputationTest -v`
Expected: FAIL — `value_bets` not yet computed.

### Step 5: Add value bet computation to `analyzer.py`

In `analyze_match()`, find the probability normalisation block (around lines 591-601):

```python
        # Normalise probability sums
        for keys, _label in [...]:
            ...

        report_dict["team1"] = data.team1
```

Insert AFTER the normalisation block, BEFORE `report_dict["team1"] = ...`:

```python
        # Compute value bets: flag any market where our edge ≥ 5%
        value_bets: list[str] = []
        if data.odds and data.odds.scrape_success:
            _odds = data.odds
            _markets = [
                ("W1",   report_dict.get("prob_w1", 0),   _odds.implied_prob_w1,   data.team1),
                ("Draw", report_dict.get("prob_draw", 0),  _odds.implied_prob_draw, "Ничья"),
                ("W2",   report_dict.get("prob_w2", 0),   _odds.implied_prob_w2,   data.team2),
            ]
            for _key, _claude_pct, _implied, _label in _markets:
                if _implied is None:
                    continue
                _implied_pct = _implied * 100.0
                _edge = _claude_pct - _implied_pct
                if _edge >= 5.0:
                    value_bets.append(
                        f"{_label}: {_claude_pct:.0f}% vs {_implied_pct:.0f}% бук."
                        f" (+{_edge:.0f}% edge)"
                    )
        report_dict["value_bets"] = value_bets
```

### Step 6: Run all tests

```
python -m pytest tests/ -v
```

Expected: all PASS.

### Step 7: Commit

```bash
git add analyzer.py formatter.py tests/test_formatter.py tests/test_analyzer.py
git commit -m "feat: compute and display value bets when edge >= 5% vs bookmaker"
```

---

## Task 5: Push Notifications (Feature 5)

When `result_fetcher.py` auto-resolves a match result, send the user a Telegram notification.

**Files:**
- Modify: `database.py` — add migration for `user_id` + `notification_sent`; update `save_prediction()`, `get_unresolved()`; add `mark_notification_sent()`
- Modify: `result_fetcher.py` — return list of resolved dicts instead of int
- Modify: `formatter.py` — add `format_result_notification()`
- Modify: `bot.py` — pass `user_id` to `save_prediction()`; send notifications in `check_results_job()`
- Test: `tests/test_formatter.py`

### Step 1: Write failing test for `format_result_notification`

Add to `tests/test_formatter.py`:

```python
class ResultNotificationTest(unittest.TestCase):
    def test_correct_prediction_notification(self):
        from formatter import format_result_notification
        resolved = {
            "team1": "Arsenal", "team2": "Chelsea",
            "score": "2-1", "top_rec": "Arsenal Win",
            "actual_result": "W1",
        }
        text = format_result_notification(resolved)
        self.assertIn("Arsenal", text)
        self.assertIn("2-1", text)
        self.assertIn("Arsenal Win", text)
        self.assertIn("Результат", text)

    def test_wrong_prediction_notification(self):
        from formatter import format_result_notification
        resolved = {
            "team1": "Arsenal", "team2": "Chelsea",
            "score": "0-2", "top_rec": "Arsenal Win",
            "actual_result": "W2",
        }
        text = format_result_notification(resolved)
        self.assertIn("Chelsea", text)
        self.assertIn("0-2", text)
```

Run: `python -m pytest tests/test_formatter.py::ResultNotificationTest -v`
Expected: FAIL.

### Step 2: Add `format_result_notification` to `formatter.py`

Add at the end of `formatter.py`:

```python

def format_result_notification(resolved: dict) -> str:
    """Format a push notification for an auto-resolved match result."""
    team1 = resolved.get("team1", "?")
    team2 = resolved.get("team2", "?")
    score = resolved.get("score", "?-?")
    top_rec = resolved.get("top_rec") or "—"
    actual_result = resolved.get("actual_result", "")
    acc_line = resolved.get("acc_line", "")  # optional accuracy summary

    # Determine if top recommendation matched the result
    # Simple heuristic: W1 → team1 won, W2 → team2 won, DRAW → draw
    rec_lower = top_rec.lower()
    if actual_result == "W1":
        correct = team1.lower() in rec_lower or "win" in rec_lower
    elif actual_result == "W2":
        correct = team2.lower() in rec_lower
    elif actual_result == "DRAW":
        correct = "draw" in rec_lower or "ничья" in rec_lower
    else:
        correct = False

    outcome_icon = "✅" if correct else "❌"
    outcome_word = "ВЕРНО!" if correct else "Неверно"

    lines = [
        "📬 <b>Результат матча!</b>",
        "",
        f"⚽ <b>{team1} {score} {team2}</b>",
        f"🏆 Ваша ставка: <b>{top_rec}</b> — {outcome_icon} {outcome_word}",
    ]
    if acc_line:
        lines += ["", f"📊 {acc_line}"]
    lines += ["", "<i>/stats — полная статистика</i>"]
    return "\n".join(lines)
```

Run: `python -m pytest tests/test_formatter.py -v`
Expected: all PASS.

### Step 3: Add DB migration for `user_id` and `notification_sent`

In `database.py`, find `_create_tables()` → the `# Idempotent migrations` section (around line 89):

```python
    # Idempotent migrations for existing databases
    for col, coltype in [
        ("actual_goals_home", "INTEGER"),
        ("actual_goals_away", "INTEGER"),
    ]:
```

Extend the list to include new columns:

```python
    # Idempotent migrations for existing databases
    for col, coltype in [
        ("actual_goals_home",  "INTEGER"),
        ("actual_goals_away",  "INTEGER"),
        ("user_id",            "INTEGER"),
        ("notification_sent",  "INTEGER DEFAULT 0"),
    ]:
```

### Step 4: Update `save_prediction()` to accept `user_id`

Find the `_save_prediction_sync` function in `database.py`. Its signature currently is:

```python
def _save_prediction_sync(report, completeness: float) -> int:
```

Change to:

```python
def _save_prediction_sync(report, completeness: float, user_id: Optional[int] = None) -> int:
```

And in the INSERT statement, add `user_id` to the columns and values. The existing INSERT should look something like:

```python
    cursor = conn.execute(
        """INSERT INTO predictions
           (team1, team2, predicted_at, prob_w1, prob_draw, prob_w2,
            prob_over_2_5, prob_btts_yes, top_rec, completeness)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (report.team1, report.team2, _now_utc(),
         report.prob_w1, report.prob_draw, report.prob_w2,
         report.prob_over_2_5, report.prob_btts_yes,
         report.top_recommendation, completeness),
    )
```

Add `user_id` to the INSERT:

```python
    cursor = conn.execute(
        """INSERT INTO predictions
           (team1, team2, predicted_at, prob_w1, prob_draw, prob_w2,
            prob_over_2_5, prob_btts_yes, top_rec, completeness, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (report.team1, report.team2, _now_utc(),
         report.prob_w1, report.prob_draw, report.prob_w2,
         report.prob_over_2_5, report.prob_btts_yes,
         report.top_recommendation, completeness, user_id),
    )
```

Update the async wrapper signature too:

```python
async def save_prediction(report, completeness: float, user_id: Optional[int] = None) -> int:
    return await asyncio.to_thread(_save_prediction_sync, report, completeness, user_id)
```

### Step 5: Add `mark_notification_sent()` and update `get_unresolved()` in `database.py`

Add after `mark_result`:

```python
def _mark_notification_sent_sync(prediction_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE predictions SET notification_sent=1 WHERE id=?",
            (prediction_id,),
        )
        conn.commit()


async def mark_notification_sent(prediction_id: int) -> None:
    await asyncio.to_thread(_mark_notification_sent_sync, prediction_id)
```

Update `get_unresolved()` to include `user_id` and `id` in the returned rows. Find `_get_unresolved_sync`:

```python
        rows = conn.execute(
            """SELECT team1, team2, predicted_at FROM predictions
               WHERE actual_result IS NULL AND ..."""
        ).fetchall()
        return [dict(r) for r in rows]
```

Add `id, user_id` to the SELECT:

```python
        rows = conn.execute(
            """SELECT id, team1, team2, predicted_at, user_id FROM predictions
               WHERE actual_result IS NULL AND
                     notification_sent = 0 AND ..."""
        ).fetchall()
        return [dict(r) for r in rows]
```

(Also add `notification_sent = 0` condition so already-notified predictions aren't re-checked.)

### Step 6: Update `result_fetcher.py` — return list instead of int

Change `check_pending_results()` to return `list[dict]` instead of `int`.

Current signature: `async def check_pending_results() -> int:`
New: `async def check_pending_results() -> list[dict]:`

Change the internal logic:

```python
async def check_pending_results() -> list[dict]:
    pending = await db.get_unresolved(older_than_hours=3.0)
    if not pending:
        logger.debug("No pending predictions to check for results")
        return []

    logger.info("Checking results for %d pending predictions", len(pending))
    resolved: list[dict] = []

    for pred in pending:
        try:
            result = await fetch_actual_result(
                pred["team1"], pred["team2"], pred["predicted_at"],
            )
            if result:
                actual_result, score = result
                stored = await db.mark_result(pred["team1"], pred["team2"], score)
                if stored:
                    await db.recompute_calibration()
                    stats = await db.get_calibration_stats()
                    acc_line = ""
                    if stats and stats.get("n_with_result", 0) > 0:
                        acc_line = (
                            f"Точность: {stats['n_with_result']} матчей, "
                            f"{stats['acc_1x2']:.1f}% (1X2)"
                        )
                    resolved.append({
                        "prediction_id": pred["id"],
                        "user_id":       pred.get("user_id"),
                        "team1":         pred["team1"],
                        "team2":         pred["team2"],
                        "score":         score,
                        "actual_result": actual_result,
                        "top_rec":       pred.get("top_rec"),
                        "acc_line":      acc_line,
                    })
                    logger.info(
                        "Auto-stored result: %s vs %s → %s",
                        pred["team1"], pred["team2"], score,
                    )
        except Exception as exc:
            logger.warning(
                "Result fetch failed for %s vs %s: %s",
                pred["team1"], pred["team2"], exc,
            )

    return resolved
```

Note: `pred.get("top_rec")` — `get_unresolved()` also needs to SELECT `top_rec`. Update that query too:
```sql
SELECT id, team1, team2, predicted_at, top_rec, user_id FROM predictions WHERE ...
```

### Step 7: Update `bot.py` — pass `user_id` and send notifications

In `predict_handler()`, find the DB save call (around line 206):

```python
        await db.save_prediction(report, match_data.data_completeness)
```

Change to:

```python
        await db.save_prediction(report, match_data.data_completeness, user_id=user_id)
```

In `check_results_job()`, the current code calls `check_pending_results()` and treats the result as `int`. Change to:

```python
async def check_results_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs every 3h to auto-fetch match results from Fotmob and notify users."""
    try:
        resolved_list = await check_pending_results()
        if resolved_list:
            logger.info(
                "Background result check: found %d new results", len(resolved_list)
            )
            for resolved in resolved_list:
                uid = resolved.get("user_id")
                if uid:
                    try:
                        from formatter import format_result_notification
                        msg = format_result_notification(resolved)
                        await context.bot.send_message(
                            chat_id=uid,
                            text=msg,
                            parse_mode=HTML,
                        )
                        await db.mark_notification_sent(resolved["prediction_id"])
                    except TelegramError as tg_exc:
                        logger.warning(
                            "Notification failed for user %d: %s", uid, tg_exc
                        )
    except Exception as exc:
        logger.warning("Background result check failed: %s", exc)
```

Also add `from formatter import format_result_notification` to `bot.py` imports (or keep it inline as above).

### Step 8: Run all tests

```
python -m pytest tests/ -v
```

Expected: all PASS.

### Step 9: Quick DB smoke test

```bash
python -c "
import asyncio
from database import init_db
async def t():
    await init_db()
    print('DB migration OK — user_id and notification_sent columns exist')
asyncio.run(t())
"
```

### Step 10: Commit

```bash
git add database.py result_fetcher.py formatter.py bot.py tests/test_formatter.py
git commit -m "feat: push notifications to users when match results are auto-resolved"
```

---

## Task 6: /schedule Command (Feature 4)

Add a `/schedule` command that shows today's matches from Fotmob, grouped by league.

**Files:**
- Modify: `scrapers/fotmob.py` — add `fetch_today_matches()` + two dataclasses
- Modify: `formatter.py` — add `format_schedule()`
- Modify: `bot.py` — add `schedule_handler()`, register CommandHandler, update /start and /help
- Test: `tests/test_formatter.py`

### Step 1: Write failing tests

Add to `tests/test_formatter.py`:

```python
class ScheduleFormatterTest(unittest.TestCase):
    def test_format_schedule_empty(self):
        from formatter import format_schedule
        text = format_schedule([])
        self.assertIn("нет матчей", text.lower())

    def test_format_schedule_shows_teams(self):
        from formatter import format_schedule
        from scrapers.fotmob import LeagueSchedule, ScheduledMatch

        leagues = [
            LeagueSchedule(
                league_name="Premier League",
                country_flag="🏴󠁧󠁢󠁥󠁮󠁧󠁿",
                matches=[
                    ScheduledMatch(time_utc="15:00", home="Arsenal", away="Chelsea"),
                    ScheduledMatch(time_utc="17:30", home="Man City", away="Liverpool"),
                ],
            )
        ]
        text = format_schedule(leagues)
        self.assertIn("Premier League", text)
        self.assertIn("Arsenal", text)
        self.assertIn("Chelsea", text)
        self.assertIn("15:00", text)
        self.assertIn("/predict", text)
```

Run: `python -m pytest tests/test_formatter.py::ScheduleFormatterTest -v`
Expected: FAIL — `LeagueSchedule`, `ScheduledMatch`, `format_schedule` don't exist.

### Step 2: Add dataclasses to `scrapers/fotmob.py`

At the top of `scrapers/fotmob.py`, after the imports (around line 15), add:

```python
from dataclasses import dataclass, field as dc_field
```

After all existing code (at the bottom of the file), add:

```python
@dataclass
class ScheduledMatch:
    time_utc: str   # e.g. "15:00"
    home: str
    away: str


@dataclass
class LeagueSchedule:
    league_name: str
    country_flag: str
    matches: list[ScheduledMatch] = dc_field(default_factory=list)


# Top leagues to include in /schedule (Fotmob league IDs + display info)
# Run: https://www.fotmob.com/api/matches?date=YYYYMMDD to see IDs
_SCHEDULE_LEAGUES: dict[int, tuple[str, str]] = {
    47:   ("Premier League",   "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    54:   ("Bundesliga",       "🇩🇪"),
    87:   ("La Liga",          "🇪🇸"),
    55:   ("Serie A",          "🇮🇹"),
    53:   ("Ligue 1",          "🇫🇷"),
    42:   ("Champions League", "🏆"),
    73:   ("Europa League",    "🥈"),
    48:   ("Championship",     "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    57:   ("Eredivisie",       "🇳🇱"),
    61:   ("Primeira Liga",    "🇵🇹"),
}

FOTMOB_MATCHES_URL = "https://www.fotmob.com/api/matches"


async def fetch_today_matches(
    scraper=None,
    date_str: Optional[str] = None,
) -> list[LeagueSchedule]:
    """
    Fetch today's matches from Fotmob grouped by league.
    Returns only leagues in _SCHEDULE_LEAGUES.

    scraper: optional BaseScraper instance (creates temp one if None)
    date_str: YYYYMMDD string, defaults to today UTC
    """
    import json as _json
    from datetime import date as _date, datetime as _datetime, timezone as _tz

    if date_str is None:
        date_str = _date.today().strftime("%Y%m%d")

    owned = scraper is None
    if owned:
        scraper = _TempScraper()

    try:
        raw = await scraper._fetch_with_retry(
            FOTMOB_MATCHES_URL,
            params={"date": date_str},
            headers=FOTMOB_HEADERS,
            json_mode=True,
        )
        data = _json.loads(raw)
    finally:
        if owned:
            await scraper._close()

    leagues_data = data.get("leagues", [])
    result: list[LeagueSchedule] = []

    for league in leagues_data:
        league_id = league.get("id")
        if league_id not in _SCHEDULE_LEAGUES:
            continue

        league_name, flag = _SCHEDULE_LEAGUES[league_id]
        matches_raw = league.get("matches", [])
        matches: list[ScheduledMatch] = []

        for m in matches_raw:
            status = m.get("status", {})
            if status.get("finished"):
                continue  # skip completed matches

            home_name = (m.get("home") or {}).get("name", "?")
            away_name = (m.get("away") or {}).get("name", "?")
            utc_time = status.get("utcTime", "")
            try:
                dt = _datetime.fromisoformat(
                    utc_time.replace("Z", "+00:00")
                ).astimezone(_tz.utc)
                time_str = dt.strftime("%H:%M")
            except (ValueError, AttributeError):
                time_str = "?"

            matches.append(ScheduledMatch(time_utc=time_str, home=home_name, away=away_name))

        if matches:
            result.append(LeagueSchedule(
                league_name=league_name,
                country_flag=flag,
                matches=matches,
            ))

    return result


class _TempScraper:
    """Minimal scraper-like object for fetch_today_matches when no scraper is passed."""
    DOMAIN = "fotmob.com"

    async def _fetch_with_retry(self, url, *, params=None, headers=None, json_mode=False):
        import aiohttp, asyncio
        full_url = url
        if params:
            import urllib.parse
            full_url = url + "?" + urllib.parse.urlencode(params)
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.text()

    async def _close(self):
        pass
```

### Step 3: Add `format_schedule()` to `formatter.py`

Add at the end of `formatter.py`:

```python

def format_schedule(leagues: list) -> str:
    """Format /schedule reply — today's matches grouped by league."""
    from datetime import date
    today_str = date.today().strftime("%d.%m.%Y")
    sep = "─" * 32
    lines = [f"⚽ <b>МАТЧИ СЕГОДНЯ — {today_str}</b>", sep, ""]

    if not leagues:
        lines.append("<i>Нет матчей в топ-лигах сегодня.</i>")
        return "\n".join(lines)

    for league in leagues:
        lines.append(f"{league.country_flag} <b>{league.league_name}</b>")
        for m in league.matches:
            lines.append(f"  {m.time_utc}  {m.home} vs {m.away}")
            lines.append(f"  <code>/predict {m.home} vs {m.away}</code>")
        lines.append("")

    lines += [sep, "<i>Время указано в UTC. /predict — прогноз матча.</i>"]

    message = "\n".join(lines)
    if len(message) > 4090:
        message = message[:4087] + "…"
    return message
```

### Step 4: Run tests

```
python -m pytest tests/test_formatter.py -v
```

Expected: all PASS including `ScheduleFormatterTest`.

### Step 5: Add `schedule_handler` to `bot.py`

In the imports section, add `fetch_today_matches` and `format_schedule` (or import inline).

Add the handler function (after `history_handler`):

```python
async def schedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedule — show today's top-league matches."""
    from scrapers.fotmob import fetch_today_matches
    from formatter import format_schedule

    msg = await update.message.reply_html("⏳ <b>Загружаю расписание...</b>")
    try:
        leagues = await fetch_today_matches()
        text = format_schedule(leagues)
    except Exception as exc:
        logger.warning("Schedule fetch failed: %s", exc)
        text = "❌ Не удалось загрузить расписание. Попробуйте позже."

    try:
        await msg.edit_text(text, parse_mode=HTML)
    except TelegramError as tg_exc:
        logger.warning("schedule edit_text failed: %s", tg_exc)
```

Register it in `main()`:

```python
    app.add_handler(CommandHandler("schedule", schedule_handler))
```

Update `/start` message to include `/schedule`:

```python
        "  /schedule — матчи сегодня\\n\"\n"
```

Update `/help` to include:

```python
        \"<b>/schedule</b> — список матчей на сегодня\\n\"\n"
        \"  Показывает топ-лиги: PL, Bundesliga, LaLiga и др.\\n\\n\"\n"
```

### Step 6: Run all tests

```
python -m pytest tests/ -v
```

Expected: all PASS.

### Step 7: Integration smoke test (no Telegram needed)

```bash
python -c "
import asyncio
from scrapers.fotmob import fetch_today_matches
from formatter import format_schedule

async def t():
    leagues = await fetch_today_matches()
    print(f'Found {len(leagues)} leagues with matches today')
    for l in leagues:
        print(f'  {l.country_flag} {l.league_name}: {len(l.matches)} matches')
    print()
    print(format_schedule(leagues))

asyncio.run(t())
"
```

### Step 8: Commit

```bash
git add scrapers/fotmob.py formatter.py bot.py tests/test_formatter.py
git commit -m "feat: add /schedule command showing today's top-league matches"
```

---

## Final Verification

### Run full test suite

```
python -m pytest tests/ -v --tb=short
```

Expected output:
```
tests/test_analyzer.py::ValueBetComputationTest::test_value_bets_computed_when_edge_exceeds_5pct PASSED
tests/test_analyzer.py::ValueBetComputationTest::test_no_value_bets_when_edge_below_5pct PASSED
tests/test_formatter.py::... PASSED  (all)
tests/test_fotmob_helper.py::LeagueExtractionTest::... PASSED
tests/test_models.py::ModelFieldTests::... PASSED  (all 7)
tests/test_team_parser.py::... PASSED
```

### Check git log

```
git log --oneline -6
```

Expected:
```
xxxxxxx feat: add /schedule command showing today's top-league matches
xxxxxxx feat: push notifications to users when match results are auto-resolved
xxxxxxx feat: compute and display value bets when edge >= 5% vs bookmaker
xxxxxxx feat: extract league name from Fotmob and inject into AI prompt
xxxxxxx feat: show scraped AH market line in prediction report
xxxxxxx feat: extend models with league_name, value_bets, market_ah fields
```

### Start bot and test in Telegram

```bash
python bot.py
```

Test commands:
- `/schedule` — should show today's matches
- `/predict Arsenal vs Chelsea` — report should include AH market line, value bets (if any), league in AI context
- After result: notification sent automatically on next background check

---

## Notes

- **Value bets threshold:** 5% edge minimum. Adjust in `analyzer.py` by changing `>= 5.0` if needed.
- **Schedule leagues:** Extend `_SCHEDULE_LEAGUES` dict in `scrapers/fotmob.py` with more Fotmob league IDs.
- **Notification privacy:** Users who made prediction before this feature was deployed have `user_id=NULL` in DB — they simply won't be notified (graceful degradation).
- **`_TempScraper`:** Minimal aiohttp wrapper for schedule fetching; if Fotmob blocks it, extend to use the full `BaseScraper` with UA rotation.
