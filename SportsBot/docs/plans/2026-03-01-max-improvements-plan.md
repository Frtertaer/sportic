# SportsBot Max Improvements — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement all 9 improvement areas across 4 blocks: AH odds, more leagues, Brier O/U+BTTS, live progress, /history, date-aware cache, persistent rate limit, expanded aliases, and improved AI prompt.

**Architecture:** Each task modifies a specific file or pair of files. Tasks are ordered by dependency: models first (others depend on it), then scrapers, then DB schema, then bot handlers, then AI prompt last.

**Tech Stack:** Python 3.11+, aiohttp, python-telegram-bot 22.5, sqlite3, anthropic SDK, Pydantic v2

---

## Task 1: Add Asian Handicap Fields to OddsData Model

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\models.py` (lines 107–123)

**Step 1: Edit OddsData in models.py**

Replace the `OddsData` class (from `class OddsData(BaseModel):` to end of the class) with:

```python
class OddsData(BaseModel):
    team1_win_odds: Optional[float] = None
    draw_odds: Optional[float] = None
    team2_win_odds: Optional[float] = None
    over_2_5_odds: Optional[float] = None
    under_2_5_odds: Optional[float] = None
    over_1_5_odds: Optional[float] = None
    under_1_5_odds: Optional[float] = None
    # Asian Handicap — real scraped values
    asian_handicap_line: Optional[float] = None      # e.g. -0.5, 0, +0.5, -1.0
    asian_handicap_home_odds: Optional[float] = None # e.g. 1.87
    asian_handicap_away_odds: Optional[float] = None # e.g. 1.93
    asian_handicap: Optional[str] = None             # formatted: "Arsenal -0.5 (1.87/1.93)"
    btts_yes_odds: Optional[float] = None
    btts_no_odds: Optional[float] = None
    # Overround-adjusted implied probabilities (0.0–1.0)
    implied_prob_w1: Optional[float] = None
    implied_prob_draw: Optional[float] = None
    implied_prob_w2: Optional[float] = None
    scrape_success: bool = True
    error_message: Optional[str] = None
```

**Step 2: Verify**

```bash
cd C:\Users\user\Desktop\SportsBot
python -c "from models import OddsData; o = OddsData(); print(o.asian_handicap_line, o.asian_handicap_home_odds)"
```

Expected output: `None None`

**Step 3: Commit**

```bash
git add models.py
git commit -m "feat: add asian_handicap_line/home_odds/away_odds fields to OddsData"
```

---

## Task 2: Asian Handicap Scraping + Parallel League Search + 13 More Leagues

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\scrapers\oddsportal.py`

**Step 1: Replace `_LEAGUE_PAGES` list (line 54–67)**

```python
_LEAGUE_PAGES = [
    # Tier 1 — top 5 European leagues
    "/soccer/england/premier-league/",
    "/soccer/spain/laliga/",
    "/soccer/germany/bundesliga/",
    "/soccer/italy/serie-a/",
    "/soccer/france/ligue-1/",
    # Tier 2 — major European leagues
    "/soccer/netherlands/eredivisie/",
    "/soccer/portugal/liga-portugal/",
    "/soccer/russia/premier-league/",
    "/soccer/ukraine/premier-league/",
    "/soccer/turkey/super-lig/",
    "/soccer/scotland/premiership/",
    "/soccer/belgium/jupiler-pro-league/",
    # Tier 2 — second divisions
    "/soccer/england/championship/",
    "/soccer/germany/2-bundesliga/",
    "/soccer/italy/serie-b/",
    "/soccer/france/ligue-2/",
    # European cups
    "/soccer/europe/champions-league/",
    "/soccer/europe/europa-league/",
    "/soccer/europe/conference-league/",
    # Americas
    "/soccer/usa/mls/",
    "/soccer/brazil/serie-a/",
    "/soccer/argentina/primera-division/",
    # Asia
    "/soccer/japan/j1-league/",
    "/soccer/south-korea/k-league-1/",
]
```

**Step 2: Replace `_find_match_url` method with parallel version**

Replace the entire `_find_match_url` method:

```python
async def _find_match_url(self) -> Optional[str]:
    """
    Search all league pages in parallel for JSON-LD SportsEvent entries.
    Returns the first match URL found.
    """
    t1_slug = self._slugify(self.team1)
    t2_slug = self._slugify(self.team2)
    t1_frag = t1_slug[:5]
    t2_frag = t2_slug[:5]

    async def _check_league(league_path: str) -> Optional[str]:
        try:
            html = await self._fetch_with_retry(
                BASE_URL + league_path,
                headers=_BE_HEADERS,
            )
            soup = BeautifulSoup(html, "lxml")
            for sc in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(sc.string or "")
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if item.get("@type") != "SportsEvent":
                            continue
                        event_name_slug = self._slugify(item.get("name", ""))
                        url = item.get("url", "")
                        if not url:
                            continue
                        if t1_frag in event_name_slug and t2_frag in event_name_slug:
                            logger.info("Found match on Betexplorer (%s): %s", league_path, url)
                            return url
                except (json.JSONDecodeError, AttributeError, KeyError):
                    continue
        except (ScraperError, RateLimitError) as exc:
            logger.debug("Betexplorer league page failed %s: %s", league_path, exc)
        return None

    # Run all league page checks in parallel
    results = await asyncio.gather(
        *[_check_league(p) for p in _LEAGUE_PAGES],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, str):
            return r
    return None
```

**Step 3: Add AH endpoint fetch and parser**

After the `_fetch_best_odds_html` method, add `_extract_best_ah_odds`:

