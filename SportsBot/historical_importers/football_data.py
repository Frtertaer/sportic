from __future__ import annotations

import csv
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

import database as db
from team_parser import normalize_team_name

logger = logging.getLogger(__name__)


FOOTBALL_DATA_DIVISIONS: dict[str, tuple[str, str]] = {
    "E0": ("Premier League", "England"),
    "E1": ("Championship", "England"),
    "E2": ("League One", "England"),
    "E3": ("League Two", "England"),
    "EC": ("Conference", "England"),
    "SP1": ("La Liga", "Spain"),
    "SP2": ("Segunda Division", "Spain"),
    "D1": ("Bundesliga", "Germany"),
    "D2": ("2. Bundesliga", "Germany"),
    "I1": ("Serie A", "Italy"),
    "I2": ("Serie B", "Italy"),
    "F1": ("Ligue 1", "France"),
    "F2": ("Ligue 2", "France"),
    "N1": ("Eredivisie", "Netherlands"),
    "P1": ("Primeira Liga", "Portugal"),
    "B1": ("Jupiler Pro League", "Belgium"),
    "SC0": ("Premiership", "Scotland"),
    "T1": ("Super Lig", "Turkey"),
    "G1": ("Super League", "Greece"),
}

FOOTBALL_DATA_TO_BETEXPLORER_LEAGUE_KEYS: dict[str, str] = {
    "E0": "eng.1",
    "E1": "eng.2",
    "SP1": "esp.1",
    "F1": "fra.1",
    "D1": "ger.1",
    "I1": "ita.1",
    "N1": "ned.1",
    "P1": "por.1",
}


@dataclass(slots=True)
class ImportSummary:
    csv_path: str
    run_id: Optional[int]
    competition_name: Optional[str]
    division_code: Optional[str]
    betexplorer_league_key: Optional[str]
    requested_from: Optional[str]
    requested_to: Optional[str]
    rows_processed: int = 0
    match_rows_inserted: int = 0
    match_rows_updated: int = 0
    market_rows_inserted: int = 0
    market_rows_updated: int = 0
    snapshot_rows_inserted: int = 0
    snapshot_rows_updated: int = 0
    skipped_rows: int = 0

    @property
    def total_records_inserted(self) -> int:
        return self.match_rows_inserted + self.market_rows_inserted + self.snapshot_rows_inserted

    @property
    def total_records_updated(self) -> int:
        return self.match_rows_updated + self.market_rows_updated + self.snapshot_rows_updated


@dataclass(slots=True)
class TeamState:
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0
    played: int = 0
    last_match_dt: Optional[datetime] = None
    recent_points: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_goal_diff: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_scored: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_conceded: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_shots_for: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_shots_allowed: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_sot_for: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_sot_allowed: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_corners_for: deque[int] = field(default_factory=lambda: deque(maxlen=5))
    recent_corners_allowed: deque[int] = field(default_factory=lambda: deque(maxlen=5))

    def update(
        self,
        goals_for: int,
        goals_against: int,
        match_dt: datetime,
        *,
        shots_for: Optional[int] = None,
        shots_allowed: Optional[int] = None,
        shots_on_target_for: Optional[int] = None,
        shots_on_target_allowed: Optional[int] = None,
        corners_for: Optional[int] = None,
        corners_allowed: Optional[int] = None,
    ) -> None:
        self.played += 1
        self.goals_for += goals_for
        self.goals_against += goals_against
        if goals_for > goals_against:
            points = 3
        elif goals_for == goals_against:
            points = 1
        else:
            points = 0
        self.points += points
        self.recent_points.append(points)
        self.recent_goal_diff.append(goals_for - goals_against)
        self.recent_scored.append(goals_for)
        self.recent_conceded.append(goals_against)
        if shots_for is not None:
            self.recent_shots_for.append(shots_for)
        if shots_allowed is not None:
            self.recent_shots_allowed.append(shots_allowed)
        if shots_on_target_for is not None:
            self.recent_sot_for.append(shots_on_target_for)
        if shots_on_target_allowed is not None:
            self.recent_sot_allowed.append(shots_on_target_allowed)
        if corners_for is not None:
            self.recent_corners_for.append(corners_for)
        if corners_allowed is not None:
            self.recent_corners_allowed.append(corners_allowed)
        self.last_match_dt = match_dt


