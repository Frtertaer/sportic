"""
Shared Fotmob helpers used by the former Flashscore/Sofascore scrapers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field as _dc_field
from typing import Optional

import config
from scrapers.base import RateLimitError, ScraperError

logger = logging.getLogger(__name__)
_FOTMOB_TEAM_DATA_CACHE: dict[int, tuple[float, dict]] = {}
_RESOLVED_TEAM_CACHE: dict[str, tuple[float, "ResolvedFotmobTeam"]] = {}
_RESOLVE_INFLIGHT: dict[tuple[int, str], asyncio.Future[Optional["ResolvedFotmobTeam"]]] = {}

FOTMOB_SEARCH = "https://apigw.fotmob.com/searchapi/suggest"
FOTMOB_TEAM_HTML = "https://www.fotmob.com/teams/{team_id}"

FOTMOB_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://www.fotmob.com/",
}
FOTMOB_HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://www.fotmob.com/",
}

_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)

_DEFAULT_SEARCH_ALIASES: dict[str, list[str]] = {
    "Bayern Munich": ["Bayern M\u00fcnchen"],
}
_TEAM_ID_RE = re.compile(r"team_(\d+)")
_IGNORE_TOKENS = {
    # English / generic
    "fc", "cf", "sc", "afc", "ac", "as", "ss", "us", "ssc",
    # Full words often in formal names (e.g. "Pisa Sporting Club")
    "club", "sporting", "calcio", "football", "futbol",
    # Spanish / Portuguese
    "cd", "rcd", "ud", "sd", "ad", "rc", "ca",
    # Eastern European / Scandinavian
    "sk", "sl", "jk", "fk", "nk", "pk", "bk", "if",
    # French
    "ogc", "losc", "asm", "aj", "ea", "sm",
    # German
    "vfb", "vfl", "tsg", "rb", "sv", "bsc", "fsv",
    # Dutch / Belgian
    "az", "nac", "pec",
    # Italian
    "acf", "uc",
    # Scottish / Irish
    "st",
    # Middle East / Africa / Asia
    "al",
}
_TOKEN_EQUIVALENTS = {
    "munich": "munchen",
    "muenchen": "munchen",
    "utd": "united",
}


@dataclass(slots=True)
class ResolvedFotmobTeam:
    requested_name: str
    search_term: str
    team_id: int
    data: dict


def clear_fotmob_caches() -> None:
    _FOTMOB_TEAM_DATA_CACHE.clear()
    _RESOLVED_TEAM_CACHE.clear()
    _RESOLVE_INFLIGHT.clear()


def _consume_future_exception(future: asyncio.Future[Optional["ResolvedFotmobTeam"]]) -> None:
    if future.cancelled():
        return
    try:
        future.exception()
    except Exception:
        pass


def _cache_key(team_name: str) -> str:
    normalized = _normalize_name(team_name)
    return normalized or team_name.strip().lower()


def _get_cached_team_data(team_id: int) -> Optional[dict]:
    entry = _FOTMOB_TEAM_DATA_CACHE.get(team_id)
    if not entry:
        return None
    expires_at, data = entry
    if time.monotonic() >= expires_at:
        _FOTMOB_TEAM_DATA_CACHE.pop(team_id, None)
        return None
    return data


def _set_cached_team_data(team_id: int, data: dict) -> None:
    _FOTMOB_TEAM_DATA_CACHE[team_id] = (
        time.monotonic() + config.FOTMOB_TEAM_CACHE_TTL_SECONDS,
        data,
    )


def _get_cached_resolved_team(team_name: str) -> Optional[ResolvedFotmobTeam]:
    entry = _RESOLVED_TEAM_CACHE.get(_cache_key(team_name))
    if not entry:
        return None
    expires_at, resolved = entry
    if time.monotonic() >= expires_at:
        _RESOLVED_TEAM_CACHE.pop(_cache_key(team_name), None)
        return None
    return resolved


def _set_cached_resolved_team(team_name: str, resolved: ResolvedFotmobTeam) -> None:
    _RESOLVED_TEAM_CACHE[_cache_key(team_name)] = (
        time.monotonic() + config.FOTMOB_TEAM_CACHE_TTL_SECONDS,
        resolved,
    )


def build_fotmob_search_url(
    term: str,
    *,
    size: int = 30,
    lang: str = "en",
    ver: str = "1",
) -> str:
    encoded_term = urllib.parse.quote(term, safe="")
    return f"{FOTMOB_SEARCH}?term={encoded_term}&lang={lang}&size={size}&ver={ver}"


def build_fotmob_search_candidates(
    team_name: str,
    *,
    search_aliases: Optional[list[str]] = None,
) -> list[str]:
    derived_aliases = _build_derived_search_aliases(team_name)
    parser_aliases = _build_team_parser_search_aliases(team_name)
    candidates: list[str] = []
    for candidate in (
        (search_aliases or [])
        + derived_aliases
        + parser_aliases
        + _DEFAULT_SEARCH_ALIASES.get(team_name, [])
        + [team_name]
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _build_derived_search_aliases(team_name: str) -> list[str]:
    """
    Strip club prefixes to generate search-friendly aliases.

    Uses two strategies:
    1. Known tokens list (_IGNORE_TOKENS) - "FC", "SC", "OGC", etc.
    2. Smart heuristic: any leading word that is ≤4 chars and purely
       alphabetic is likely a club prefix (covers FK, NK, IF, BK, AZ,
       and any future prefix we haven't seen yet).

    Example: "OGC Nice" → ["Nice"]
             "FK Sarajevo" → ["Sarajevo"]  (even without FK in _IGNORE_TOKENS)
             "Manchester City" → []  (first word is 10 chars — not a prefix)
    """
    parts = team_name.split()
    aliases: list[str] = []

    # Strategy 1: strip known prefix tokens (greedy, can strip multiple)
    # Only strip leading ordinals like "1." (e.g. "1. FC Nürnberg"), not years.
    stripped = list(parts)
    while stripped:
        normalized = _normalize_token(stripped[0])
        if normalized in _IGNORE_TOKENS:
            stripped = stripped[1:]
            continue
        # Strip leading ordinal (e.g. "1.") but NOT standalone years (4 digits)
        if re.fullmatch(r"\d{1,2}\.?", stripped[0]):
            stripped = stripped[1:]
            continue
        break
    known_alias = " ".join(stripped).strip()
    if known_alias and known_alias != team_name:
        aliases.append(known_alias)

    # Strategy 2: if first word is short (≤4 chars), alphabetic, and NOT
    # a legitimate part of a team name — strip it as a likely club prefix.
    _NOT_PREFIX = {
        "real", "red", "star", "new", "old", "san", "los", "las",
        "al", "el", "le", "la", "de", "du", "go", "cd",
        "boca", "bay", "east", "west", "city", "town", "port",
        "club", "rayo", "york", "bath", "hull", "ross", "cork",
        "bury", "dale", "vale", "como", "nice", "lyon", "lens",
        "metz", "roma", "lask", "gent", "genk", "ajax", "psv",
        "fano", "bari", "pisa", "spal",
    }
    if len(parts) >= 2:
        first = parts[0]
        remaining = " ".join(parts[1:]).strip()
        # Don't strip if the remaining part is purely numeric (year) —
        # e.g. "Como 1907" → "Como" is the name, "1907" is just a year suffix
        remaining_is_year = bool(re.fullmatch(r"\d{4}", remaining))
        if (
            len(first) <= 4
            and first.isalpha()
            and first.lower() not in _NOT_PREFIX
            and not remaining_is_year
        ):
            if remaining and remaining != team_name and remaining not in aliases:
                aliases.append(remaining)

    # Strategy 3: strip trailing year suffix (e.g. "Como 1907" → "Como")
    if len(parts) >= 2 and re.fullmatch(r"\d{4}", parts[-1]):
        name_without_year = " ".join(parts[:-1]).strip()
        if name_without_year and name_without_year not in aliases:
            aliases.append(name_without_year)

    return aliases


def _build_team_parser_search_aliases(team_name: str) -> list[str]:
    from team_parser import TEAM_ALIASES

    team_name_normalized = _normalize_name(team_name)
    if not team_name_normalized:
        return []

    team_name_token_count = len(team_name.split())
    aliases: list[str] = []
    for alias, canonical in TEAM_ALIASES.items():
        if canonical != team_name:
            continue
        if not alias.isascii():
            continue
        normalized_alias = _normalize_name(alias)
        if not normalized_alias or normalized_alias != team_name_normalized:
            continue
        if alias.lower() == team_name.lower():
            continue
        if len(alias.split()) > team_name_token_count:
            continue
        aliases.append(_format_search_alias(alias))

    return sorted(set(aliases), key=lambda value: (len(value.split()), len(value)))


def _format_search_alias(alias: str) -> str:
    return " ".join(part.title() for part in alias.split())


def _normalize_token(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value)
    ascii_value = ascii_value.encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[a-z0-9]+", ascii_value.lower().replace("&", " and "))
    if not tokens:
        return ""
    return _TOKEN_EQUIVALENTS.get(tokens[0], tokens[0])


def _normalize_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value)
    ascii_value = ascii_value.encode("ascii", "ignore").decode("ascii")
    tokens = []
    for token in re.findall(r"[a-z0-9]+", ascii_value.lower().replace("&", " and ")):
        token = _TOKEN_EQUIVALENTS.get(token, token)
        if token not in _IGNORE_TOKENS:
            tokens.append(token)
    return " ".join(tokens)


def _resolve_alias(name: str) -> str | None:
    """Look up team alias → canonical name via TEAM_ALIASES."""
    from team_parser import TEAM_ALIASES
    return TEAM_ALIASES.get(name.lower().strip())


def _normalize_name_keep_all(value: str) -> str:
    """Like _normalize_name but keeps ALL tokens (no _IGNORE_TOKENS stripping).

    Used for matching when the caller already stripped tokens on one side
    but needs to compare against the raw name on the other side.
    """
    ascii_value = unicodedata.normalize("NFKD", value)
    ascii_value = ascii_value.encode("ascii", "ignore").decode("ascii")
    tokens = []
    for token in re.findall(r"[a-z0-9]+", ascii_value.lower().replace("&", " and ")):
        token = _TOKEN_EQUIVALENTS.get(token, token)
        tokens.append(token)
    return " ".join(tokens)


def _names_match(expected_names: list[str], actual_names: list[str]) -> bool:
    # Expand both sides with alias resolution
    expanded_expected: set[str] = set()
    expanded_expected_raw: set[str] = set()  # with all tokens, no stripping
    for name in expected_names:
        if name:
            expanded_expected.add(_normalize_name(name))
            expanded_expected_raw.add(_normalize_name_keep_all(name))
            canon = _resolve_alias(name)
            if canon:
                expanded_expected.add(_normalize_name(canon))
                expanded_expected_raw.add(_normalize_name_keep_all(canon))

    expanded_actual: set[str] = set()
    expanded_actual_raw: set[str] = set()
    for name in actual_names:
        if name:
            expanded_actual.add(_normalize_name(name))
            expanded_actual_raw.add(_normalize_name_keep_all(name))
            canon = _resolve_alias(name)
            if canon:
                expanded_actual.add(_normalize_name(canon))
                expanded_actual_raw.add(_normalize_name_keep_all(canon))

    expanded_expected.discard("")
    expanded_actual.discard("")
    expanded_expected_raw.discard("")
    expanded_actual_raw.discard("")

    # Exact match after normalization (ignoring prefix tokens)
    if expanded_expected & expanded_actual:
        return True

    # Exact match on RAW (all tokens kept) — handles "AJ Auxerre" == "AJ Auxerre"
    if expanded_expected_raw & expanded_actual_raw:
        return True

    for expected in expanded_expected:
        expected_tokens = set(expected.split())
        if not expected_tokens:
            continue
        for actual in expanded_actual:
            actual_tokens = set(actual.split())
            if not actual_tokens:
                continue
            # Subset check: when SMALLER set has ≥2 tokens
            smaller = min(len(expected_tokens), len(actual_tokens))
            if smaller >= 2 and (expected_tokens <= actual_tokens or actual_tokens <= expected_tokens):
                return True

    # --- Fallback for single-token matches (e.g. "auxerre" vs "aj auxerre") ---
    # Allow match only when the "extra" tokens on the other side are ALL
    # in _IGNORE_TOKENS (i.e., they're club prefixes like AJ, OGC, FK, etc.)
    # This prevents "Milan" matching "Inter Milan" (inter is NOT a prefix).
    for expected in expanded_expected:
        e_tokens = expected.split()
        if len(e_tokens) != 1:
            continue
        core = e_tokens[0]
        for actual_raw in expanded_actual_raw:
            a_raw_tokens = actual_raw.split()
            if core in a_raw_tokens:
                extra = {t for t in a_raw_tokens if t != core}
                if extra and extra <= _IGNORE_TOKENS:
                    return True

    for actual in expanded_actual:
        a_tokens = actual.split()
        if len(a_tokens) != 1:
            continue
        core = a_tokens[0]
        for expected_raw in expanded_expected_raw:
            e_raw_tokens = expected_raw.split()
            if core in e_raw_tokens:
                extra = {t for t in e_raw_tokens if t != core}
                if extra and extra <= _IGNORE_TOKENS:
                    return True

    return False


def _extract_ranked_team_ids(search_data: dict) -> list[tuple[int, int]]:
    """Extract team IDs from Fotmob /searchapi/suggest response.

    The suggest API returns teamSuggest[].options[] with format:
      {"text": "Borussia Dortmund|9789", "score": 300788.0, "payload": {"id": "9789", ...}}
    """
    results: list[tuple[int, float]] = []

    # New format: /searchapi/suggest → teamSuggest
    for suggest_group in search_data.get("teamSuggest", []):
        for option in suggest_group.get("options", []):
            payload = option.get("payload", {})
            team_id = payload.get("id")
            score = option.get("score", 0)
            if team_id:
                try:
                    results.append((int(team_id), score))
                except (ValueError, TypeError):
                    pass

    # Fallback: old format (/searchapi/search → hits.hits with links)
    if not results:
        counter: Counter[str] = Counter()
        hits = search_data.get("hits", {}).get("hits", [])
        for hit in hits:
            for link in hit.get("links", []):
                for team_id in _TEAM_ID_RE.findall(link.get("href", "")):
                    counter[team_id] += 1
        return [(int(team_id), count) for team_id, count in counter.most_common()]

    # Sort by score (highest first) and return as (id, rank)
    results.sort(key=lambda x: x[1], reverse=True)
    return [(tid, rank + 1) for rank, (tid, _) in enumerate(results)]


async def fetch_fotmob_team_data(scraper, team_id: int) -> dict:
    """Fetch team data from Fotmob via __NEXT_DATA__ in HTML page.

    The old /api/teams endpoint now returns 404; all data is embedded
    in the server-rendered HTML as a Next.js JSON payload.
    """
    cached = _get_cached_team_data(team_id)
    if cached is not None:
        return cached
    url = FOTMOB_TEAM_HTML.format(team_id=team_id)
    raw_html = await scraper._fetch_with_retry(
        url,
        headers=FOTMOB_HTML_HEADERS,
    )
    match = _NEXT_DATA_RE.search(raw_html)
    if not match:
        raise ScraperError(f"No __NEXT_DATA__ found on Fotmob team page {team_id}")
    next_data = json.loads(match.group(1))
    fallback = next_data.get("props", {}).get("pageProps", {}).get("fallback", {})
    team_key = f"team-{team_id}"
    parsed = fallback.get(team_key)
    if not parsed or not isinstance(parsed, dict):
        raise ScraperError(f"No '{team_key}' in __NEXT_DATA__ fallback")
    _set_cached_team_data(team_id, parsed)
    return parsed


async def _resolve_fotmob_team_uncached(
    scraper,
    team_name: str,
    *,
    search_candidates: list[str],
    active_logger: logging.Logger,
) -> Optional[ResolvedFotmobTeam]:
    for search_term in search_candidates:
        try:
            raw = await scraper._fetch_with_retry(
                build_fotmob_search_url(search_term),
                headers=FOTMOB_HEADERS,
                json_mode=True,
            )
            ranked_ids = _extract_ranked_team_ids(json.loads(raw))
        except (ScraperError, RateLimitError, json.JSONDecodeError) as exc:
            active_logger.warning(
                "Fotmob team search failed for '%s' via '%s': %s",
                team_name,
                search_term,
                exc,
            )
            continue

        if not ranked_ids:
            continue

        expected_names = [team_name, search_term]
        for team_id, _count in ranked_ids:
            try:
                team_data = await fetch_fotmob_team_data(scraper, team_id)
            except (ScraperError, RateLimitError, json.JSONDecodeError) as exc:
                active_logger.warning(
                    "Fotmob team lookup failed for '%s' id=%s: %s",
                    team_name,
                    team_id,
                    exc,
                )
                continue

            details = team_data.get("details", {})
            actual_names = [
                details.get("name", ""),
                details.get("shortName", ""),
            ]
            if _names_match(expected_names, actual_names):
                return ResolvedFotmobTeam(
                    requested_name=team_name,
                    search_term=search_term,
                    team_id=team_id,
                    data=team_data,
                )

            active_logger.debug(
                "Rejected Fotmob candidate for '%s': id=%s names=%s",
                team_name,
                team_id,
                actual_names,
            )

    return None


async def resolve_fotmob_team(
    scraper,
    team_name: str,
    *,
    search_aliases: Optional[list[str]] = None,
    log: Optional[logging.Logger] = None,
) -> tuple[Optional[ResolvedFotmobTeam], list[str]]:
    active_logger = log or logger
    search_candidates = build_fotmob_search_candidates(
        team_name,
        search_aliases=search_aliases,
    )
    cached = _get_cached_resolved_team(team_name)
    if cached is not None:
        return cached, search_candidates

    loop = asyncio.get_running_loop()
    inflight_key = (id(loop), _cache_key(team_name))
    inflight = _RESOLVE_INFLIGHT.get(inflight_key)
    if inflight is not None:
        resolved = await inflight
        return resolved, search_candidates

    future: asyncio.Future[Optional[ResolvedFotmobTeam]] = loop.create_future()
    future.add_done_callback(_consume_future_exception)
    _RESOLVE_INFLIGHT[inflight_key] = future
    try:
        resolved = await _resolve_fotmob_team_uncached(
            scraper,
            team_name,
            search_candidates=search_candidates,
            active_logger=active_logger,
        )
        if resolved is not None:
            _set_cached_resolved_team(team_name, resolved)
        future.set_result(resolved)
        return resolved, search_candidates
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        _RESOLVE_INFLIGHT.pop(inflight_key, None)


# ── Schedule types ─────────────────────────────────────────────────────────────


@dataclass
class ScheduledMatch:
    time_utc: str   # e.g. "15:00"
    home: str
    away: str


@dataclass
class LeagueSchedule:
    league_name: str
    country_flag: str
    matches: list[ScheduledMatch] = _dc_field(default_factory=list)


# Fotmob league IDs → (display_name, flag)
_SCHEDULE_LEAGUES: dict[int, tuple[str, str]] = {
    47:  ("Premier League",   "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    54:  ("Bundesliga",       "🇩🇪"),
    87:  ("La Liga",          "🇪🇸"),
    55:  ("Serie A",          "🇮🇹"),
    53:  ("Ligue 1",          "🇫🇷"),
    42:  ("Champions League", "🏆"),
    73:  ("Europa League",    "🥈"),
    48:  ("Championship",     "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    57:  ("Eredivisie",       "🇳🇱"),
    61:  ("Primeira Liga",    "🇵🇹"),
}

FOTMOB_MATCHES_URL = "https://www.fotmob.com/api/matches"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league_slug}/scoreboard"
ESPN_HEADERS = {"Accept": "application/json"}
ESPN_SCHEDULE_LEAGUES: dict[str, tuple[str, str]] = {
    "eng.1": ("Premier League", "🏴"),
    "ger.1": ("Bundesliga", "🇩🇪"),
    "esp.1": ("La Liga", "🇪🇸"),
    "ita.1": ("Serie A", "🇮🇹"),
    "fra.1": ("Ligue 1", "🇫🇷"),
    "uefa.champions": ("Champions League", "🏆"),
    "uefa.europa": ("Europa League", "🥈"),
    "eng.2": ("Championship", "🏴"),
    "ned.1": ("Eredivisie", "🇳🇱"),
    "por.1": ("Primeira Liga", "🇵🇹"),
}


class _TempScraper:
    """Minimal HTTP helper for fetch_today_matches when no scraper instance is provided."""
    DOMAIN = "fotmob.com"

    async def _fetch_with_retry(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        json_mode: bool = False,
    ) -> str:
        import aiohttp
        from scrapers.network import build_client_session
        full_url = url
        if params:
            full_url = url + "?" + urllib.parse.urlencode(params)
        async with build_client_session(timeout_total=15, ssl=False) as session:
            async with session.get(
                full_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.text()

    async def _close(self) -> None:
        pass


async def fetch_today_matches(
    scraper=None,
    date_str: Optional[str] = None,
) -> list[LeagueSchedule]:
    """
    Fetch today's matches for top leagues.

    Args:
        scraper: optional BaseScraper-compatible instance (creates _TempScraper if None)
        date_str: date in YYYYMMDD format (defaults to today UTC)

    Returns:
        List of LeagueSchedule for configured top leagues that have unfinished matches.
    """
    import json as _json
    from datetime import date as _date, datetime as _datetime, timezone as _tz

    if date_str is None:
        date_str = _date.today().strftime("%Y%m%d")

    owned = scraper is None
    if owned:
        scraper = _TempScraper()

    async def _fetch_league_schedule(
        league_slug: str,
        league_name: str,
        flag: str,
    ) -> Optional[LeagueSchedule]:
        raw = await scraper._fetch_with_retry(
            ESPN_SCOREBOARD_URL.format(league_slug=league_slug),
            params={"dates": date_str},
            headers=ESPN_HEADERS,
            json_mode=True,
        )
        data = _json.loads(raw)
        matches: list[ScheduledMatch] = []

        for event in data.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            status = (competition.get("status") or {}).get("type") or {}
            if status.get("completed"):
                continue

            competitors = competition.get("competitors") or []
            home = next((item for item in competitors if item.get("homeAway") == "home"), None)
            away = next((item for item in competitors if item.get("homeAway") == "away"), None)
            if not home or not away:
                continue

            home_name = ((home.get("team") or {}).get("displayName") or "?").strip()
            away_name = ((away.get("team") or {}).get("displayName") or "?").strip()
            kickoff_utc = competition.get("date") or event.get("date") or ""
            try:
                dt = _datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00")).astimezone(_tz.utc)
                time_str = dt.strftime("%H:%M")
            except (ValueError, AttributeError):
                time_str = "?"

            matches.append(
                ScheduledMatch(
                    time_utc=time_str,
                    home=home_name,
                    away=away_name,
                )
            )

        if not matches:
            return None
        return LeagueSchedule(
            league_name=league_name,
            country_flag=flag,
            matches=matches,
        )

    try:
        tasks = [
            _fetch_league_schedule(league_slug, league_name, flag)
            for league_slug, (league_name, flag) in ESPN_SCHEDULE_LEAGUES.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if owned:
            await scraper._close()

    schedules: list[LeagueSchedule] = []
    failures: list[Exception] = []
    for (league_slug, _meta), result in zip(ESPN_SCHEDULE_LEAGUES.items(), results):
        if isinstance(result, Exception):
            logger.warning("Schedule fetch failed for %s: %s", league_slug, result)
            failures.append(result)
            continue
        if result is not None:
            schedules.append(result)

    if schedules:
        return schedules
    if failures:
        raise failures[0]
    return []