```python
@staticmethod
def _extract_best_ah_odds(html: str) -> dict:
    """
    Parse Asian Handicap odds. Each table has data-handicap attribute.
    Pick the line with the most bookmaker rows (most liquid market).
    Returns {"line": float, "home": float, "away": float} or {}.
    """
    if not html:
        return {}
    soup = BeautifulSoup(html, "lxml")

    best: dict = {}
    best_row_count = 0

    for table in soup.select("table[data-handicap]"):
        try:
            line = float(table["data-handicap"])
        except (ValueError, KeyError):
            continue

        home_odds_list, away_odds_list = [], []
        for row in table.select("tr"):
            cells = row.select("[data-odd]")
            if len(cells) >= 2:
                try:
                    h = float(cells[0]["data-odd"])
                    a = float(cells[1]["data-odd"])
                    if 1.01 <= h <= 20 and 1.01 <= a <= 20:
                        home_odds_list.append(h)
                        away_odds_list.append(a)
                except (ValueError, KeyError):
                    pass

        if home_odds_list and len(home_odds_list) > best_row_count:
            best_row_count = len(home_odds_list)
            best = {
                "line": line,
                "home": round(max(home_odds_list), 2),
                "away": round(max(away_odds_list), 2),
            }

    return best
```

**Step 4: Update `scrape()` to fetch AH and populate fields**

In the `scrape()` method, replace the section that calls `asyncio.gather` (lines ~266–291) with:

```python
# Fetch 1X2, O/U 2.5, BTTS, and AH odds concurrently
html_1x2, html_ou, html_bts, html_ah = await asyncio.gather(
    self._fetch_best_odds_html(match_id, "1x2", referer),
    self._fetch_best_odds_html(match_id, "ou", referer),
    self._fetch_best_odds_html(match_id, "bts", referer),
    self._fetch_best_odds_html(match_id, "ah", referer),
    return_exceptions=True,
)

result = OddsData()

if not isinstance(html_1x2, Exception):
    odds_1x2 = self._extract_best_odds(html_1x2, "1x2")
    result.team1_win_odds = odds_1x2.get("home")
    result.draw_odds = odds_1x2.get("draw")
    result.team2_win_odds = odds_1x2.get("away")
    self._fill_implied_probs(result)

if not isinstance(html_ou, Exception):
    odds_ou = self._extract_best_odds(html_ou, "ou")
    result.over_2_5_odds = odds_ou.get("over")
    result.under_2_5_odds = odds_ou.get("under")

if not isinstance(html_bts, Exception):
    odds_bts = self._extract_best_odds(html_bts, "bts")
    result.btts_yes_odds = odds_bts.get("btts_yes")
    result.btts_no_odds = odds_bts.get("btts_no")

if not isinstance(html_ah, Exception):
    ah = self._extract_best_ah_odds(html_ah)
    if ah:
        result.asian_handicap_line = ah["line"]
        result.asian_handicap_home_odds = ah["home"]
        result.asian_handicap_away_odds = ah["away"]
        sign = "+" if ah["line"] > 0 else ""
        result.asian_handicap = (
            f"{self.team1} {sign}{ah['line']} "
            f"({ah['home']:.2f} / {ah['away']:.2f})"
        )

result.scrape_success = bool(result.team1_win_odds)
if not result.scrape_success:
    result.error_message = "Could not parse 1X2 odds from Betexplorer"
return result
```

**Step 5: Verify**

```bash
python -c "
import asyncio
from scrapers.oddsportal import OddsportalScraper
async def t():
    s = OddsportalScraper('Arsenal', 'Chelsea')
    r = await s.scrape()
    print('success:', r.scrape_success)
    print('1x2:', r.team1_win_odds, r.draw_odds, r.team2_win_odds)
    print('AH:', r.asian_handicap)
asyncio.run(t())
"
```

Expected: `success: True`, AH shows real line and odds (or None if no match scheduled).

**Step 6: Commit**

```bash
git add scrapers/oddsportal.py
git commit -m "feat: add AH scraping, parallel league search, expand to 25 leagues"
```

---

## Task 3: Brier O/U + BTTS + actual_goals + rate_limits + /history query

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\database.py`

**Step 1: Add new columns to `_create_tables` schema**

In `_create_tables`, replace the `predictions` table CREATE statement with:

```python
conn.executescript("""
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team1           TEXT    NOT NULL,
    team2           TEXT    NOT NULL,
    predicted_at    TEXT    NOT NULL,
    prob_w1         REAL,
    prob_draw       REAL,
    prob_w2         REAL,
    prob_over_2_5   REAL,
    prob_btts_yes   REAL,
    top_rec         TEXT,
    completeness    REAL,
    actual_result   TEXT,
    actual_score    TEXT,
    actual_goals_home INTEGER,
    actual_goals_away INTEGER,
    result_at       TEXT
);

CREATE TABLE IF NOT EXISTS team_memory (
    team      TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    saved_at  TEXT NOT NULL,
    PRIMARY KEY (team, key)
);

CREATE TABLE IF NOT EXISTS calibration (
    computed_at   TEXT PRIMARY KEY,
    n_total       INTEGER,
    n_with_result INTEGER,
    acc_1x2       REAL,
    brier_1x2     REAL,
    brier_ou      REAL,
    brier_btts    REAL
);

CREATE TABLE IF NOT EXISTS rate_limits (
    user_id         INTEGER PRIMARY KEY,
    last_request_at TEXT NOT NULL
);
""")

# Migrate existing DB: add new columns if missing (idempotent)
for col_sql in [
    "ALTER TABLE predictions ADD COLUMN actual_goals_home INTEGER",
    "ALTER TABLE predictions ADD COLUMN actual_goals_away INTEGER",
]:
    try:
        conn.execute(col_sql)
        conn.commit()
    except Exception:
        pass  # Column already exists