def _parse_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported football-data date format: {value}")


def _parse_time(value: str) -> Optional[time]:
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _parse_int(value: object) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_float(value: object) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _pick_float(row: dict, *columns: str) -> Optional[float]:
    for column in columns:
        value = _parse_float(row.get(column))
        if value is not None:
            return value
    return None


def _normalized_probs_from_decimal(*odds: Optional[float]) -> tuple[Optional[float], ...]:
    if any(odd is None or odd <= 1.0 for odd in odds):
        return tuple(None for _ in odds)
    implied = [1.0 / float(odd) for odd in odds]
    total = sum(implied)
    if total <= 0:
        return tuple(None for _ in odds)
    return tuple(round((part / total) * 100.0, 4) for part in implied)


def _decimal_from_probability_pct(probability_pct: Optional[float]) -> Optional[float]:
    if probability_pct is None or probability_pct <= 0.0:
        return None
    return round(100.0 / probability_pct, 4)


def _assign_derived_moneyline_markets(payload: dict) -> None:
    implied_w1, implied_draw, implied_w2 = _normalized_probs_from_decimal(
        payload.get("odds_w1"),
        payload.get("odds_draw"),
        payload.get("odds_w2"),
    )
    payload["implied_prob_w1"] = implied_w1
    payload["implied_prob_draw"] = implied_draw
    payload["implied_prob_w2"] = implied_w2
    if None in (implied_w1, implied_draw, implied_w2):
        return
    p1 = float(implied_w1)
    pd = float(implied_draw)
    p2 = float(implied_w2)
    no_draw_total = p1 + p2
    if no_draw_total > 0:
        payload["odds_dnb_team1"] = _decimal_from_probability_pct((p1 * 100.0) / no_draw_total)
        payload["odds_dnb_team2"] = _decimal_from_probability_pct((p2 * 100.0) / no_draw_total)
    payload["odds_dc_1x"] = _decimal_from_probability_pct(p1 + pd)
    payload["odds_dc_12"] = _decimal_from_probability_pct(p1 + p2)
    payload["odds_dc_x2"] = _decimal_from_probability_pct(pd + p2)


def _compute_actual_result(value: str) -> Optional[str]:
    normalized = str(value or "").strip().upper()
    if normalized == "H":
        return "W1"
    if normalized == "A":
        return "W2"
    if normalized == "D":
        return "DRAW"
    return None


def _season_label(match_date: date) -> str:
    start_year = match_date.year if match_date.month >= 7 else match_date.year - 1
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def _provider_match_key(div_code: str, match_date: date, kickoff: Optional[time], team1: str, team2: str) -> str:
    time_part = kickoff.strftime("%H:%M") if kickoff else "00:00"
    return (
        "football-data.co.uk:"
        f"{div_code}:{match_date.isoformat()}:{time_part}:"
        f"{normalize_team_name(team1).lower()}:{normalize_team_name(team2).lower()}"
    )


def _line_string(team_name: str, handicap: Optional[float]) -> Optional[str]:
    if handicap is None:
        return None
    sign = "+" if handicap > 0 else ""
    return f"{team_name} {sign}{handicap:g}"


