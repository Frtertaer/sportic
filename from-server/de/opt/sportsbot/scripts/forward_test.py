#!/usr/bin/env python3
"""Forward-test harness: generate + persist predictions for upcoming fixtures.

Runs the FULL live pipeline (scrapers + grok live-search + AI chain) for each
fixture and saves every PredictionReport to the ``predictions`` table — the same
path the Telegram bot uses on /predict — so results can be filled in later
(``prediction_resolution`` / ``mark_result_by_prediction_id``) and ROI/CLV graded.
Each run also appends a human-readable line to ``data/forward_test_log.jsonl``.

Idempotent per fixture: skips a fixture that already has an unresolved prediction
unless ``--force``. Schedule it (systemd timer / cron) to log predictions on a
cadence; settle + score them once the matches finish.

Usage:
  python scripts/forward_test.py                 # default embedded fixtures
  python scripts/forward_test.py --fixtures f.txt # 'Team1,Team2[,date,competition]' per line
  python scripts/forward_test.py --force          # re-log even if already present
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config
import database as db
from analyzer import analyze_match
from scrapers import scrape_all_parallel

LOG_PATH = ROOT / "data" / "forward_test_log.jsonl"

# the-odds-api sport keys to scan for fixtures (WC active now; add leagues later).
ODDS_SPORT_KEYS = ("soccer_fifa_world_cup",)

# Sentinel user_id tagging predictions made by the forward test, so the resolver
# can score them in isolation from any other predictions in the table.
FT_USER_ID = 990001

# "Quality pick" gate: only log a bet that clears these. The EDGE gate is the key
# one — it requires the model's OWN probability for the recommended outcome to beat
# the market price (model_prob * odds - 1), i.e. a genuine +EV bet, not just backing
# a favourite. Tunable via env.
MIN_COMPLETENESS_PCT = float(os.getenv("FT_MIN_COMPLETENESS", "60"))
MIN_EDGE = float(os.getenv("FT_MIN_EDGE", "0.05"))  # +5% model-vs-market edge
MAX_EDGE = float(os.getenv("FT_MAX_EDGE", "0.22"))  # cap: a bigger "edge" vs a 24-book market
# is model OVER-confidence (esp. Over 2.5 on internationals), not real value -> reject as a bug.
# Week-1 forward-test -7.2% ROI was sunk by +31/34/83% "edge" overs that lost. (2026-07)


def _pct_to_prob(value) -> float | None:
    """Report probabilities are 0-100; normalise to 0-1."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v > 1.5:
        v /= 100.0
    return v if 0.0 < v <= 1.0 else None


_OVERUNDER_RE = re.compile(r"(over|under)\s+(\d+(?:\.\d+)?)")
_OVER_FIELDS = {1.5: "prob_over_1_5", 2.5: "prob_over_2_5", 3.5: "prob_over_3_5"}


def _is_unpriceable(rec_low: str) -> bool:
    """Reject any recommendation that cannot map to one exact feed market.

    In particular, a Double Chance/DNB label must never inherit a team's 1X2
    moneyline probability or price.  The bulk feed used here does not provide
    those derived markets.
    """
    if any(marker in rec_low for marker in ("&", " and ", "/", " parlay", " combo")):
        return True
    if "btts" in rec_low or "both teams" in rec_low:
        return True
    if any(
        marker in rec_low
        for marker in ("double chance", "draw no bet", "dnb", "no draw")
    ):
        return True
    return False


def _ah_line(rec_low: str) -> float | None:
    """The Asian-handicap line if this is an AH bet, else None."""
    if "handicap" not in rec_low and "asian" not in rec_low and not re.search(r"\bah\b", rec_low):
        return None
    m = re.search(r"([-+]?\d+(?:\.\d+)?)", rec_low)
    return float(m.group(1)) if m else None


