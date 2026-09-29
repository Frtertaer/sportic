"""Strict Winline football snapshot adapter for the paper council.

The browser receiver is deliberately kept outside the council process.  This
module only accepts a versioned, fresh snapshot, converts one complete Winline
quote into no-vig probabilities, and performs a fail-closed identity match to
an ESPN event used for schedule/result resolution.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WINLINE_SNAPSHOT_SCHEMA = "winline-football-snapshot-v1"
WINLINE_SOURCE = "winline-public-prematch-board"
WINLINE_SOURCE_URL = "https://winline.ru/stavki/sport/futbol"
TEAM_MATCH_VERSION = "winline-espn-team-normalisation-v1"

_EVENT_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_RELATIVE_KICKOFF_RE = re.compile(
    r"^(сегодня|завтра)\s+(\d{1,2}:\d{2})$", re.IGNORECASE
)
_NAMED_DATE_RE = re.compile(
    r"^(\d{1,2})\s+([а-яё.]+)(?:\s+(\d{4}))?\s+(\d{1,2}:\d{2})$",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(
    r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?\s+(\d{1,2}:\d{2})$"
)

_RU_MONTHS = {
    "января": 1,
    "янв": 1,
    "февраля": 2,
    "фев": 2,
    "марта": 3,
    "мар": 3,
    "апреля": 4,
    "апр": 4,
    "мая": 5,
    "май": 5,
    "июня": 6,
    "июн": 6,
    "июля": 7,
    "июл": 7,
    "августа": 8,
    "авг": 8,
    "сентября": 9,
    "сен": 9,
    "сент": 9,
    "октября": 10,
    "окт": 10,
    "ноября": 11,
    "ноя": 11,
    "декабря": 12,
    "дек": 12,
}

_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

_LEAGUE_COMPETITION_MARKERS = {
    "bra.1": ("\u0431\u0440\u0430\u0437\u0438\u043b\u0438\u044f",),
    "fin.1": ("\u0444\u0438\u043d\u043b\u044f\u043d\u0434\u0438\u044f",),
    "nor.1": ("\u043d\u043e\u0440\u0432\u0435\u0433\u0438\u044f",),
    "swe.1": ("\u0448\u0432\u0435\u0446\u0438\u044f",),
}
_LEAGUE_NOISE_TOKENS = {
    "bra.1": frozenset({"ec", "fc"}),
    "fin.1": frozenset({"fc", "ifk"}),
    "nor.1": frozenset({"bk", "fk", "if", "il"}),
    "swe.1": frozenset({"bk", "ff", "if", "ik", "is"}),
}
_LEAGUE_TEAM_ALIASES = {
    "bra.1": {
        "atletico mg": "atletico mineiro",
        "atletiko mineiro": "atletico mineiro",
        "bahia": "bahia",
        "baiya ba": "bahia",
        "corinthians": "corinthians",
        "korintians": "corinthians",
        "cruzeiro": "cruzeiro",
        "kruzeiro": "cruzeiro",
    },
    "swe.1": {
        "halmstads": "halmstad",
        "khalmstad": "halmstad",
        "hacken": "hacken",
        "khekken": "hacken",
        "hammarby": "hammarby",
        "khammarbyu": "hammarby",
        "malme": "malmo",
        "malmo": "malmo",
        "djurgarden": "djurgarden",
        "yurgorden": "djurgarden",
    },
}


class WinlineSnapshotError(ValueError):
    """The Winline snapshot or a provider identity is unsafe to consume."""


class AmbiguousWinlineMatch(WinlineSnapshotError):
    """More than one Winline row matched the same settlement event."""


def _as_utc(value: datetime | str, *, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError(f"{label} is empty")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    else:
        raise TypeError(f"{label} must be a timestamp string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    clean = _SPACE_RE.sub(" ", value).strip()
    if not clean or len(clean) > max_length or _CONTROL_RE.search(clean):
        raise ValueError(f"{label} is empty, too long, or contains control characters")
    return clean


def _strict_price(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("decimal price must be a JSON number")
    price = float(value)
    if not math.isfinite(price) or not 1.0 < price <= 1000.0:
        raise ValueError("decimal price must be finite and in (1.0, 1000.0]")
    return price


class H2HQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home: float
    draw: float
    away: float

    _prices = field_validator("home", "draw", "away", mode="before")(_strict_price)

    @model_validator(mode="after")
    def validate_overround(self) -> "H2HQuote":
        overround = sum(1.0 / price for price in (self.home, self.draw, self.away))
        if not 0.97 <= overround <= 1.50:
            raise ValueError("1X2 implied-probability sum is implausible")
        return self


class Totals25Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    under: float
    over: float

    _prices = field_validator("under", "over", mode="before")(_strict_price)

    @model_validator(mode="after")
    def validate_overround(self) -> "Totals25Quote":
        overround = (1.0 / self.under) + (1.0 / self.over)
        if not 0.97 <= overround <= 1.35:
            raise ValueError("totals implied-probability sum is implausible")
        return self


class WinlineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["winline"]
    event_id: str
    competition: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    captured_at: datetime
    h2h: H2HQuote
    totals_2_5: Totals25Quote

    @field_validator("event_id", mode="before")
    @classmethod
    def validate_provider_id(cls, value: Any) -> str:
        if not isinstance(value, str) or not _EVENT_ID_RE.fullmatch(value.strip()):
            raise ValueError("provider ids must be positive decimal strings")
        return value.strip()

    @field_validator("competition", mode="before")
    @classmethod
    def validate_competition(cls, value: Any) -> str:
        return _clean_text(value, label="competition", max_length=180)

    @field_validator("home_team", "away_team", mode="before")
    @classmethod
    def validate_team(cls, value: Any) -> str:
        return _clean_text(value, label="team name", max_length=120)

    @field_validator("kickoff_utc", "captured_at", mode="before")
    @classmethod
    def validate_timestamp(cls, value: Any, info: Any) -> datetime:
        return _as_utc(value, label=info.field_name)

    @model_validator(mode="after")
    def validate_identity_and_time(self) -> "WinlineEvent":
        if normalise_team_name(self.home_team) == normalise_team_name(self.away_team):
            raise ValueError("home and away teams must differ")
        if self.kickoff_utc <= self.captured_at:
            raise ValueError("prematch kickoff must be after quote capture")
        return self


class WinlineSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[WINLINE_SNAPSHOT_SCHEMA]
    source: Literal[WINLINE_SOURCE]
    source_url: Literal[WINLINE_SOURCE_URL]
    captured_at: datetime
    scan_complete: Literal[True]
    scan_stop_reason: Literal["stable", "bounded"]
    scan_iterations: int = Field(ge=1, le=1000)
    board_event_count: int = Field(ge=1, le=10000)
    event_count: int = Field(ge=1, le=5000)
    events: list[WinlineEvent] = Field(min_length=1, max_length=5000)

    @field_validator("captured_at", mode="before")
    @classmethod
    def validate_captured_at(cls, value: Any) -> datetime:
        return _as_utc(value, label="captured_at")

    @model_validator(mode="after")
    def validate_snapshot(self) -> "WinlineSnapshot":
        if self.event_count != len(self.events):
            raise ValueError("event_count does not equal the number of events")
        if self.board_event_count < self.event_count:
            raise ValueError("board_event_count cannot be smaller than event_count")
        ids = [event.event_id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot contains duplicate Winline event ids")
        if any(event.captured_at > self.captured_at + timedelta(seconds=5) for event in self.events):
            raise ValueError("an event capture is newer than the snapshot completion time")
        return self


def parse_kickoff_utc(text: str, *, now: datetime | str) -> datetime:
    """Parse a Winline prematch label in a browser forced to the UTC timezone.

    Live clock labels (for example ``2Т 77'``) and any unrecognised format fail
    closed.  Explicit dates without a year are assigned to the nearest future
    occurrence relative to ``now``.
    """

    reference = _as_utc(now, label="now")
    label = _SPACE_RE.sub(" ", _clean_text(text, label="kickoff", max_length=80)).strip()
    lowered = label.casefold().replace("ё", "е")

    relative = _RELATIVE_KICKOFF_RE.fullmatch(lowered)
    if relative:
        hour, minute = _parse_clock(relative.group(2))
        day_offset = 0 if relative.group(1).casefold() == "сегодня" else 1
        target_date = (reference + timedelta(days=day_offset)).date()
        candidate = datetime(
            target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=timezone.utc
        )
        if candidate <= reference:
            raise ValueError("relative kickoff is not in the future")
        return candidate

    named = _NAMED_DATE_RE.fullmatch(lowered)
    if named:
        day = int(named.group(1))
        month_name = named.group(2).rstrip(".")
        month = _RU_MONTHS.get(month_name)
        if month is None:
            raise ValueError("unknown Russian month in kickoff")
        year = int(named.group(3)) if named.group(3) else reference.year
        hour, minute = _parse_clock(named.group(4))
        return _future_calendar_kickoff(day, month, year, hour, minute, reference, bool(named.group(3)))

    numeric = _NUMERIC_DATE_RE.fullmatch(lowered)
    if numeric:
        day, month = int(numeric.group(1)), int(numeric.group(2))
        raw_year = numeric.group(3)
        if raw_year:
            year = int(raw_year)
            if year < 100:
                year += 2000
        else:
            year = reference.year
        hour, minute = _parse_clock(numeric.group(4))
        return _future_calendar_kickoff(day, month, year, hour, minute, reference, bool(raw_year))

    raise ValueError("unrecognised or live Winline kickoff label")


def _parse_clock(value: str) -> tuple[int, int]:
    match = _CLOCK_RE.fullmatch(value)
    if not match:
        raise ValueError("invalid kickoff clock")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("invalid kickoff clock")
    return hour, minute


def _future_calendar_kickoff(
    day: int,
    month: int,
    year: int,
    hour: int,
    minute: int,
    reference: datetime,
    explicit_year: bool,
) -> datetime:
    try:
        candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("invalid calendar kickoff") from exc
    if not explicit_year and candidate <= reference:
        try:
            candidate = candidate.replace(year=candidate.year + 1)
        except ValueError as exc:
            raise ValueError("invalid next-year kickoff") from exc
    if candidate <= reference or candidate - reference > timedelta(days=370):
        raise ValueError("calendar kickoff is not a plausible future event")
    return candidate


def normalise_team_name(value: str) -> str:
    """Return a deterministic Latin token form without fuzzy guessing."""

    clean = _clean_text(value, label="team name", max_length=120).casefold()
    clean = unicodedata.normalize("NFKD", clean)
    clean = "".join(character for character in clean if not unicodedata.combining(character))
    transliterated = "".join(_CYRILLIC_TO_LATIN.get(character, character) for character in clean)
    tokens = [token for token in _NON_ALNUM_RE.split(transliterated) if token]
    return " ".join(tokens)


def team_names_match(left: str, right: str) -> bool:
    """Conservative deterministic match used only alongside kickoff identity."""

    left_name, right_name = normalise_team_name(left), normalise_team_name(right)
    if not left_name or not right_name:
        return False
    if left_name == right_name:
        return True
    left_compact, right_compact = left_name.replace(" ", ""), right_name.replace(" ", "")
    if left_compact == right_compact:
        return True
    return False


def _league_team_name(value: str, league: str) -> str:
    tokens = normalise_team_name(value).split()
    noise = _LEAGUE_NOISE_TOKENS.get(league, frozenset())
    canonical = " ".join(token for token in tokens if token not in noise)
    return _LEAGUE_TEAM_ALIASES.get(league, {}).get(canonical, canonical)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r} is forbidden")


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_snapshot(
    path: str | Path,
    now: datetime | str | None = None,
    max_age_seconds: int = 900,
) -> dict[str, Any]:
    """Load and strictly validate a fresh Winline snapshot.

    No cache fallback is performed.  A stale, future-dated, malformed, partial,
    duplicate-key, or non-standard JSON snapshot raises ``WinlineSnapshotError``.
    """

    snapshot_path = Path(path)
    if max_age_seconds <= 0:
        raise WinlineSnapshotError("max_age_seconds must be positive")
    try:
        stat = snapshot_path.stat()
        if not snapshot_path.is_file() or stat.st_size <= 0 or stat.st_size > 10_000_000:
            raise WinlineSnapshotError("snapshot is not a regular file of a safe size")
        raw = snapshot_path.read_text(encoding="utf-8")
        payload = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        snapshot = WinlineSnapshot.model_validate(payload)
    except WinlineSnapshotError:
        raise
    except Exception as exc:
        raise WinlineSnapshotError(f"invalid Winline snapshot: {type(exc).__name__}") from exc

    reference = _as_utc(now or datetime.now(timezone.utc), label="now")
    age = (reference - snapshot.captured_at).total_seconds()
    if age < -60:
        raise WinlineSnapshotError("snapshot capture time is in the future")
    if age > max_age_seconds:
        raise WinlineSnapshotError("snapshot is stale")
    for event in snapshot.events:
        event_age = (reference - event.captured_at).total_seconds()
        if event_age < -60 or event_age > max_age_seconds:
            raise WinlineSnapshotError("snapshot contains a stale or future-dated event quote")
    return snapshot.model_dump(mode="json")


def _no_vig(prices: Mapping[str, float]) -> dict[str, float]:
    inverses = {key: 1.0 / float(value) for key, value in prices.items()}
    total = sum(inverses.values())
    if not math.isfinite(total) or total <= 0.0:
        raise WinlineSnapshotError("cannot calculate no-vig probabilities")
    result = {key: round(value * 100.0 / total, 4) for key, value in inverses.items()}
    largest = max(result, key=result.get)
    result[largest] = round(result[largest] + 100.0 - sum(result.values()), 4)
    return result


def benchmark_from_event(row: Mapping[str, Any]) -> dict[str, float]:
    """Build the council's five-key no-vig benchmark from one Winline quote."""

    try:
        event = WinlineEvent.model_validate(dict(row))
    except Exception as exc:
        raise WinlineSnapshotError(f"invalid Winline event: {type(exc).__name__}") from exc
    one_x_two = _no_vig(
        {"TEAM1": event.h2h.home, "DRAW": event.h2h.draw, "TEAM2": event.h2h.away}
    )
    totals = _no_vig({"OVER": event.totals_2_5.over, "UNDER": event.totals_2_5.under})
    return {
        "1X2:TEAM1": one_x_two["TEAM1"],
        "1X2:DRAW": one_x_two["DRAW"],
        "1X2:TEAM2": one_x_two["TEAM2"],
        "TOTALS_2_5:OVER": totals["OVER"],
        "TOTALS_2_5:UNDER": totals["UNDER"],
    }


def _espn_identity(event: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(event, Mapping):
        raise WinlineSnapshotError("ESPN event must be a mapping")
    event_id = event.get("event_id") or event.get("id")
    kickoff = event.get("kickoff") or event.get("commence_time") or event.get("date")
    home = event.get("home") or event.get("home_team")
    away = event.get("away") or event.get("away_team")
    league = event.get("league") or event.get("sport_key") or "espn_soccer"

    competitions = event.get("competitions") or []
    competition = competitions[0] if isinstance(competitions, list) and competitions else {}
    if not kickoff and isinstance(competition, Mapping):
        kickoff = competition.get("date")
    if (not home or not away) and isinstance(competition, Mapping):
        for competitor in competition.get("competitors") or []:
            if not isinstance(competitor, Mapping):
                continue
            side = competitor.get("homeAway")
            team = competitor.get("team") or {}
            if not isinstance(team, Mapping):
                continue
            name = team.get("displayName") or team.get("name") or team.get("shortDisplayName")
            if side == "home":
                home = name
            elif side == "away":
                away = name

    try:
        clean_event_id = _clean_text(str(event_id or ""), label="ESPN event id", max_length=80)
        clean_home = _clean_text(home, label="ESPN home team", max_length=120)
        clean_away = _clean_text(away, label="ESPN away team", max_length=120)
        clean_league = _clean_text(str(league), label="ESPN league", max_length=100)
        kickoff_utc = _as_utc(kickoff, label="ESPN kickoff")
    except Exception as exc:
        raise WinlineSnapshotError(f"invalid ESPN event identity: {type(exc).__name__}") from exc
    if not _EVENT_ID_RE.fullmatch(clean_event_id):
        raise WinlineSnapshotError("ESPN event id must be a positive decimal string")
    if normalise_team_name(clean_home) == normalise_team_name(clean_away):
        raise WinlineSnapshotError("ESPN home and away teams must differ")
    return {
        "event_id": clean_event_id,
        "kickoff_utc": _iso_utc(kickoff_utc),
        "home_team": clean_home,
        "away_team": clean_away,
        "league": clean_league,
    }


def match_espn_event(
    espn_event: Mapping[str, Any],
    winline_events: Sequence[Mapping[str, Any]],
    max_kickoff_delta_seconds: int = 600,
) -> dict[str, Any] | None:
    """Return one council-ready Winline/ESPN event or fail on ambiguity.

    Home/away orientation must agree, both team names must pass conservative
    deterministic normalisation, and kickoff times must be within the supplied
    tolerance.  A reverse-orientation or fuzzy-only match is never accepted.
    """

    if not isinstance(max_kickoff_delta_seconds, int) or not 0 <= max_kickoff_delta_seconds <= 3600:
        raise WinlineSnapshotError("max_kickoff_delta_seconds must be in [0, 3600]")
    identity = _espn_identity(espn_event)
    league = identity["league"]
    if league not in _LEAGUE_COMPETITION_MARKERS:
        raise WinlineSnapshotError("unsupported ESPN league for Winline identity matching")
    espn_kickoff = _as_utc(identity["kickoff_utc"], label="ESPN kickoff")
    matches: list[tuple[WinlineEvent, float]] = []
    for raw in winline_events:
        try:
            candidate = WinlineEvent.model_validate(dict(raw))
        except Exception as exc:
            raise WinlineSnapshotError(f"invalid candidate Winline event: {type(exc).__name__}") from exc
        delta = abs((candidate.kickoff_utc - espn_kickoff).total_seconds())
        if delta > max_kickoff_delta_seconds:
            continue
        competition = candidate.competition.casefold()
        if not any(
            marker in competition for marker in _LEAGUE_COMPETITION_MARKERS[league]
        ):
            continue
        if _league_team_name(identity["home_team"], league) != _league_team_name(
            candidate.home_team, league
        ):
            continue
        if _league_team_name(identity["away_team"], league) != _league_team_name(
            candidate.away_team, league
        ):
            continue
        matches.append((candidate, delta))

    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousWinlineMatch(
            f"ESPN event {identity['event_id']} matched {len(matches)} Winline events"
        )
    candidate, delta = matches[0]
    event = candidate.model_dump(mode="json")
    benchmark = benchmark_from_event(event)
    return {
        "event_id": identity["event_id"],
        "event_namespace": "espn",
        "settlement_provider": "espn",
        "settlement_event_id": identity["event_id"],
        "price_provider": "winline",
        "price_event_id": candidate.event_id,
        "sport_key": identity["league"],
        "competition": candidate.competition,
        "home_team": identity["home_team"],
        "away_team": identity["away_team"],
        "commence_time": identity["kickoff_utc"],
        "captured_at": _iso_utc(candidate.captured_at),
        "n_price_sources": 1,
        "h2h": candidate.h2h.model_dump(),
        "totals_2_5": candidate.totals_2_5.model_dump(),
        "benchmark_probabilities": benchmark,
        "benchmark_source_counts": {"1X2": 1, "TOTALS_2_5": 1},
        "benchmark_is_executable": False,
        "settlement_basis": "REGULATION_ONLY_LEAGUE_FINAL",
        "identity_match": {
            "version": TEAM_MATCH_VERSION,
            "kickoff_delta_seconds": delta,
            "winline_home_team": candidate.home_team,
            "winline_away_team": candidate.away_team,
        },
    }


__all__ = [
    "AmbiguousWinlineMatch",
    "TEAM_MATCH_VERSION",
    "WINLINE_SNAPSHOT_SCHEMA",
    "WINLINE_SOURCE",
    "WINLINE_SOURCE_URL",
    "WinlineEvent",
    "WinlineSnapshot",
    "WinlineSnapshotError",
    "benchmark_from_event",
    "load_snapshot",
    "match_espn_event",
    "normalise_team_name",
    "parse_kickoff_utc",
    "team_names_match",
]