def _build_market_payload(row: dict, *, historical_match_id: int, team1_name: str, match_date: date) -> list[dict]:
    open_payload = {
        "historical_match_id": historical_match_id,
        "snapshot_kind": "OPEN",
        "captured_at": f"{match_date.isoformat()}T00:00:00+00:00",
        "bookmaker_count": None,
        "source_provider": "football-data.co.uk:consensus",
        "odds_w1": _pick_float(row, "AvgH", "PSH", "B365H", "BWH", "MaxH"),
        "odds_draw": _pick_float(row, "AvgD", "PSD", "B365D", "BWD", "MaxD"),
        "odds_w2": _pick_float(row, "AvgA", "PSA", "B365A", "BWA", "MaxA"),
        "odds_over_2_5": _pick_float(row, "Avg>2.5", "P>2.5", "B365>2.5", "Max>2.5"),
        "odds_under_2_5": _pick_float(row, "Avg<2.5", "P<2.5", "B365<2.5", "Max<2.5"),
        "ah_main_line": _line_string(team1_name, _pick_float(row, "AHh")),
        "ah_team1_odds": _pick_float(row, "AvgAHH", "PAHH", "B365AHH", "MaxAHH"),
        "ah_team2_odds": _pick_float(row, "AvgAHA", "PAHA", "B365AHA", "MaxAHA"),
    }
    _assign_derived_moneyline_markets(open_payload)

    close_payload = {
        "historical_match_id": historical_match_id,
        "snapshot_kind": "CLOSE",
        "captured_at": f"{match_date.isoformat()}T23:59:59+00:00",
        "bookmaker_count": None,
        "source_provider": "football-data.co.uk:consensus",
        "odds_w1": _pick_float(row, "AvgCH", "PSCH", "B365CH", "BWCH", "MaxCH", "CLCH"),
        "odds_draw": _pick_float(row, "AvgCD", "PSCD", "B365CD", "BWCD", "MaxCD", "CLCD"),
        "odds_w2": _pick_float(row, "AvgCA", "PSCA", "B365CA", "BWCA", "MaxCA", "CLCA"),
        "odds_over_2_5": _pick_float(row, "AvgC>2.5", "PC>2.5", "B365C>2.5", "MaxC>2.5"),
        "odds_under_2_5": _pick_float(row, "AvgC<2.5", "PC<2.5", "B365C<2.5", "MaxC<2.5"),
        "ah_main_line": _line_string(team1_name, _pick_float(row, "AHCh", "AHh")),
        "ah_team1_odds": _pick_float(row, "AvgCAHH", "PCAHH", "B365CAHH", "MaxCAHH"),
        "ah_team2_odds": _pick_float(row, "AvgCAHA", "PCAHA", "B365CAHA", "MaxCAHA"),
    }
    _assign_derived_moneyline_markets(close_payload)

    payloads = []
    for payload in (open_payload, close_payload):
        if any(
            payload.get(key) is not None
            for key in (
                "odds_w1",
                "odds_draw",
                "odds_w2",
                "odds_over_2_5",
                "odds_under_2_5",
                "ah_team1_odds",
                "ah_team2_odds",
            )
        ):
            payloads.append(payload)
    return payloads