def model_prob_for_recommendation(report, t1: str, t2: str) -> float | None:
    """The model's own probability that the recommended bet wins (0-1), for the
    EXACT recommended line. Returns None for bets we cannot price cleanly."""
    rec = (report.top_recommendation or "").lower()
    if _is_unpriceable(rec):
        return None
    m = _OVERUNDER_RE.search(rec)
    if m:
        side, line = m.group(1), float(m.group(2))
        field = _OVER_FIELDS.get(line)
        if not field:
            return None
        p = _pct_to_prob(getattr(report, field, None))
        if p is None:
            return None
        return p if side == "over" else (1.0 - p)
    # Asian handicap: only -0.5 settles exactly like a straight win; others need spreads
    ah = _ah_line(rec)
    if ah is not None and ah != -0.5:
        return None
    if "draw" in rec and _norm_team(t1) not in _norm_team(rec) and _norm_team(t2) not in _norm_team(rec):
        return _pct_to_prob(getattr(report, "prob_draw", None))
    if _norm_team(t1) and _norm_team(t1) in _norm_team(rec):
        return _pct_to_prob(getattr(report, "prob_w1", None))
    if _norm_team(t2) and _norm_team(t2) in _norm_team(rec):
        return _pct_to_prob(getattr(report, "prob_w2", None))
    return None