```

**Step 2: Update `_mark_result_sync` to store goals**

Replace the score parsing block inside `_mark_result_sync`:

```python
try:
    parts = score.replace(" ", "").split("-")
    home_g, away_g = int(parts[0]), int(parts[1])
    if home_g > away_g:
        actual_result = "W1"
    elif home_g < away_g:
        actual_result = "W2"
    else:
        actual_result = "DRAW"
except (ValueError, IndexError):
    logger.warning("Could not parse score '%s'", score)
    return False
```

And replace the final `conn.execute(UPDATE ...)` with:

```python
conn.execute(
    """
    UPDATE predictions
    SET actual_result=?, actual_score=?, actual_goals_home=?, actual_goals_away=?, result_at=?
    WHERE id=?
    """,
    (actual_result, score, home_g, away_g, _now_utc(), row["id"]),
)
conn.commit()
```

**Step 3: Replace the Brier O/U and BTTS stubs in `_recompute_calibration_sync`**

Replace the two `pass` stubs (the `if r["prob_over_2_5"] is not None:` and `if r["prob_btts_yes"] is not None:` blocks) with:

```python
# O/U 2.5 Brier
if r["prob_over_2_5"] is not None and r.get("actual_goals_home") is not None:
    total_goals = r["actual_goals_home"] + r["actual_goals_away"]
    pov = r["prob_over_2_5"] / 100
    pun = 1.0 - pov
    ov_actual = 1.0 if total_goals > 2.5 else 0.0
    probs_ou.append((pov, pun))
    outcomes_ou.append((ov_actual, 1.0 - ov_actual))

# BTTS Brier
if r["prob_btts_yes"] is not None and r.get("actual_goals_home") is not None:
    btts_actual = 1.0 if (r["actual_goals_home"] > 0 and r["actual_goals_away"] > 0) else 0.0
    pyes = r["prob_btts_yes"] / 100
    pno = 1.0 - pyes
    probs_btts.append((pyes, pno))
    outcomes_btts.append((btts_actual, 1.0 - btts_actual))
```

And update the stats dict to fill in real Brier O/U and BTTS:

```python
stats = {
    "computed_at": _now_utc(),
    "n_total": n_total,
    "n_with_result": n_with_result,
    "acc_1x2": acc_1x2,
    "brier_1x2": bs_1x2,
    "brier_ou": round(_brier_score(probs_ou, outcomes_ou), 4) if probs_ou else None,
    "brier_btts": round(_brier_score(probs_btts, outcomes_btts), 4) if probs_btts else None,
}
```

**Step 4: Add persistent rate limit functions (add at end of database.py)**

```python
# ── Rate limiting ───────────────────────────────────────────────────────────────

def _get_last_request_sync(user_id: int) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_request_at FROM rate_limits WHERE user_id=?", (user_id,)
        ).fetchone()
        return row["last_request_at"] if row else None