def _average(values: deque[int]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _rolling_points(values: deque[int]) -> Optional[float]:
    if not values:
        return None
    return float(sum(values))


def _build_table_positions(team_states: dict[str, TeamState]) -> dict[str, int]:
    ordered = sorted(
        team_states.items(),
        key=lambda item: (
            -item[1].points,
            -(item[1].goals_for - item[1].goals_against),
            -item[1].goals_for,
            item[0],
        ),
    )
    return {team_key: index + 1 for index, (team_key, _state) in enumerate(ordered)}


def _rest_hours(current_match_dt: datetime, last_match_dt: Optional[datetime]) -> Optional[float]:
    if last_match_dt is None:
        return None
    delta = current_match_dt - last_match_dt
    if delta <= timedelta(0):
        return None
    return round(delta.total_seconds() / 3600.0, 4)


def _build_snapshot_payload(
    historical_match_id: int,
    *,
    competition_name: str,
    captured_at: str,
    team1_key: str,
    team2_key: str,
    current_match_dt: datetime,
    league_states: dict[str, TeamState],
) -> Optional[dict]:
    team1_state = league_states.get(team1_key)
    team2_state = league_states.get(team2_key)
    if team1_state is None and team2_state is None:
        return None

    positions = _build_table_positions(league_states) if league_states else {}
    team1_rest = _rest_hours(current_match_dt, None if team1_state is None else team1_state.last_match_dt)
    team2_rest = _rest_hours(current_match_dt, None if team2_state is None else team2_state.last_match_dt)

    available_groups = 0
    if team1_state and team2_state and team1_state.recent_points and team2_state.recent_points:
        available_groups += 1
    if team1_state and team2_state and team1_state.recent_scored and team2_state.recent_scored:
        available_groups += 1
    if team1_state and team2_state and team1_state.played > 0 and team2_state.played > 0:
        available_groups += 1
    if team1_state and team2_state and team1_state.recent_sot_for and team2_state.recent_sot_for:
        available_groups += 1

    return {
        "historical_match_id": historical_match_id,
        "snapshot_source": "football_data_csv_rolling",
        "captured_at": captured_at,
        "data_completeness": round(available_groups / 4.0, 4),
        "successful_source_count": available_groups,
        "total_source_count": 4,
        "team1_form_points_last5": None if team1_state is None else _rolling_points(team1_state.recent_points),
        "team2_form_points_last5": None if team2_state is None else _rolling_points(team2_state.recent_points),
        "team1_goal_diff_last5": None if team1_state is None else _average(team1_state.recent_goal_diff),
        "team2_goal_diff_last5": None if team2_state is None else _average(team2_state.recent_goal_diff),
        "team1_scored_avg": None if team1_state is None else _average(team1_state.recent_scored),
        "team2_scored_avg": None if team2_state is None else _average(team2_state.recent_scored),
        "team1_conceded_avg": None if team1_state is None else _average(team1_state.recent_conceded),
        "team2_conceded_avg": None if team2_state is None else _average(team2_state.recent_conceded),
        "team1_shots_avg": None if team1_state is None else _average(team1_state.recent_shots_for),
        "team2_shots_avg": None if team2_state is None else _average(team2_state.recent_shots_for),
        "team1_shots_allowed_avg": None if team1_state is None else _average(team1_state.recent_shots_allowed),
        "team2_shots_allowed_avg": None if team2_state is None else _average(team2_state.recent_shots_allowed),
        "team1_shots_on_target_avg": None if team1_state is None else _average(team1_state.recent_sot_for),
        "team2_shots_on_target_avg": None if team2_state is None else _average(team2_state.recent_sot_for),
        "team1_shots_on_target_allowed_avg": None if team1_state is None else _average(team1_state.recent_sot_allowed),
        "team2_shots_on_target_allowed_avg": None if team2_state is None else _average(team2_state.recent_sot_allowed),
        "team1_corners_avg": None if team1_state is None else _average(team1_state.recent_corners_for),
        "team2_corners_avg": None if team2_state is None else _average(team2_state.recent_corners_for),
        "team1_corners_allowed_avg": None if team1_state is None else _average(team1_state.recent_corners_allowed),
        "team2_corners_allowed_avg": None if team2_state is None else _average(team2_state.recent_corners_allowed),
        "team1_table_position": positions.get(team1_key),
        "team2_table_position": positions.get(team2_key),
        "team1_points": None if team1_state is None else team1_state.points,
        "team2_points": None if team2_state is None else team2_state.points,
        "rest_gap_hours": (
            round(team1_rest - team2_rest, 4)
            if team1_rest is not None and team2_rest is not None
            else None
        ),
        "team1_short_rest": None if team1_rest is None else team1_rest < 72.0,
        "team2_short_rest": None if team2_rest is None else team2_rest < 72.0,
        "competition_name": competition_name,
        "schedule_density_note": "Derived from football-data.co.uk rolling same-league history before kickoff.",
    }


class FootballDataHistoricalImporter:
    def __init__(self, *, csv_path: str | Path, dry_run: bool = False) -> None:
        self.csv_path = Path(csv_path)
        self.dry_run = dry_run

    def _load_rows(self) -> list[dict]:
        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [row for row in reader]
        rows.sort(
            key=lambda row: (
                _parse_date(str(row.get("Date") or "")),
                _parse_time(str(row.get("Time") or "")) or time(0, 0),
                str(row.get("HomeTeam") or ""),
                str(row.get("AwayTeam") or ""),
            )
        )
        return rows

    async def run(self) -> ImportSummary:
        rows = self._load_rows()
        competition_name = None
        division_code = None
        if rows:
            division_code = str(rows[0].get("Div") or "").strip() or None
            competition_name = FOOTBALL_DATA_DIVISIONS.get(
                division_code or "",
                (str(rows[0].get("Div") or "").strip() or None, None),
            )[0]
        summary = ImportSummary(
            csv_path=str(self.csv_path),
            run_id=None,
            competition_name=competition_name,
            division_code=division_code,
            betexplorer_league_key=FOOTBALL_DATA_TO_BETEXPLORER_LEAGUE_KEYS.get(division_code or ""),
            requested_from=None,
            requested_to=None,
        )
        if rows:
            parsed_dates = [_parse_date(str(row.get("Date") or "")) for row in rows]
            summary.requested_from = min(parsed_dates).isoformat()
            summary.requested_to = max(parsed_dates).isoformat()

        run_id: Optional[int] = None
        if not self.dry_run:
            run_id = await db.start_historical_import_run(
                "CSV_IMPORT",
                "football-data.co.uk",
                competition_name=competition_name,
                requested_from=summary.requested_from,
                requested_to=summary.requested_to,
                season_label=None,
            )
            summary.run_id = run_id

        states_by_competition: dict[tuple[str, str], dict[str, TeamState]] = {}
        try:
            for row in rows:
                try:
                    div_code = str(row.get("Div") or "").strip()
                    match_date = _parse_date(str(row.get("Date") or ""))
                    kickoff_time = _parse_time(str(row.get("Time") or ""))
                    match_dt = datetime.combine(match_date, kickoff_time or time(0, 0))
                    season_label = _season_label(match_date)
                    competition_name, competition_country = FOOTBALL_DATA_DIVISIONS.get(
                        div_code,
                        (div_code or "Unknown", None),
                    )

                    team1_name = str(row.get("HomeTeam") or "").strip()
                    team2_name = str(row.get("AwayTeam") or "").strip()
                    team1_score = _parse_int(row.get("FTHG"))
                    team2_score = _parse_int(row.get("FTAG"))
                    actual_result = _compute_actual_result(str(row.get("FTR") or ""))
                    team1_shots = _parse_int(row.get("HS"))
                    team2_shots = _parse_int(row.get("AS"))
                    team1_shots_on_target = _parse_int(row.get("HST"))
                    team2_shots_on_target = _parse_int(row.get("AST"))
                    team1_corners = _parse_int(row.get("HC"))
                    team2_corners = _parse_int(row.get("AC"))
                    if not (team1_name and team2_name and team1_score is not None and team2_score is not None and actual_result):
                        summary.skipped_rows += 1
                        continue

                    provider_match_key = _provider_match_key(
                        div_code,
                        match_date,
                        kickoff_time,
                        team1_name,
                        team2_name,
                    )
                    existing = await db.get_historical_match_by_provider_key("football-data.co.uk", provider_match_key)
                    match_payload = {
                        "provider_match_key": provider_match_key,
                        "provider_name": "football-data.co.uk",
                        "competition_name": competition_name,
                        "competition_country": competition_country,
                        "season_label": season_label,
                        "match_date_utc": match_date.isoformat(),
                        "kickoff_utc": match_dt.replace(tzinfo=timezone.utc).isoformat(),
                        "team1_name": team1_name,
                        "team2_name": team2_name,
                        "team1_normalized": normalize_team_name(team1_name).lower(),
                        "team2_normalized": normalize_team_name(team2_name).lower(),
                        "team1_score": team1_score,
                        "team2_score": team2_score,
                        "actual_result": actual_result,
                        "status": "FINISHED",
                    }
                    if not self.dry_run:
                        historical_match_id = await db.upsert_historical_match(match_payload)
                    else:
                        historical_match_id = existing["id"] if existing else 0
                    if existing is None:
                        summary.match_rows_inserted += 1
                    else:
                        summary.match_rows_updated += 1

                    competition_key = (competition_name, season_label)
                    league_states = states_by_competition.setdefault(competition_key, {})
                    team1_key = normalize_team_name(team1_name).lower()
                    team2_key = normalize_team_name(team2_name).lower()

                    snapshot_payload = _build_snapshot_payload(
                        historical_match_id,
                        competition_name=competition_name,
                        captured_at=f"{match_date.isoformat()}T00:00:00+00:00",
                        team1_key=team1_key,
                        team2_key=team2_key,
                        current_match_dt=match_dt,
                        league_states=league_states,
                    )
                    if snapshot_payload is not None:
                        had_existing_snapshot = False
                        if not self.dry_run and historical_match_id:
                            had_existing_snapshot = await db.get_historical_team_snapshot(historical_match_id) is not None
                            await db.upsert_historical_team_snapshot(snapshot_payload)
                        if had_existing_snapshot:
                            summary.snapshot_rows_updated += 1
                        else:
                            summary.snapshot_rows_inserted += 1

                    existing_lines = [] if self.dry_run or not historical_match_id else await db.get_historical_market_lines(historical_match_id)
                    for market_payload in _build_market_payload(
                        row,
                        historical_match_id=historical_match_id,
                        team1_name=team1_name,
                        match_date=match_date,
                    ):
                        had_existing_line = any(
                            line.get("snapshot_kind") == market_payload["snapshot_kind"]
                            and line.get("source_provider") == market_payload["source_provider"]
                            for line in existing_lines
                        )
                        if not self.dry_run and historical_match_id:
                            await db.upsert_historical_market_line(market_payload)
                        if had_existing_line:
                            summary.market_rows_updated += 1
                        else:
                            summary.market_rows_inserted += 1

                    if not self.dry_run and historical_match_id:
                        await db.upsert_historical_provider_coverage(
                            {
                                "historical_match_id": historical_match_id,
                                "source_key": "football_data_csv",
                                "source_label": "football-data.co.uk CSV",
                                "status": "OK",
                                "error_type": None,
                                "error_message": None,
                                "duration_ms": None,
                                "provider_name": "football-data.co.uk",
                            }
                        )

                    state1 = league_states.setdefault(team1_key, TeamState())
                    state2 = league_states.setdefault(team2_key, TeamState())
                    state1.update(
                        team1_score,
                        team2_score,
                        match_dt,
                        shots_for=team1_shots,
                        shots_allowed=team2_shots,
                        shots_on_target_for=team1_shots_on_target,
                        shots_on_target_allowed=team2_shots_on_target,
                        corners_for=team1_corners,
                        corners_allowed=team2_corners,
                    )
                    state2.update(
                        team2_score,
                        team1_score,
                        match_dt,
                        shots_for=team2_shots,
                        shots_allowed=team1_shots,
                        shots_on_target_for=team2_shots_on_target,
                        shots_on_target_allowed=team1_shots_on_target,
                        corners_for=team2_corners,
                        corners_allowed=team1_corners,
                    )
                    summary.rows_processed += 1
                except Exception:
                    logger.exception(
                        "football-data.co.uk row import failed: %s vs %s",
                        row.get("HomeTeam"),
                        row.get("AwayTeam"),
                    )
                    summary.skipped_rows += 1

            if run_id is not None:
                await db.finish_historical_import_run(
                    run_id,
                    status="SUCCESS",
                    records_inserted=summary.total_records_inserted,
                    records_updated=summary.total_records_updated,
                    records_failed=summary.skipped_rows,
                    error_summary=None if summary.skipped_rows == 0 else f"{summary.skipped_rows} row(s) skipped during csv import",
                )
            return summary
        except Exception as exc:
            if run_id is not None:
                await db.finish_historical_import_run(
                    run_id,
                    status="FAILED",
                    records_inserted=summary.total_records_inserted,
                    records_updated=summary.total_records_updated,
                    records_failed=max(summary.skipped_rows, 1),
                    error_summary=str(exc),
                )
            raise
