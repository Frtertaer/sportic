"""
Pydantic v2 data models — shared contracts between all modules.

Design rules:
- Every scraper model has scrape_success: bool (never raises, encodes failure)
- All fields are Optional where data might be missing
- MatchData aggregates scraper outputs; properties expose data quality
- PredictionReport is the AI output — input to formatter.py
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class MatchResult(str, Enum):
    WIN = "W"
    DRAW = "D"
    LOSS = "L"

class PlayerStatus(str, Enum):
    AVAILABLE = "available"
    INJURED = "injured"
    SUSPENDED = "suspended"
    DOUBTFUL = "doubtful"

class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ── Flashscore models ─────────────────────────────────────────────────────────

class RecentMatch(BaseModel):
    date: str
    opponent: str
    result: MatchResult
    score: str            # "2-1"
    home_away: str        # "H" or "A"
    competition: str = ""

class H2HRecord(BaseModel):
    date: str
    home_team: str
    away_team: str
    score: str
    competition: str = ""

class FlashscoreData(BaseModel):
    team1_name: str
    team2_name: str
    team1_form: list[RecentMatch] = Field(default_factory=list)
    team2_form: list[RecentMatch] = Field(default_factory=list)
    h2h_matches: list[H2HRecord] = Field(default_factory=list)
    team1_goals_scored_avg: Optional[float] = None
    team1_goals_conceded_avg: Optional[float] = None
    team2_goals_scored_avg: Optional[float] = None
    team2_goals_conceded_avg: Optional[float] = None
    scrape_success: bool = True
    error_message: Optional[str] = None
    league_name: Optional[str] = None


# ── Sofascore models ──────────────────────────────────────────────────────────

class Player(BaseModel):
    name: str
    position: str = ""
    status: PlayerStatus = PlayerStatus.AVAILABLE
    injury_detail: Optional[str] = None

class SofascoreData(BaseModel):
    team1_name: str
    team2_name: str
    team1_squad: list[Player] = Field(default_factory=list)
    team2_squad: list[Player] = Field(default_factory=list)
    team1_predicted_lineup: list[str] = Field(default_factory=list)
    team2_predicted_lineup: list[str] = Field(default_factory=list)
    team1_key_absences: list[Player] = Field(default_factory=list)
    team2_key_absences: list[Player] = Field(default_factory=list)
    scrape_success: bool = True
    error_message: Optional[str] = None


# ── Transfermarkt models ──────────────────────────────────────────────────────

class TransfermarktData(BaseModel):
    team1_name: str
    team2_name: str
    team1_market_value_eur: Optional[float] = None   # millions EUR
    team2_market_value_eur: Optional[float] = None
    team1_avg_player_value: Optional[float] = None
    team2_avg_player_value: Optional[float] = None
    scrape_success: bool = True
    error_message: Optional[str] = None


# ── OddsPortal models ─────────────────────────────────────────────────────────

class OddsData(BaseModel):
    team1_win_odds: Optional[float] = None
    draw_odds: Optional[float] = None
    team2_win_odds: Optional[float] = None
    over_2_5_odds: Optional[float] = None
    under_2_5_odds: Optional[float] = None
    over_1_5_odds: Optional[float] = None
    under_1_5_odds: Optional[float] = None
    over_3_5_odds: Optional[float] = None
    under_3_5_odds: Optional[float] = None
    dnb_team1_odds: Optional[float] = None
    dnb_team2_odds: Optional[float] = None
    double_chance_1x_odds: Optional[float] = None
    double_chance_12_odds: Optional[float] = None
    double_chance_x2_odds: Optional[float] = None
    asian_handicap: Optional[str] = None
    asian_handicap_line: Optional[float] = None
    asian_handicap_home_odds: Optional[float] = None
    asian_handicap_away_odds: Optional[float] = None
    btts_yes_odds: Optional[float] = None
    btts_no_odds: Optional[float] = None
    # Overround-adjusted implied probabilities (0.0–1.0)
    implied_prob_w1: Optional[float] = None
    implied_prob_draw: Optional[float] = None
    implied_prob_w2: Optional[float] = None
    scrape_success: bool = True
    error_message: Optional[str] = None


class TeamAdvancedMetrics(BaseModel):
    rating: Optional[float] = None
    rating_rank: Optional[int] = None
    xg: Optional[float] = None
    xg_rank: Optional[int] = None
    xga: Optional[float] = None
    xga_rank: Optional[int] = None
    xg_diff: Optional[float] = None
    xg_diff_rank: Optional[int] = None
    possession_pct: Optional[float] = None
    possession_rank: Optional[int] = None
    shots_on_target_per_match: Optional[float] = None
    shots_on_target_rank: Optional[int] = None
    big_chances: Optional[float] = None
    big_chances_rank: Optional[int] = None
    touches_in_opp_box: Optional[float] = None
    touches_in_opp_box_rank: Optional[int] = None
    corners: Optional[float] = None
    corners_rank: Optional[int] = None
    clean_sheets: Optional[float] = None
    clean_sheets_rank: Optional[int] = None


class AdvancedStatsData(BaseModel):
    team1_name: str
    team2_name: str
    team1_metrics: TeamAdvancedMetrics = Field(default_factory=TeamAdvancedMetrics)
    team2_metrics: TeamAdvancedMetrics = Field(default_factory=TeamAdvancedMetrics)
    scrape_success: bool = True
    error_message: Optional[str] = None


class MarketSelectionSignal(BaseModel):
    avg_odds: Optional[float] = None
    min_odds: Optional[float] = None
    max_odds: Optional[float] = None
    implied_prob: Optional[float] = None
    bookmaker_count: int = 0
    movement_score: int = 0


class MarketSignalsData(BaseModel):
    team1_name: str
    team2_name: str
    one_x_two: dict[str, MarketSelectionSignal] = Field(default_factory=dict)
    over_under_2_5: dict[str, MarketSelectionSignal] = Field(default_factory=dict)
    btts: dict[str, MarketSelectionSignal] = Field(default_factory=dict)
    asian_handicap_line: Optional[float] = None
    asian_handicap: dict[str, MarketSelectionSignal] = Field(default_factory=dict)
    favourite_label: Optional[str] = None
    favourite_gap_pct: Optional[float] = None
    market_warnings: list[str] = Field(default_factory=list)
    scrape_success: bool = True
    error_message: Optional[str] = None


class TableEntrySummary(BaseModel):
    position: Optional[int] = None
    played: Optional[int] = None
    points: Optional[int] = None
    goal_difference: Optional[int] = None
    scores_str: Optional[str] = None
    qualification_label: Optional[str] = None
    qualification_color: Optional[str] = None


class StandingsContextData(BaseModel):
    team1_name: str
    team2_name: str
    league_name: Optional[str] = None
    team1_overall: TableEntrySummary = Field(default_factory=TableEntrySummary)
    team2_overall: TableEntrySummary = Field(default_factory=TableEntrySummary)
    team1_home: TableEntrySummary = Field(default_factory=TableEntrySummary)
    team1_away: TableEntrySummary = Field(default_factory=TableEntrySummary)
    team2_home: TableEntrySummary = Field(default_factory=TableEntrySummary)
    team2_away: TableEntrySummary = Field(default_factory=TableEntrySummary)
    point_gap: Optional[int] = None
    position_gap: Optional[int] = None
    team1_above_team2: Optional[bool] = None
    scrape_success: bool = True
    error_message: Optional[str] = None


class MatchContextData(BaseModel):
    team1_name: str
    team2_name: str
    competition_name: Optional[str] = None
    kickoff_utc: Optional[str] = None
    venue_name: Optional[str] = None
    referee_name: Optional[str] = None
    is_neutral_venue: Optional[bool] = None
    is_team1_home: Optional[bool] = None
    stage: Optional[str] = None
    is_knockout: Optional[bool] = None
    is_second_leg: Optional[bool] = None
    scrape_success: bool = True
    error_message: Optional[str] = None


class WeatherContextData(BaseModel):
    team1_name: str
    team2_name: str
    venue_name: Optional[str] = None
    city_name: Optional[str] = None
    country_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    kickoff_utc: Optional[str] = None
    forecast_time_utc: Optional[str] = None
    temperature_c: Optional[float] = None
    apparent_temperature_c: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_gusts_kmh: Optional[float] = None
    precipitation_mm: Optional[float] = None
    precipitation_probability_pct: Optional[float] = None
    weather_code: Optional[int] = None
    weather_label: Optional[str] = None
    weather_warnings: list[str] = Field(default_factory=list)
    scrape_success: bool = True
    error_message: Optional[str] = None


class TeamScheduleLoadSummary(BaseModel):
    rest_hours: Optional[float] = None
    matches_last_7d: int = 0
    matches_next_7d: int = 0
    last_match_utc: Optional[str] = None
    last_opponent: Optional[str] = None
    next_match_utc: Optional[str] = None
    next_opponent: Optional[str] = None
    short_rest: Optional[bool] = None
    next_short_turnaround: Optional[bool] = None


class ScheduleLoadData(BaseModel):
    team1_name: str
    team2_name: str
    team1: TeamScheduleLoadSummary = Field(default_factory=TeamScheduleLoadSummary)
    team2: TeamScheduleLoadSummary = Field(default_factory=TeamScheduleLoadSummary)
    rest_gap_hours: Optional[float] = None
    load_warnings: list[str] = Field(default_factory=list)
    scrape_success: bool = True
    error_message: Optional[str] = None


# ── X.com Social Intelligence ────────────────────────────────────────────────

class XSocialSignal(BaseModel):
    """A single intelligence signal extracted from X.com."""
    type: str  # injury_update, lineup_leak, transfer_rumor, morale, tactical, weather
    content: str
    confidence: str = "MEDIUM"  # HIGH, MEDIUM, LOW
    source_hint: Optional[str] = None  # e.g. "journalist", "club_official", "fan"
    recency: Optional[str] = None  # e.g. "2h ago", "today"


class XSocialData(BaseModel):
    """Live-search match intelligence (grok web/X) — qualitative overlay."""
    team1_name: str
    team2_name: str
    signals: list[XSocialSignal] = Field(default_factory=list)
    sentiment_team1: Optional[str] = None  # positive, neutral, negative
    sentiment_team2: Optional[str] = None
    key_narrative: Optional[str] = None  # 1-2 sentence dominant narrative
    # Structured absences distilled from the live dump, used to gap-fill the
    # squad scraper's key_absences so the numeric model (key_absence_diff +
    # _absence_impact) sees live injuries/suspensions the market may not have priced.
    team1_absences: list[Player] = Field(default_factory=list)
    team2_absences: list[Player] = Field(default_factory=list)
    scrape_success: bool = True
    error_message: Optional[str] = None


class SourceStatus(BaseModel):
    key: str
    label: str
    field_name: str
    scrape_success: bool
    error_message: Optional[str] = None
    provider: Optional[str] = None
    required: bool = False
    applicable: bool = True
    coverage_gap: bool = False
    duration_ms: Optional[int] = None
    exception_type: Optional[str] = None


# ── Aggregate match data ──────────────────────────────────────────────────────

class MatchData(BaseModel):
    """Passed to analyzer.py after all scrapers finish."""
    team1: str
    team2: str
    flashscore: Optional[FlashscoreData] = None
    sofascore: Optional[SofascoreData] = None
    transfermarkt: Optional[TransfermarktData] = None
    odds: Optional[OddsData] = None
    market_signals: Optional[MarketSignalsData] = None
    advanced_stats: Optional[AdvancedStatsData] = None
    standings_context: Optional[StandingsContextData] = None
    match_context: Optional[MatchContextData] = None
    weather_context: Optional[WeatherContextData] = None
    schedule_load: Optional[ScheduleLoadData] = None
    x_social: Optional[XSocialData] = None
    pinnacle_odds: Optional[PinnacleOddsSnapshot] = None
    source_statuses: list[SourceStatus] = Field(default_factory=list)

    def _legacy_source_statuses(self) -> list[SourceStatus]:
        mapping = [
            ("fotmob_form", "Form/H2H", "flashscore", self.flashscore),
            ("fotmob_squad", "Squads", "sofascore", self.sofascore),
            ("transfermarkt", "Values", "transfermarkt", self.transfermarkt),
            ("betexplorer", "Odds", "odds", self.odds),
            ("market_signals", "Market signals", "market_signals", self.market_signals),
            ("advanced_stats", "Advanced stats", "advanced_stats", self.advanced_stats),
            ("standings_context", "Standings", "standings_context", self.standings_context),
            ("match_context", "Match context", "match_context", self.match_context),
            ("weather_context", "Weather", "weather_context", self.weather_context),
            ("schedule_load", "Schedule/load", "schedule_load", self.schedule_load),
            ("x_social", "X Social", "x_social", self.x_social),
        ]
        statuses: list[SourceStatus] = []
        for key, label, field_name, data in mapping:
            statuses.append(SourceStatus(
                key=key,
                label=label,
                field_name=field_name,
                scrape_success=bool(data is not None and data.scrape_success),
                error_message=getattr(data, "error_message", None),
            ))
        return statuses

    @property
    def resolved_source_statuses(self) -> list[SourceStatus]:
        return self.source_statuses or self._legacy_source_statuses()

    @property
    def successful_source_count(self) -> int:
        return sum(1 for status in self.resolved_source_statuses if status.scrape_success)

    @property
    def total_source_count(self) -> int:
        return len(self.resolved_source_statuses)

    @property
    def successful_applicable_source_count(self) -> int:
        return sum(
            1
            for status in self.resolved_source_statuses
            if status.applicable and status.scrape_success
        )

    @property
    def applicable_source_count(self) -> int:
        return sum(1 for status in self.resolved_source_statuses if status.applicable)

    @property
    def unsupported_source_count(self) -> int:
        return sum(1 for status in self.resolved_source_statuses if status.coverage_gap)

    @property
    def unsupported_sources(self) -> list[str]:
        return [status.label for status in self.resolved_source_statuses if status.coverage_gap]

    @property
    def data_completeness(self) -> float:
        """0.0–1.0 fraction of all registered sources that succeeded."""
        total = self.total_source_count
        if total == 0:
            return 0.0
        return self.successful_source_count / total

    @property
    def applicable_data_completeness(self) -> float:
        total = self.applicable_source_count
        if total == 0:
            return 0.0
        return self.successful_applicable_source_count / total

    @property
    def failed_sources(self) -> list[str]:
        return [
            status.label
            for status in self.resolved_source_statuses
            if status.applicable and not status.scrape_success
        ]

    @property
    def failed_source_messages(self) -> list[str]:
        messages: list[str] = []
        for status in self.resolved_source_statuses:
            if status.scrape_success or not status.applicable:
                continue
            if status.error_message:
                messages.append(f"{status.label}: {status.error_message}")
            else:
                messages.append(f"{status.label}: data unavailable")
        return messages

    @property
    def unsupported_source_messages(self) -> list[str]:
        grouped: dict[str, list[str]] = {}
        for status in self.resolved_source_statuses:
            if not status.coverage_gap:
                continue
            provider = status.provider or "Provider"
            grouped.setdefault(provider, []).append(status.label)

        messages: list[str] = []
        for provider, labels in grouped.items():
            label_str = ", ".join(labels)
            messages.append(
                f"{provider} coverage unavailable for this match: {label_str}"
            )
        return messages


# ── Prediction output ─────────────────────────────────────────────────────────

class PredictionFeatureSnapshot(BaseModel):
    """Frozen numeric feature set captured at prediction time for offline evaluation."""
    prediction_id: Optional[int] = None
    created_at: Optional[str] = None

    team1: str
    team2: str
    competition_name: Optional[str] = None
    data_completeness: float
    successful_source_count: int = 0
    total_source_count: int = 0
    is_neutral_venue: Optional[bool] = None
    is_team1_home: Optional[bool] = None

    market_prob_w1: Optional[float] = None
    market_prob_draw: Optional[float] = None
    market_prob_w2: Optional[float] = None
    market_prob_over_2_5: Optional[float] = None
    market_prob_under_2_5: Optional[float] = None
    market_prob_btts_yes: Optional[float] = None
    market_prob_btts_no: Optional[float] = None

    llm_prob_w1: Optional[float] = None
    llm_prob_draw: Optional[float] = None
    llm_prob_w2: Optional[float] = None
    llm_prob_over_2_5: Optional[float] = None
    llm_prob_under_2_5: Optional[float] = None
    llm_prob_btts_yes: Optional[float] = None
    llm_prob_btts_no: Optional[float] = None

    model_prob_w1: Optional[float] = None
    model_prob_draw: Optional[float] = None
    model_prob_w2: Optional[float] = None
    model_prob_over_2_5: Optional[float] = None
    model_prob_under_2_5: Optional[float] = None
    model_prob_btts_yes: Optional[float] = None
    model_prob_btts_no: Optional[float] = None
    model_expected_goals: Optional[float] = None

    blend_alpha: float = 0.0
    top_edge_pct: Optional[float] = None
    llm_model_gap_pct: Optional[float] = None
    no_bet_triggered: bool = False

    form_points_last5_diff: Optional[float] = None
    form_goal_diff_last5_diff: Optional[float] = None
    form_scored_avg_diff: Optional[float] = None
    form_conceded_avg_diff: Optional[float] = None
    h2h_team1_win_rate: Optional[float] = None
    h2h_draw_rate: Optional[float] = None
    h2h_avg_goals: Optional[float] = None
    xg_diff: Optional[float] = None
    xga_edge: Optional[float] = None
    rating_diff: Optional[float] = None
    possession_diff: Optional[float] = None
    standings_point_gap: Optional[float] = None
    standings_position_gap: Optional[float] = None
    rest_gap_hours: Optional[float] = None
    team1_short_rest: Optional[bool] = None
    team2_short_rest: Optional[bool] = None
    market_value_ratio: Optional[float] = None
    key_absence_diff: Optional[int] = None
    shots_avg_diff: Optional[float] = None
    shots_on_target_avg_diff: Optional[float] = None
    corners_avg_diff: Optional[float] = None
    shots_balance_diff: Optional[float] = None
    shots_on_target_balance_diff: Optional[float] = None
    corners_balance_diff: Optional[float] = None

    # Home/Away goal splits (from standings or form)
    team1_home_scored_avg: Optional[float] = None   # team1 goals scored per home match
    team1_home_conceded_avg: Optional[float] = None  # team1 goals conceded per home match
    team1_away_scored_avg: Optional[float] = None
    team1_away_conceded_avg: Optional[float] = None
    team2_home_scored_avg: Optional[float] = None
    team2_home_conceded_avg: Optional[float] = None
    team2_away_scored_avg: Optional[float] = None
    team2_away_conceded_avg: Optional[float] = None

    # Elo rating features (pre-fetched from EloSystem)
    elo_team1: Optional[float] = None          # team1 overall Elo
    elo_team2: Optional[float] = None          # team2 overall Elo
    elo_home_rating: Optional[float] = None    # home team's home-split Elo
    elo_away_rating: Optional[float] = None    # away team's away-split Elo
    elo_prob_w1: Optional[float] = None        # Elo-derived P(W1) %
    elo_prob_draw: Optional[float] = None      # Elo-derived P(Draw) %
    elo_prob_w2: Optional[float] = None        # Elo-derived P(W2) %
    elo_xg_adjustment: Optional[float] = None  # xG shift from Elo diff (±goals)

    # Referee context
    referee_name: Optional[str] = None
    referee_goals_vs_avg: Optional[float] = None  # +0.3 means ref's matches have +0.3 goals vs avg
    referee_over_2_5_pct: Optional[float] = None   # % of ref's matches with >2.5 goals

    # Weather context
    weather_wind_kmh: Optional[float] = None
    weather_temperature_c: Optional[float] = None
    weather_precipitation_mm: Optional[float] = None

    # Line movement signals
    movement_score_w1: Optional[int] = None       # positive = sharp money on W1
    movement_score_over: Optional[int] = None      # positive = sharp money on Over
    favourite_gap_pct: Optional[float] = None      # market consensus strength


class ValueCandidateSnapshot(BaseModel):
    label: str
    family: str
    market_code: Optional[str] = None
    selection_code: Optional[str] = None
    probability: float
    market_probability: float
    edge: float
    odds: Optional[float] = None
    confidence_pct: Optional[float] = None


class PredictionReport(BaseModel):
    """Structured AI output — consumed by formatter.py."""
    team1: str
    team2: str

    # Match result
    prob_w1: float
    prob_draw: float
    prob_w2: float
    result_confidence: str

    # Total goals
    prob_over_2_5: float
    prob_under_2_5: float
    expected_goals: float
    goals_confidence: str
    prob_over_1_5: Optional[float] = None
    prob_under_1_5: Optional[float] = None
    prob_over_3_5: Optional[float] = None
    prob_under_3_5: Optional[float] = None

    # Asian handicap
    asian_handicap_rec: str
    asian_handicap_confidence: str

    # BTTS
    prob_btts_yes: float
    prob_btts_no: float
    btts_confidence: str
    prob_dnb_team1: Optional[float] = None
    prob_dnb_team2: Optional[float] = None
    prob_double_chance_1x: Optional[float] = None
    prob_double_chance_12: Optional[float] = None
    prob_double_chance_x2: Optional[float] = None

    # Key factors
    team1_strengths: list[str] = Field(default_factory=list)
    team1_weaknesses: list[str] = Field(default_factory=list)
    team2_strengths: list[str] = Field(default_factory=list)
    team2_weaknesses: list[str] = Field(default_factory=list)

    # Top pick
    top_recommendation: str
    top_recommendation_odds: Optional[float] = None
    top_confidence_pct: float
    risk_level: str
    top_pick_context: Optional[str] = None
    top_market_code: Optional[str] = None
    top_selection_code: Optional[str] = None
    stake_units: Optional[float] = None
    stake_label: Optional[str] = None
    stake_rationale: Optional[str] = None
    kelly_pct: Optional[float] = None
    bankroll_mode: Optional[str] = None
    recommendation_mode: Optional[str] = None
    policy_flags: list[str] = Field(default_factory=list)

    # Meta
    analysis_summary: str
    data_warnings: list[str] = Field(default_factory=list)

    # New: enriched output fields
    value_bets: list[str] = Field(default_factory=list)
    value_candidate_snapshot: list[ValueCandidateSnapshot] = Field(default_factory=list)
    market_ah_line: Optional[str] = None
    market_ah_odds: Optional[float] = None
    methodology_version: Optional[str] = None
    prompt_version: Optional[str] = None
    feature_snapshot: Optional[PredictionFeatureSnapshot] = None

    # CLV tracking (Closing Line Value)
    pinnacle_odds_snapshot: Optional["PinnacleOddsSnapshot"] = None
    clv_pct: Optional[float] = None  # populated after match starts


class PinnacleOddsSnapshot(BaseModel):
    """Pinnacle odds captured at prediction time + closing line for CLV calc."""
    captured_at: Optional[str] = None
    # 1X2
    pin_w1: Optional[float] = None
    pin_draw: Optional[float] = None
    pin_w2: Optional[float] = None
    # Over/Under 2.5
    pin_over_2_5: Optional[float] = None
    pin_under_2_5: Optional[float] = None
    # Closing odds (filled later by background job)
    close_w1: Optional[float] = None
    close_draw: Optional[float] = None
    close_w2: Optional[float] = None
    close_over_2_5: Optional[float] = None
    close_under_2_5: Optional[float] = None
    # CLV per market
    clv_1x2_pct: Optional[float] = None
    clv_ou_pct: Optional[float] = None


# ── Lesson categories (auto-learning) ────────────────────────────────────────

class LessonCategory(str, Enum):
    """Categories for lessons extracted from prediction errors/successes."""
    OVERCONFIDENCE = "OVERCONFIDENCE"
    UNDERDOG_MISS = "UNDERDOG_MISS"
    FORM_REVERSAL = "FORM_REVERSAL"
    H2H_IGNORED = "H2H_IGNORED"
    INJURY_IMPACT = "INJURY_IMPACT"
    MARKET_SIGNAL = "MARKET_SIGNAL"
    CORRECT_PREDICTION = "CORRECT_PREDICTION"
    TOTAL_GOALS_ERROR = "TOTAL_GOALS_ERROR"
    BTTS_ERROR = "BTTS_ERROR"


class Lesson(BaseModel):
    """A single learned insight from a past prediction error or success."""
    id: Optional[int] = None
    text: str
    category: LessonCategory
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    times_confirmed: int = 0
    created_at: Optional[str] = None
    source_match: Optional[str] = None
    source_prediction_id: Optional[int] = None


# ── Agent intermediate output models ─────────────────────────────────────────

class AnalystOutput(BaseModel):
    """Output from Agent 1 (Analyst / Opus) — PredictionReport fields + reasoning."""
    # Match result
    prob_w1: float
    prob_draw: float
    prob_w2: float
    result_confidence: str
    # Total goals
    prob_over_2_5: float
    prob_under_2_5: float
    expected_goals: float
    goals_confidence: str
    # Asian handicap
    asian_handicap_rec: str
    asian_handicap_confidence: str
    # BTTS
    prob_btts_yes: float
    prob_btts_no: float
    btts_confidence: str
    # Key factors
    team1_strengths: list[str] = Field(default_factory=list)
    team1_weaknesses: list[str] = Field(default_factory=list)
    team2_strengths: list[str] = Field(default_factory=list)
    team2_weaknesses: list[str] = Field(default_factory=list)
    # Top pick
    top_recommendation: str
    top_recommendation_odds: Optional[float] = None
    top_confidence_pct: float
    risk_level: str
    # Meta
    analysis_summary: str
    data_warnings: list[str] = Field(default_factory=list)
    # Extended: reasoning for Critic to review
    reasoning: str = ""
    key_assumptions: list[str] = Field(default_factory=list)
    lesson_applications: list[str] = Field(default_factory=list)


class CriticIssue(BaseModel):
    """A single issue found by the Critic agent."""
    field: str
    issue: str
    severity: str = "MEDIUM"  # HIGH / MEDIUM / LOW
    suggested_correction: Optional[str] = None


class CriticOutput(BaseModel):
    """Output from Agent 2 (Critic / Sonnet)."""
    issues_found: list[CriticIssue] = Field(default_factory=list)
    adjusted_probabilities: Optional[dict] = None
    lessons_properly_applied: bool = True
    bias_detected: Optional[str] = None
    overall_assessment: str = "APPROVE"  # APPROVE / REVISE / REJECT
    reasoning: str = ""


# ── Memory context models ────────────────────────────────────────────────────

class SimilarMatch(BaseModel):
    """A past match retrieved from vector memory."""
    match_description: str
    prediction_summary: str
    actual_result: Optional[str] = None
    actual_score: Optional[str] = None
    similarity_score: float = 0.0
    lesson_learned: Optional[str] = None


class MemoryContext(BaseModel):
    """Aggregated context from memory systems, passed to the agent chain."""
    similar_matches: list[SimilarMatch] = Field(default_factory=list)
    relevant_lessons: list[Lesson] = Field(default_factory=list)
    accuracy_stats: Optional[dict] = None
    team_memory: str = ""
    calibration: str = ""