def _set_last_request_sync(user_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rate_limits (user_id, last_request_at)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_request_at=excluded.last_request_at
            """,
            (user_id, _now_utc()),
        )
        conn.commit()


async def get_last_request(user_id: int) -> Optional[str]:
    """Returns ISO timestamp of user's last request, or None."""
    return await asyncio.to_thread(_get_last_request_sync, user_id)


async def set_last_request(user_id: int) -> None:
    """Record that user_id just made a request (now)."""
    await asyncio.to_thread(_set_last_request_sync, user_id)


# ── Prediction history ─────────────────────────────────────────────────────────

def _get_prediction_history_sync(team1: str, team2: str, limit: int = 5) -> list[dict]:
    t1_l, t2_l = team1.strip().lower(), team2.strip().lower()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT team1, team2, predicted_at,
                   prob_w1, prob_draw, prob_w2,
                   top_rec, actual_result, actual_score, completeness
            FROM predictions
            WHERE (LOWER(team1)=? AND LOWER(team2)=?)
               OR (LOWER(team1)=? AND LOWER(team2)=?)
            ORDER BY predicted_at DESC LIMIT ?
            """,
            (t1_l, t2_l, t2_l, t1_l, limit),
        ).fetchall()
        return [dict(r) for r in rows]


async def get_prediction_history(team1: str, team2: str, limit: int = 5) -> list[dict]:
    """Return up to `limit` past predictions for this team pair."""
    return await asyncio.to_thread(_get_prediction_history_sync, team1, team2, limit)
```

**Step 5: Verify**

```bash
python -c "
import asyncio, database as db

async def t():
    await db.init_db()
    # Test rate limit
    await db.set_last_request(12345)
    ts = await db.get_last_request(12345)
    print('rate_limit ts:', ts)
    # Test history (empty)
    h = await db.get_prediction_history('Arsenal', 'Chelsea')
    print('history:', h)
asyncio.run(t())
"
```

Expected: `rate_limit ts: 2026-03-01T...`, `history: []`

**Step 6: Commit**

```bash
git add database.py
git commit -m "feat: actual_goals columns, Brier O/U+BTTS, persistent rate_limits, /history query"
```

---

## Task 4: Date-Aware Cache Key

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\cache.py`

**Step 1: Update `make_key` to include today's date**

Replace the `make_key` static method:

```python
@staticmethod
def make_key(team1: str, team2: str) -> str:
    from datetime import date
    pair = sorted([team1.strip().lower(), team2.strip().lower()])
    today = date.today().isoformat()   # "2026-03-01"
    return f"{pair[0]}|{pair[1]}|{today}"
```

**Step 2: Verify**

```bash
python -c "
from cache import TTLCache
k1 = TTLCache.make_key('Arsenal', 'Chelsea')
k2 = TTLCache.make_key('Chelsea', 'Arsenal')
print('keys equal:', k1 == k2)
print('has date:', '2026' in k1)
print(k1)
"
```

Expected: `keys equal: True`, `has date: True`, key ends with today's date.

**Step 3: Commit**

```bash
git add cache.py
git commit -m "feat: date-aware cache key — predictions expire at midnight"
```

---

## Task 5: Expanded Team Aliases

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\team_parser.py`
- Modify: `C:\Users\user\Desktop\SportsBot\scrapers\flashscore.py`

**Step 1: Add missing aliases to `TEAM_ALIASES` in team_parser.py**

Find the end of the `TEAM_ALIASES` dict (before `_SEP_RE`) and add:

```python
    # ── English — more clubs ─────────────────────────────────────────────────
    "brentford": "Brentford FC",
    "crystal palace": "Crystal Palace",
    "everton": "Everton FC",
    "fulham": "Fulham FC",
    "ipswich": "Ipswich Town",
    "bournemouth": "AFC Bournemouth",
    "southampton": "Southampton FC",
    "aston villa": "Aston Villa",
    "villa": "Aston Villa",
    "leeds": "Leeds United",
    "sunderland": "Sunderland AFC",
    "middlesbrough": "Middlesbrough FC",
    "boro": "Middlesbrough FC",
    "sheffield wednesday": "Sheffield Wednesday",
    "stoke": "Stoke City",
    "swansea": "Swansea City",
    "cardiff": "Cardiff City",
    "millwall": "Millwall FC",
    "qpr": "Queens Park Rangers",
    # ── Spanish ──────────────────────────────────────────────────────────────
    "atletico de madrid": "Atletico Madrid",
    "real sociedad": "Real Sociedad",
    "athletic bilbao": "Athletic Club",
    "athletic": "Athletic Club",
    "celta": "Celta Vigo",
    "celta vigo": "Celta Vigo",
    "mallorca": "RCD Mallorca",
    "girona": "Girona FC",
    "las palmas": "UD Las Palmas",
    # ── German ───────────────────────────────────────────────────────────────
    "rb leipzig": "RB Leipzig",
    "leipzig": "RB Leipzig",
    "heidenheim": "1. FC Heidenheim",
    "augsburg": "FC Augsburg",
    "wolfsburg": "VfL Wolfsburg",
    "bremen": "Werder Bremen",
    "werder": "Werder Bremen",
    "stuttgart": "VfB Stuttgart",
    "freiburg": "SC Freiburg",
    "köln": "1. FC Köln",
    "koln": "1. FC Köln",
    "darmstadt": "SV Darmstadt 98",
    "union berlin": "1. FC Union Berlin",
    "hertha": "Hertha BSC",
    "hamburger sv": "Hamburger SV",
    "hsv": "Hamburger SV",
    "hannover": "Hannover 96",
    "kaiserslautern": "1. FC Kaiserslautern",
    # ── Italian ──────────────────────────────────────────────────────────────
    "udinese": "Udinese Calcio",
    "genoa": "Genoa CFC",
    "sassuolo": "US Sassuolo",
    "parma": "Parma Calcio",
    "lecce": "US Lecce",
    "monza": "AC Monza",
    "cagliari": "Cagliari Calcio",
    "verona": "Hellas Verona",
    "venezia": "Venezia FC",
    "empoli": "Empoli FC",
    # ── French ───────────────────────────────────────────────────────────────
    "nice": "OGC Nice",
    "rennes": "Stade Rennais",
    "lens": "RC Lens",
    "strasbourg": "RC Strasbourg",
    "nantes": "FC Nantes",
    "toulouse": "Toulouse FC",
    "brest": "Stade Brestois",
    "reims": "Stade de Reims",
    "metz": "FC Metz",
    "havre": "Le Havre AC",
    # ── Portuguese ───────────────────────────────────────────────────────────
    "braga": "SC Braga",
    "vitoria": "Vitória SC",
    "gil vicente": "Gil Vicente FC",
    # ── Dutch ────────────────────────────────────────────────────────────────
    "az alkmaar": "AZ Alkmaar",
    "az": "AZ Alkmaar",
    "twente": "FC Twente",
    "utrecht": "FC Utrecht",
    "vitesse": "Vitesse Arnhem",
    "groningen": "FC Groningen",
    # ── Russian (more) ───────────────────────────────────────────────────────
    "краснодар": "FC Krasnodar",
    "рубин": "Rubin Kazan",
    "рубин казань": "Rubin Kazan",
    "крылья советов": "Krylya Sovetov",
    "крылья": "Krylya Sovetov",
    "ростов": "FC Rostov",
    "урал": "FC Ural Yekaterinburg",
    "химки": "FC Khimki",
    "нижний новгород": "FC Nizhny Novgorod",
    "факел": "FC Fakel Voronezh",
    "оренбург": "FC Orenburg",
    "балтика": "FC Baltika Kaliningrad",
    "ахмат": "FC Akhmat Grozny",
    "сочи": "FC Sochi",
    "торпедо": "FC Torpedo Moscow",
    # ── Champions League / Europa ─────────────────────────────────────────────
    "club brugge": "Club Brugge",
    "brugge": "Club Brugge",
    "anderlecht": "RSC Anderlecht",
    "slavia prague": "SK Slavia Prague",
    "slavia": "SK Slavia Prague",
    "sparta prague": "AC Sparta Prague",
    "sparta": "AC Sparta Prague",
    "dinamo zagreb": "GNK Dinamo Zagreb",
    "red bull salzburg": "FC Red Bull Salzburg",
    "salzburg": "FC Red Bull Salzburg",
    "young boys": "BSC Young Boys",
    "bsc young boys": "BSC Young Boys",
    "lazio": "SS Lazio",
    "udinese": "Udinese Calcio",
    # ── South American ───────────────────────────────────────────────────────
    "flamengo": "CR Flamengo",
    "palmeiras": "SE Palmeiras",
    "boca juniors": "CA Boca Juniors",
    "boca": "CA Boca Juniors",
    "river plate": "CA River Plate",
    "river": "CA River Plate",
}
```

**Step 2: Update `_SEARCH_ALIASES` in scrapers/flashscore.py**

Replace the current `_SEARCH_ALIASES` dict:

```python
_SEARCH_ALIASES: dict[str, list[str]] = {
    # Name used by team_parser → alternate names Fotmob might use
    "Bayern Munich": ["Bayern München", "FC Bayern München", "FC Bayern"],
    "Atletico Madrid": ["Atlético Madrid", "Atlético de Madrid"],
    "Borussia Mönchengladbach": ["Mönchengladbach", "Borussia M'gladbach"],
    "Bayer Leverkusen": ["Bayer 04 Leverkusen", "Bayer 04"],
    "RB Leipzig": ["RasenBallsport Leipzig", "Red Bull Leipzig"],
    "Eintracht Frankfurt": ["Eintracht Frankfurt", "SGE"],
    "Paris Saint-Germain": ["Paris Saint-Germain FC", "PSG", "Paris SG"],
    "Olympique Marseille": ["Marseille", "OM"],
    "Olympique Lyonnais": ["Lyon", "OL", "Olympique de Lyon"],
    "Borussia Dortmund": ["BV Borussia Dortmund", "BVB"],
    "Real Betis": ["Real Betis Balompié"],
    "Tottenham Hotspur": ["Tottenham", "Spurs"],
    "Brighton & Hove Albion": ["Brighton", "Brighton and Hove Albion"],
    "Wolverhampton Wanderers": ["Wolves", "Wolverhampton"],
    "Nottingham Forest": ["Nottm Forest", "Nott'm Forest"],
    "West Ham United": ["West Ham"],
    "Newcastle United": ["Newcastle"],
    "Sheffield United": ["Sheff Utd"],
    "AFC Bournemouth": ["Bournemouth"],
    "Sporting CP": ["Sporting Clube de Portugal", "Sporting Lisboa"],
    "SSC Napoli": ["Napoli"],
    "ACF Fiorentina": ["Fiorentina"],
    "AS Roma": ["Roma"],
    "SS Lazio": ["Lazio"],
    "Juventus": ["Juventus FC"],
    "Inter Milan": ["FC Internazionale", "Internazionale", "Inter"],
    "AC Milan": ["Milan"],
    "SK Slavia Prague": ["Slavia Praha", "Slavia Prague"],
    "AC Sparta Prague": ["Sparta Praha", "Sparta Prague"],
    "GNK Dinamo Zagreb": ["Dinamo Zagreb", "NK Dinamo"],
    "FC Red Bull Salzburg": ["Red Bull Salzburg", "Salzburg"],
    "AFC Ajax": ["Ajax Amsterdam", "Ajax"],
    "PSV Eindhoven": ["PSV"],
    "Fenerbahçe SK": ["Fenerbahce"],
    "Beşiktaş JK": ["Besiktas"],
    "Galatasaray SK": ["Galatasaray"],
    "Zenit St. Petersburg": ["Zenit", "FC Zenit"],
    "Spartak Moscow": ["Spartak", "FC Spartak Moscow"],
    "CSKA Moscow": ["CSKA", "PFK CSKA Moskva"],
}
```

**Step 3: Verify**

```bash
python -c "
from team_parser import normalize_team_name, parse_teams_from_input
print(normalize_team_name('ман сити'))          # Manchester City
print(normalize_team_name('Краснодар'))          # FC Krasnodar
print(normalize_team_name('leeds'))             # Leeds United
print(normalize_team_name('rb leipzig'))        # RB Leipzig
t1, t2 = parse_teams_from_input('Реал - Барса')
print(t1, 'vs', t2)                            # Real Madrid vs Barcelona
"
```

**Step 4: Commit**

```bash
git add team_parser.py scrapers/flashscore.py
git commit -m "feat: expand team aliases to 120+ entries, add Fotmob search aliases for 35 clubs"
```

---

## Task 6: Live Scraping Progress

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\scrapers\__init__.py`
- Modify: `C:\Users\user\Desktop\SportsBot\bot.py`

**Step 1: Update `scrape_all_parallel` to accept a progress callback**

Replace entire `scrapers/__init__.py`:

```python
"""
Parallel scraping orchestrator.

Runs all 4 scrapers concurrently via asyncio.gather().
Accepts optional on_progress callback: async fn(source_name: str, success: bool)
Called immediately after each scraper finishes so bot can update the loading message.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable, Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from models import MatchData
from scrapers.flashscore import FlashscoreScraper
from scrapers.sofascore import SofascoreScraper
from scrapers.transfermarkt import TransfermarktScraper
from scrapers.oddsportal import OddsportalScraper

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str, bool], Awaitable[None]]]


