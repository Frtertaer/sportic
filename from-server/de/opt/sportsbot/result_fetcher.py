"""
Automatic result fetcher via Fotmob API.

For each unresolved prediction (made > 3h ago), searches Fotmob fixture data
to find if the match has been played and what the score was.

Uses resolve_fotmob_team() from scrapers/fotmob.py — same helper as the scrapers.
Creates a temporary BaseScraper-compatible object to reuse the HTTP session logic.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from scrapers.base import BaseScraper
from scrapers.fotmob import resolve_fotmob_team
import database as db
from prediction_resolution import process_prediction_result

logger = logging.getLogger(__name__)


class _FotmobScraper(BaseScraper):
    """Minimal scraper subclass that gives result_fetcher access to _fetch_with_retry."""
    DOMAIN = "fotmob.com"

    async def scrape(self):
        pass  # not used directly


async def fetch_actual_result(
    team1: str,
    team2: str,
    predicted_at: str,
) -> Optional[tuple[str, str]]:
    """
    Try to find the actual result of a match via Fotmob fixture data.

    Returns (actual_result, score) e.g. ('W1', '2-1') or None if not found.
    actual_result is from team1's perspective (W1=team1 won, W2=team2 won).
    """
    scraper1 = _FotmobScraper(team1, team2)
    scraper2 = _FotmobScraper(team1, team2)
    try:
        results = await asyncio.gather(
            resolve_fotmob_team(scraper1, team1, log=logger),
            resolve_fotmob_team(scraper2, team2, log=logger),
            return_exceptions=True,
        )

        # If either resolution returned an exception, treat as failure
        if isinstance(results[0], BaseException) or isinstance(results[1], BaseException):
            logger.debug(
                "Team resolution raised for %s vs %s: t1=%s, t2=%s",
                team1, team2, results[0], results[1],
            )
            return None

        (resolved_t1, _), (resolved_t2, _) = results

        if not resolved_t1 or not resolved_t2:
            logger.debug(
                "Could not resolve teams for result fetch: %s vs %s", team1, team2
            )
            return None

        t1_id = resolved_t1.team_id
        t2_id = resolved_t2.team_id
        data1 = resolved_t1.data

        # Parse prediction timestamp to compare with fixture date
        try:
            pred_dt = datetime.fromisoformat(predicted_at)
            if pred_dt.tzinfo is None:
                pred_dt = pred_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pred_dt = datetime.now(timezone.utc)

        fixtures = (
            data1.get("fixtures", {})
            .get("allFixtures", {})
            .get("fixtures", [])
        )

        for fx in fixtures:
            try:
                status = fx.get("status", {})
                if not status.get("finished"):
                    continue

                # Check opponent is team2
                opp = fx.get("opponent", {})
                if opp.get("id") != t2_id:
                    continue

                # Check this match happened after we made the prediction
                utc_time_str = status.get("utcTime", "")
                if not utc_time_str:
                    continue
                try:
                    match_dt = datetime.fromisoformat(
                        utc_time_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    match_dt = datetime.fromisoformat(utc_time_str[:10]).replace(
                        tzinfo=timezone.utc
                    )

                if match_dt <= pred_dt:
                    continue  # match was before our prediction

                # Extract scores
                home = fx.get("home", {})
                away = fx.get("away", {})
                home_score = home.get("score")
                away_score = away.get("score")

                if home_score is None or away_score is None:
                    continue

                home_id = home.get("id")
                home_g = int(home_score)
                away_g = int(away_score)

                # Determine result from team1's perspective
                t1_is_home = (home_id == t1_id)
                if t1_is_home:
                    score_str = f"{home_g}-{away_g}"
                    if home_g > away_g:
                        result = "W1"
                    elif home_g < away_g:
                        result = "W2"
                    else:
                        result = "DRAW"
                else:
                    score_str = f"{away_g}-{home_g}"  # always shown as t1-t2
                    if away_g > home_g:
                        result = "W1"
                    elif away_g < home_g:
                        result = "W2"
                    else:
                        result = "DRAW"

                logger.info(
                    "Found result for %s vs %s: %s (%s)",
                    team1, team2, score_str, result,
                )
                return result, score_str

            except (KeyError, TypeError, ValueError):
                continue

        return None

    finally:
        await asyncio.gather(
            scraper1._close(), scraper2._close(), return_exceptions=True
        )


async def _fetch_espn_result(
    team1: str,
    team2: str,
    predicted_at: str,
) -> Optional[tuple[str, str]]:
    """
    Fallback result fetcher via ESPN Scoreboard API.
    Searches completed matches across major leagues on the prediction date.
    """
    import aiohttp
    from scrapers.fotmob import ESPN_SCOREBOARD_URL, ESPN_SCHEDULE_LEAGUES, _names_match

    try:
        pred_dt = datetime.fromisoformat(predicted_at)
        if pred_dt.tzinfo is None:
            pred_dt = pred_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    # Check the prediction date and the next day (match might span midnight)
    dates_to_check = [pred_dt.strftime("%Y%m%d")]
    from datetime import timedelta
    next_day = pred_dt + timedelta(days=1)
    dates_to_check.append(next_day.strftime("%Y%m%d"))

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for check_date in dates_to_check:
                for league_slug in ESPN_SCHEDULE_LEAGUES:
                    url = ESPN_SCOREBOARD_URL.format(league_slug=league_slug)
                    try:
                        async with session.get(
                            url,
                            params={"dates": check_date},
                            headers={"Accept": "application/json"},
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                    except Exception:
                        continue

                    for event in data.get("events", []):
                        competition = (event.get("competitions") or [{}])[0]
                        status = (competition.get("status") or {}).get("type") or {}
                        if not status.get("completed"):
                            continue

                        competitors = competition.get("competitors") or []
                        if len(competitors) < 2:
                            continue

                        home_comp = next(
                            (c for c in competitors if c.get("homeAway") == "home"), None
                        )
                        away_comp = next(
                            (c for c in competitors if c.get("homeAway") == "away"), None
                        )
                        if not home_comp or not away_comp:
                            continue

                        home_name = (home_comp.get("team") or {}).get("displayName", "")
                        away_name = (away_comp.get("team") or {}).get("displayName", "")

                        # Check if this match involves our teams
                        direct = (
                            _names_match([team1], [home_name])
                            and _names_match([team2], [away_name])
                        )
                        reverse = (
                            _names_match([team1], [away_name])
                            and _names_match([team2], [home_name])
                        )
                        if not direct and not reverse:
                            continue

                        home_score = int(home_comp.get("score", "0"))
                        away_score = int(away_comp.get("score", "0"))

                        if direct:
                            score_str = f"{home_score}-{away_score}"
                            if home_score > away_score:
                                result = "W1"
                            elif home_score < away_score:
                                result = "W2"
                            else:
                                result = "DRAW"
                        else:
                            score_str = f"{away_score}-{home_score}"
                            if away_score > home_score:
                                result = "W1"
                            elif away_score < home_score:
                                result = "W2"
                            else:
                                result = "DRAW"

                        logger.info(
                            "ESPN fallback found result for %s vs %s: %s (%s)",
                            team1, team2, score_str, result,
                        )
                        return result, score_str
    except Exception as exc:
        logger.debug("ESPN fallback failed: %s", exc)

    return None


async def check_pending_results() -> list[dict]:
    """
    Background task: check Fotmob for results of unresolved predictions.
    Returns a list of dicts for each newly resolved prediction (with user_id, score, etc.).
    """
    pending = await db.get_unresolved(older_than_hours=3.0)
    if not pending:
        logger.debug("No pending predictions to check for results")
        return []

    logger.info("Checking results for %d pending predictions", len(pending))
    resolved: list[dict] = []

    for pred in pending:
        try:
            result = await fetch_actual_result(
                pred["team1"],
                pred["team2"],
                pred["predicted_at"],
            )
            # Fallback to ESPN if Fotmob didn't find the result
            if result is None:
                result = await _fetch_espn_result(
                    pred["team1"],
                    pred["team2"],
                    pred["predicted_at"],
                )
            if result:
                actual_result, score = result
                resolved_pred = await process_prediction_result(pred["id"], score)
                if resolved_pred:
                    logger.info(
                        "Auto-stored result: %s vs %s → %s",
                        pred["team1"], pred["team2"], score,
                    )

                    # Update Elo ratings with actual result
                    try:
                        from elo import update_elo_after_result
                        parts = resolved_pred["actual_score"].split("-")
                        if len(parts) == 2:
                            hg, ag = int(parts[0].strip()), int(parts[1].strip())
                            await update_elo_after_result(
                                pred["team1"], pred["team2"], hg, ag,
                            )
                            logger.debug("Elo updated: %s %d-%d %s", pred["team1"], hg, ag, pred["team2"])
                    except Exception as elo_exc:
                        logger.debug("Elo update failed (non-critical): %s", elo_exc)

                    stats = await db.get_calibration_stats()
                    acc_line = ""
                    if stats and stats.get("n_with_result", 0) > 0:
                        acc_line = (
                            f"Точность: {stats['n_with_result']} матчей, "
                            f"{stats.get('acc_1x2', 0.0):.1f}% (1X2)"
                        )
                    resolved.append({
                        "prediction_id": pred["id"],
                        "user_id":       pred.get("user_id"),
                        "team1":         pred["team1"],
                        "team2":         pred["team2"],
                        "score":         resolved_pred["actual_score"],
                        "actual_result": resolved_pred["actual_result"],
                        "top_rec":       pred.get("top_rec"),
                        "acc_line":      acc_line,
                    })
        except Exception as exc:
            logger.warning(
                "Result fetch failed for %s vs %s: %s",
                pred["team1"], pred["team2"], exc,
            )

    return resolved