def _norm_team(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _avg_price(events_outcomes: list[float]) -> float | None:
    vals = [v for v in events_outcomes if isinstance(v, (int, float)) and v > 1.0]
    return round(mean(vals), 3) if vals else None


def fetch_odds_index() -> dict[frozenset, dict]:
    """One the-odds-api call per sport → consensus h2h / totals-2.5 / btts per event."""
    index: dict[frozenset, dict] = {}
    key = config.ODDS_API_KEY if getattr(config, "ODDS_API_KEY", "") else ""
    if not key:
        return index
    for sport in ODDS_SPORT_KEYS:
        # Bulk /odds endpoint only serves featured markets (h2h, spreads, totals);
        # btts is not available here, so BTTS bets are graded on accuracy only.
        url = (
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
            f"?apiKey={key}&regions=eu&markets=h2h,totals&oddsFormat=decimal"
        )
        try:
            with urllib.request.urlopen(url, timeout=25) as resp:
                events = json.load(resp)
        except Exception:  # noqa: BLE001 — odds are optional enrichment
            continue
        for ev in events:
            home, away = ev.get("home_team"), ev.get("away_team")
            if not home or not away:
                continue
            h2h: dict[str, list[float]] = {}
            totals_pts: dict[float, dict[str, list[float]]] = {}
            books = ev.get("bookmakers") or []
            for bm in books:
                for mkt in bm.get("markets", []):
                    mk = mkt.get("key")
                    for o in mkt.get("outcomes", []):
                        nm, price, point = o.get("name"), o.get("price"), o.get("point")
                        if mk == "h2h" and nm:
                            h2h.setdefault(nm, []).append(price)
                        elif mk == "totals" and point is not None and nm in ("Over", "Under"):
                            totals_pts.setdefault(float(point), {"Over": [], "Under": []})[nm].append(price)
            totals = {
                pt: {"Over": _avg_price(d["Over"]), "Under": _avg_price(d["Under"])}
                for pt, d in totals_pts.items()
            }
            snapshot = {
                "home_team": home, "away_team": away,
                "commence_time": ev.get("commence_time"),
                "n_books": len(books),
                "h2h": {k: _avg_price(v) for k, v in h2h.items()},
                "totals": totals,  # {line: {Over, Under}} — line-accurate, no 2.5 assumption
                "over_2_5": (totals.get(2.5) or {}).get("Over"),
                "under_2_5": (totals.get(2.5) or {}).get("Under"),
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            index[frozenset((_norm_team(home), _norm_team(away)))] = snapshot
    return index


def match_odds(t1: str, t2: str, index: dict[frozenset, dict]) -> dict | None:
    key = frozenset((_norm_team(t1), _norm_team(t2)))
    if key in index:
        return index[key]
    n1, n2 = _norm_team(t1), _norm_team(t2)
    for k, snap in index.items():
        ks = list(k)
        if len(ks) == 2 and {n1, n2} <= {ks[0], ks[1]}:
            return snap
        # substring tolerance (e.g. "Iran" vs "IR Iran")
        if any(n1 in x or x in n1 for x in ks) and any(n2 in x or x in n2 for x in ks):
            return snap
    return None


def odds_for_recommendation(rec: str, t1: str, t2: str, snap: dict) -> tuple[float | None, str | None]:
    """Map a recommendation to the consensus price that settles it — LINE-ACCURATE.

    Returns (None, None) for anything we cannot price from the bulk feed: combos/
    parlays, BTTS, Asian-handicap lines other than -0.5, or a totals line the books
    don't actually offer for this match (e.g. Over 1.5 where only 2.5 exists).
    """
    if not rec or not snap:
        return None, None
    low = rec.lower()
    if _is_unpriceable(low):
        return None, None
    m = _OVERUNDER_RE.search(low)
    if m:
        side, line = m.group(1), float(m.group(2))
        entry = (snap.get("totals") or {}).get(line)
        price = entry.get("Over" if side == "over" else "Under") if entry else None
        return (price, f"{side.title()} {line}") if price else (None, None)
    ah = _ah_line(low)
    if ah is not None and ah != -0.5:
        return None, None
    h2h = snap.get("h2h") or {}
    for team in (t1, t2):
        if _norm_team(team) and _norm_team(team) in _norm_team(rec):
            for nm, price in h2h.items():
                if _norm_team(nm) == _norm_team(team) or _norm_team(team) in _norm_team(nm):
                    return price, f"{team} ML"
    if "draw" in low:
        return h2h.get("Draw"), "Draw"
    return None, None

# (team1, team2, kickoff_date, competition) — real WC-2026 fixtures, 20-23 Jun 2026.
DEFAULT_FIXTURES: list[tuple[str, str, str, str]] = [
    ("Netherlands", "Sweden", "2026-06-20", "World Cup 2026"),
    ("Germany", "Ivory Coast", "2026-06-20", "World Cup 2026"),
    ("Spain", "Saudi Arabia", "2026-06-21", "World Cup 2026"),
    ("Belgium", "Iran", "2026-06-21", "World Cup 2026"),
    ("Argentina", "Austria", "2026-06-22", "World Cup 2026"),
    ("Portugal", "Uzbekistan", "2026-06-23", "World Cup 2026"),
]


async def run_one(
    t1: str, t2: str, date: str, comp: str, force: bool, odds_index: dict, quality_gate: bool,
) -> dict:
    if not force:
        existing = await db.find_latest_unresolved_prediction(t1, t2)
        if existing:
            return {
                "team1": t1, "team2": t2,
                "skipped": "already logged",
                "prediction_id": existing.get("id"),
            }

    data = await scrape_all_parallel(t1, t2)
    completeness = float(data.applicable_data_completeness or 0.0)
    report = await analyze_match(data)

    # Capture real bookmaker odds at bet time (the-odds-api) so ROI/CLV is gradeable
    # later — our Betexplorer scraper does not cover these fixtures.
    snap = match_odds(t1, t2, odds_index)
    bet_odds, bet_market = odds_for_recommendation(report.top_recommendation, t1, t2, snap or {})
    model_prob = model_prob_for_recommendation(report, t1, t2)
    edge = round(model_prob * bet_odds - 1.0, 4) if (model_prob and bet_odds) else None

    base = {
        "team1": t1, "team2": t2, "recommendation": report.top_recommendation,
        "completeness_pct": round(completeness * 100), "bet_odds": bet_odds,
        "model_prob": round(model_prob, 3) if model_prob else None, "edge": edge,
    }

    # ── Quality gate: only a genuine +EV pick with enough data gets logged ──
    if quality_gate:
        if bet_odds is None:
            return {**base, "skipped": "no odds for recommendation"}
        if completeness * 100 < MIN_COMPLETENESS_PCT:
            return {**base, "skipped": f"completeness<{MIN_COMPLETENESS_PCT:.0f}%"}
        if edge is None or edge < MIN_EDGE:
            return {**base, "skipped": f"edge<{MIN_EDGE:+.0%}"}
        if edge > MAX_EDGE:
            return {**base, "skipped": f"edge>{MAX_EDGE:+.0%} (fake — model over-confidence, not value)"}

    if bet_odds and not report.top_recommendation_odds:
        report.top_recommendation_odds = bet_odds
    pred_id = await db.save_prediction(report, completeness, user_id=FT_USER_ID)

    rec = {
        "prediction_id": pred_id,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "kickoff_date": date,
        "competition": comp,
        "confidence_pct": round(float(report.top_confidence_pct)),
        "risk": report.risk_level,
        "stake_units": report.stake_units,
        "bet_market": bet_market,
        "odds_snapshot": snap,
        **base,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _load_fixtures(path: str) -> list[tuple[str, str, str, str]]:
    fixtures: list[tuple[str, str, str, str]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "," not in line:
            continue
        parts = [p.strip() for p in line.split(",")]
        t1, t2 = parts[0], parts[1]
        date = parts[2] if len(parts) > 2 else ""
        comp = parts[3] if len(parts) > 3 else ""
        fixtures.append((t1, t2, date, comp))
    return fixtures


def fixtures_from_odds_index(odds_index: dict, days: int) -> list[tuple[str, str, str, str]]:
    """Auto-discover upcoming fixtures (not yet started, within `days`) from the odds feed."""
    now = datetime.now(timezone.utc)
    out: list[tuple[str, str, str, str, datetime]] = []
    for snap in odds_index.values():
        ct = snap.get("commence_time")
        if not ct:
            continue
        try:
            ko = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except ValueError:
            continue
        if now <= ko <= now + timedelta(days=days):
            out.append((snap["home_team"], snap["away_team"], ko.date().isoformat(), "World Cup 2026", ko))
    out.sort(key=lambda x: x[4])
    return [(a, b, c, d) for a, b, c, d, _ in out]


async def main() -> None:
    ap = argparse.ArgumentParser(description="Forward-test prediction logger.")
    ap.add_argument("--force", action="store_true", help="re-log even if a prediction already exists")
    ap.add_argument("--fixtures", help="file with 'Team1,Team2[,date,competition]' per line")
    ap.add_argument("--auto", action="store_true", help="auto-discover upcoming fixtures from the odds feed")
    ap.add_argument("--days", type=int, default=3, help="look-ahead window for --auto (default 3)")
    ap.add_argument("--all", action="store_true", help="disable the quality gate (log every fixture)")
    args = ap.parse_args()

    await db.init_db()
    odds_index = await asyncio.to_thread(fetch_odds_index)

    if args.auto:
        fixtures = fixtures_from_odds_index(odds_index, args.days)
    elif args.fixtures:
        fixtures = _load_fixtures(args.fixtures)
    else:
        fixtures = DEFAULT_FIXTURES

    quality_gate = not args.all
    mode = "auto" if args.auto else "manual"
    gate = f"quality (compl≥{MIN_COMPLETENESS_PCT:.0f}%, edge≥{MIN_EDGE:+.0%})" if quality_gate else "log-all"
    print(f"Forward test [{mode}] — {len(fixtures)} fixtures, {len(odds_index)} odds events, gate={gate}")
    print(f"log -> {LOG_PATH}")
    print("-" * 70)
    logged = skipped = errored = 0
    for t1, t2, date, comp in fixtures:
        try:
            rec = await run_one(t1, t2, date, comp, args.force, odds_index, quality_gate)
        except Exception as exc:  # noqa: BLE001 — keep going across fixtures
            rec = {"team1": t1, "team2": t2, "error": f"{type(exc).__name__}: {exc}"}
        if rec.get("error"):
            errored += 1
        elif rec.get("skipped"):
            skipped += 1
        elif rec.get("prediction_id"):
            logged += 1
        print(json.dumps(rec, ensure_ascii=False))
    print("-" * 70)
    print(f"Logged {logged} | skipped {skipped} | errored {errored}")


if __name__ == "__main__":
    asyncio.run(main())