async def scrape_all_parallel(
    team1: str,
    team2: str,
    on_progress: ProgressCallback = None,
) -> MatchData:
    """
    Scrape all 4 sources in parallel.
    Calls on_progress(name, success) as each scraper finishes.
    Returns MatchData regardless of individual scraper failures.
    """
    semaphore = asyncio.Semaphore(config.SEMAPHORE_LIMIT)

    async def guarded(coro, name: str):
        async with semaphore:
            result = await coro
        success = not isinstance(result, Exception) and getattr(result, "scrape_success", False)
        if on_progress:
            try:
                await on_progress(name, success)
            except Exception as cb_exc:
                logger.debug("Progress callback error: %s", cb_exc)
        return result

    logger.info("Starting parallel scrape: %s vs %s", team1, team2)

    results = await asyncio.gather(
        guarded(FlashscoreScraper(team1, team2).scrape(), "Fotmob форма/H2H"),
        guarded(SofascoreScraper(team1, team2).scrape(), "Fotmob состав"),
        guarded(TransfermarktScraper(team1, team2).scrape(), "Transfermarkt"),
        guarded(OddsportalScraper(team1, team2).scrape(), "Betexplorer"),
        return_exceptions=True,
    )

    flashscore, sofascore, transfermarkt, odds = results

    names = ["FotmobForm", "FotmobSquad", "Transfermarkt", "Betexplorer"]
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            logger.error("%s returned unexpected exception: %s", name, result)

    return MatchData(
        team1=team1,
        team2=team2,
        flashscore=None if isinstance(flashscore, Exception) else flashscore,
        sofascore=None if isinstance(sofascore, Exception) else sofascore,
        transfermarkt=None if isinstance(transfermarkt, Exception) else transfermarkt,
        odds=None if isinstance(odds, Exception) else odds,
    )
```

**Step 2: Add progress state and live message update to bot.py**

In `bot.py`, inside `predict_handler`, replace the scrape section:

```python
    # ── Loading message ───────────────────────────────────────────────────────
    # Progress state: source name → True/False/None (None = still running)
    _SOURCES = ["Fotmob форма/H2H", "Fotmob состав", "Transfermarkt", "Betexplorer"]
    _progress: dict[str, Optional[bool]] = {s: None for s in _SOURCES}

    def _build_progress_text() -> str:
        lines = [f"⚽ <b>{team1} vs {team2}</b>\n", "📡 <b>Сбор данных...</b>\n"]
        done = sum(1 for v in _progress.values() if v is not None)
        for name, status in _progress.items():
            if status is None:
                icon = "⏳"
            elif status:
                icon = "✅"
            else:
                icon = "❌"
            lines.append(f"  {icon} {name}")
        lines.append(f"\n<i>Получено: {done}/{len(_SOURCES)} источников...</i>")
        return "\n".join(lines)

    loading_msg = await update.message.reply_html(_build_progress_text())

    async def edit(text: str) -> None:
        try:
            await loading_msg.edit_text(text, parse_mode=HTML)
        except TelegramError as exc:
            logger.warning("edit_text failed: %s", exc)

    async def on_progress(source_name: str, success: bool) -> None:
        _progress[source_name] = success
        await edit(_build_progress_text())

    try:
        # ── Scrape ────────────────────────────────────────────────────────────
        logger.info("Scraping: %s vs %s (user %d)", team1, team2, user_id)
        match_data = await scrape_all_parallel(team1, team2, on_progress=on_progress)
        # ... rest of try block unchanged
```

**Step 3: Verify (start bot, send /predict Arsenal vs Chelsea, watch message update)**

```bash
python bot.py
```

Observe the loading message in Telegram updating with ✅/❌ per source as they complete.

**Step 4: Commit**

```bash
git add scrapers/__init__.py bot.py
git commit -m "feat: live per-source scraping progress in Telegram message"
```

---

## Task 7: `/history` Command + Persistent Rate Limit in bot.py

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\bot.py`
- Modify: `C:\Users\user\Desktop\SportsBot\formatter.py`

**Step 1: Add `format_history` to formatter.py**

Add at end of `formatter.py`:

```python
def format_history(team1: str, team2: str, rows: list[dict]) -> str:
    """Format /history response — up to 5 past predictions for a match."""
    sep = "─" * 32
    t1u = team1.upper()
    t2u = team2.upper()
    lines = [
        f"📚 <b>ИСТОРИЯ ПРОГНОЗОВ</b>",
        f"<b>{t1u} vs {t2u}</b>",
        sep, "",
    ]

    if not rows:
        lines.append(f"<i>Прогнозов на этот матч пока нет.</i>")
        lines.append(f"Сделайте прогноз: <code>/predict {team1} vs {team2}</code>")
        return "\n".join(lines)

    for i, r in enumerate(rows, 1):
        date_str = r.get("predicted_at", "")[:10]
        p1 = r.get("prob_w1") or 0
        pd = r.get("prob_draw") or 0
        p2 = r.get("prob_w2") or 0
        top = r.get("top_rec") or "—"
        actual = r.get("actual_result")
        score = r.get("actual_score")

        # Determine if prediction was correct
        predicted_label = max(
            [("W1", p1), ("DRAW", pd), ("W2", p2)],
            key=lambda x: x[1]
        )[0]
        if actual:
            correct = "✅" if predicted_label == actual else "❌"
            result_str = f"{correct} {actual} ({score})"
        else:
            result_str = "❓ Результат не внесён"

        lines += [
            f"<b>{i}. {date_str}</b>",
            f"  Исход: {r.get('team1',team1)} <b>{p1:.0f}%</b> / Х <b>{pd:.0f}%</b> / {r.get('team2',team2)} <b>{p2:.0f}%</b>",
            f"  Топ-ставка: <b>{top}</b>",
            f"  Результат: {result_str}",
            "",
        ]

    lines.append(sep)
    lines.append(f"<i>Используйте /result чтобы внести результаты.</i>")
    return "\n".join(lines)
```

**Step 2: Add `/history` handler and persistent rate limit to bot.py**

At top of `bot.py`, update imports:

```python
from formatter import (
    format_report, format_loading, format_analysing,
    format_error, format_stats, format_history,
)
```

Add `history_handler` function:

```python
async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history Team1 vs Team2 — show past predictions for a match."""
    raw_args = " ".join(context.args) if context.args else ""
    if not raw_args:
        await update.message.reply_html(
            "❓ <b>Формат:</b> <code>/history Arsenal vs Chelsea</code>"
        )
        return

    team1, team2 = parse_teams_from_input(raw_args)
    if not team1 or not team2:
        await update.message.reply_html(
            f"❌ Не удалось распознать команды: <code>{raw_args}</code>"
        )
        return

    rows = await db.get_prediction_history(team1, team2, limit=5)
    await update.message.reply_html(format_history(team1, team2, rows))
```

Replace the in-memory rate limiting block in `predict_handler` with persistent version:

```python
    # ── Persistent rate limiting ───────────────────────────────────────────────
    from datetime import datetime, timezone, timedelta
    last_ts = await db.get_last_request(user_id)
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
            wait = config.USER_RATE_LIMIT_SECONDS - elapsed
            if wait > 0:
                await update.message.reply_text(
                    f"⏳ Пожалуйста, подождите ещё {int(wait)} секунд."
                )
                return
        except (ValueError, TypeError):
            pass
    await db.set_last_request(user_id)
```

Also remove the `_USER_LAST_REQUEST` dict from top of `bot.py` (it's no longer used).

Register handler in `main()`:

```python
    app.add_handler(CommandHandler("history", history_handler))
```

Update `/start` and `/help` messages to mention `/history`:

In `start_handler`, add line:
```python
        "  /history &lt;команда1&gt; vs &lt;команда2&gt;\n"
```

In `help_handler`, add section:
```python
        "<b>/history</b> — история прогнозов на матч\n"
        "  Пример: <code>/history Arsenal vs Chelsea</code>\n\n"
```

**Step 3: Commit**

```bash
git add bot.py formatter.py
git commit -m "feat: /history command, persistent rate limit, live progress integration"
```

---

## Task 8: Improved AI System Prompt

**Files:**
- Modify: `C:\Users\user\Desktop\SportsBot\analyzer.py`

**Step 1: Replace `_SYSTEM_PROMPT` in analyzer.py**

```python
_SYSTEM_PROMPT = """You are an elite football analyst with 20+ years experience in statistical modelling, tactical assessment, and betting market analysis across all major global leagues.

## Your analytical methodology

1. **Form analysis** — recent results, momentum, home/away performance splits, last 3 vs last 5 vs last 10 trend direction
2. **H2H patterns** — psychological advantage, historical scoring trends, venue-specific dominance
3. **Squad quality** — key absences impact on tactical setup and goal threat
4. **Market calibration** — where your probabilities diverge from bookmaker implied = value
5. **Expected goals model** — `base_xG = [(t1_scored + t2_conceded)/2] + [(t2_scored + t1_conceded)/2]`

## League-specific context

Apply these league baselines when adjusting confidence and totals:
- **Premier League**: home advantage is WEAK (+3-5% only); high BTTS rate (~55%); expect upsets
- **La Liga**: possession-based play reduces open chances; BTTS rate ~47%; draws more common
- **Serie A**: defensive compactness; avg 2.45 goals/game; low BTTS; clean sheets common
- **Bundesliga**: most open top league; avg 3.1 goals/game; high BTTS (~58%); home advantage strong
- **Ligue 1**: most unpredictable; avoid >65% confidence on any market
- **Champions League groups**: away teams often defensive in first leg; draws common
- **Champions League knockouts**: aggregate context changes risk appetite significantly
- **Lower leagues / cups**: data is scarce; reduce confidence, widen probability distributions

## Asian Handicap selection rules

Use this table based on your estimated win probability for team1 (home/favoured side):
- P(W1) < 30%: recommend team2 -1.0 or team2 -1.5
- P(W1) 30-40%: recommend team2 -0.5
- P(W1) 40-50%: recommend level ball (0) or team2 -0.5
- P(W1) 50-60%: recommend team1 -0.5 or level ball (0)
- P(W1) 60-70%: recommend team1 -0.5
- P(W1) > 70%: recommend team1 -1.0
- High draw probability (>28%): prefer +0.5 for the underdog over -0.5 for the favourite
- If actual AH odds data is provided: align recommendation with the most liquid market line

## Trap match detection — REDUCE confidence when you see these signals

- Strong favourite playing away after 3-day turnaround from a big European match
- Underdog in a "must-win" situation (relegation battle / cups)
- Public money overwhelming on favourite (implied odds < 1.50)
- Large squad value gap but underdog at home with strong recent form (≥ 3W in last 5)
- When trap conditions apply: pull P(W1) 5-10% closer to draw, increase risk_level

## Weighted xG formula

```
base_xG = [(t1_avg_scored + t2_avg_conceded)/2] + [(t2_avg_scored + t1_avg_conceded)/2]
home_bonus = +0.15 if team1 is home team
form_factor_t1 = 1.1 if last3=WWW, 0.9 if last3=LLL, else 1.0
form_factor_t2 = (same scale for t2)
absence_penalty = -0.15 for each key striker absent per team
adjusted_xG = (base_xG + home_bonus) × avg(form_factor_t1, form_factor_t2) + sum(absence_penalties)
```

Report `expected_goals` = adjusted_xG rounded to 1 decimal.

## Output rules

- Respond with ONLY a valid JSON object — no explanatory text, no markdown fences
- Use the EXACT schema below
- If a data section is marked [NOT AVAILABLE]: state it in data_warnings and reduce confidence

## JSON SCHEMA (all fields required)

{
  "prob_w1": <float 0-100, probability team1 wins>,
  "prob_draw": <float 0-100>,
  "prob_w2": <float 0-100>,
  "result_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "prob_over_2_5": <float 0-100>,
  "prob_under_2_5": <float 0-100>,
  "expected_goals": <float, adjusted xG for the match>,
  "goals_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "asian_handicap_rec": <string, e.g. "Arsenal -0.5" or "Level (0)">,
  "asian_handicap_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "prob_btts_yes": <float 0-100>,
  "prob_btts_no": <float 0-100>,
  "btts_confidence": <"HIGH"|"MEDIUM"|"LOW">,
  "team1_strengths": [<string>, ...],
  "team1_weaknesses": [<string>, ...],
  "team2_strengths": [<string>, ...],
  "team2_weaknesses": [<string>, ...],
  "top_recommendation": <string, your single best bet e.g. "Arsenal Win">,
  "top_recommendation_odds": <float|null, best available odds if known>,
  "top_confidence_pct": <float 0-100>,
  "risk_level": <"HIGH"|"MEDIUM"|"LOW">,
  "analysis_summary": <string, 2-3 sentences narrative>,
  "data_warnings": [<string>, ...]
}

## CALIBRATION RULES (CRITICAL — violations will break the system)

- prob_w1 + prob_draw + prob_w2 MUST sum to EXACTLY 100.0
- prob_over_2_5 + prob_under_2_5 MUST sum to EXACTLY 100.0
- prob_btts_yes + prob_btts_no MUST sum to EXACTLY 100.0
- If fewer than 2 data sources available: set ALL confidence fields to "MEDIUM" maximum
- risk_level "LOW" requires HIGH confidence AND strong clear favourite AND full data
- team1_strengths/weaknesses: 2-4 items each, concise (max 8 words per item)
- Never assign > 80% to any single 1X2 outcome without explicitly justifying in analysis_summary
- Never assign > 75% to over_2_5 or under_2_5 for a single match"""
```

Also update the user prompt template `_USER_PROMPT` to include AH data hint when available:

In `_build_data_context`, inside Section 4 (Bookmaker Odds), replace the AH line:

```python
if o.asian_handicap:
    lines.append(f"Asian Handicap: {o.asian_handicap}")
    if o.asian_handicap_line is not None:
        lines.append(
            f"  → Most liquid AH line: {'+' if o.asian_handicap_line > 0 else ''}"
            f"{o.asian_handicap_line} "
            f"(home {o.asian_handicap_home_odds:.2f} / away {o.asian_handicap_away_odds:.2f})"
        )
```

**Step 2: Verify**

```bash
python -c "
from analyzer import _SYSTEM_PROMPT
print('Prompt length:', len(_SYSTEM_PROMPT))
assert 'Trap match' in _SYSTEM_PROMPT
assert 'League-specific' in _SYSTEM_PROMPT
assert 'adjusted_xG' in _SYSTEM_PROMPT
assert 'Asian Handicap selection' in _SYSTEM_PROMPT
print('All checks passed')
"
```

**Step 3: Commit**

```bash
git add analyzer.py
git commit -m "feat: improved system prompt with league weights, AH rules, trap detection, xG formula"
```

---

## Task 9: Final Integration Test

**Step 1: Run full pipeline test**

```bash
cd C:\Users\user\Desktop\SportsBot
python -c "
import asyncio
from scrapers import scrape_all_parallel
from analyzer import analyze_match
from formatter import format_report, format_history
import database as db

async def test():
    await db.init_db()

    # Test progress callback
    progress_log = []
    async def on_prog(name, ok):
        progress_log.append((name, ok))
        print(f'  Progress: {name} -> {\"OK\" if ok else \"FAIL\"}')

    print('--- Scraping ---')
    data = await scrape_all_parallel('Arsenal', 'Chelsea', on_progress=on_prog)
    pct = int(data.data_completeness * 100)
    print(f'Completeness: {pct}%, failed: {data.failed_sources}')

    # Check AH field
    if data.odds and data.odds.scrape_success:
        print('AH:', data.odds.asian_handicap)
        print('AH line:', data.odds.asian_handicap_line)

    print('--- Analysis ---')
    report = await analyze_match(data)
    print('W1/X/W2:', report.prob_w1, report.prob_draw, report.prob_w2)
    print('Sum 1X2:', report.prob_w1 + report.prob_draw + report.prob_w2)
    print('AH rec:', report.asian_handicap_rec)
    print('Top:', report.top_recommendation, report.top_confidence_pct)

    # Save and test history
    pid = await db.save_prediction(report, data.data_completeness)
    print('Saved prediction id:', pid)

    history = await db.get_prediction_history('Arsenal', 'Chelsea')
    print('History rows:', len(history))
    print('format_history:', len(format_history('Arsenal', 'Chelsea', history)), 'chars')

    # Test rate limit persistence
    await db.set_last_request(99999)
    ts = await db.get_last_request(99999)
    print('Rate limit persisted:', ts[:10])

    # Cache key has date
    from cache import TTLCache
    key = TTLCache.make_key('Arsenal', 'Chelsea')
    print('Cache key:', key)
    assert '2026' in key, 'Cache key missing date!'

    print('--- ALL OK ---')

asyncio.run(test())
"
```

Expected: all steps print without errors, Sum 1X2 = 100.0, cache key contains today's date.

**Step 2: Full commit**

```bash
git add -A
git commit -m "chore: final integration verified — all 9 improvements working"
```

---

## Summary of Changes

| Task | Files | What Changed |
|------|-------|-------------|
| 1 | models.py | +3 AH fields to OddsData |
| 2 | scrapers/oddsportal.py | AH endpoint, parallel search, 25 leagues |
| 3 | database.py | actual_goals, Brier O/U+BTTS, rate_limits, /history query |
| 4 | cache.py | date in cache key |
| 5 | team_parser.py, scrapers/flashscore.py | 80+ new aliases |
| 6 | scrapers/__init__.py | on_progress callback |
| 7 | bot.py, formatter.py | /history handler, persistent rate limit, live progress wiring |
| 8 | analyzer.py | Improved system prompt |
| 9 | — | Integration test |
