import os
from pathlib import Path
from dotenv import load_dotenv

# Explicit path to .env — works from any working directory
_ENV_PATH = Path(__file__).parent / ".env"
if os.getenv("SPORTSBOT_SKIP_DOTENV", "false").lower() not in {"1", "true", "yes"}:
    load_dotenv(dotenv_path=_ENV_PATH, override=True)

# ── HTTP ──────────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 20          # seconds per request
MAX_RETRIES = 3
SEMAPHORE_LIMIT = 8           # max concurrent HTTP connections

DOMAIN_CONCURRENCY_LIMITS: dict[str, int] = {
    "fotmob.com": 4,
    "transfermarkt.com": 2,
    "betexplorer.com": 3,
    "espn.com": 4,
    "api.open-meteo.com": 4,
}

FOTMOB_TEAM_CACHE_TTL_SECONDS = 1800
BETEXPLORER_INDEX_CACHE_TTL_SECONDS = 900
SCRAPER_SLOW_LOG_MS = 8000
SCRAPER_DEBUG_SNAPSHOTS: bool = os.getenv("SCRAPER_DEBUG_SNAPSHOTS", "false").lower() == "true"
SCRAPER_DEBUG_SNAPSHOTS_DIR: str = os.getenv(
    "SCRAPER_DEBUG_SNAPSHOTS_DIR",
    str(Path(__file__).parent / "debug_snapshots"),
)
SCRAPER_DEBUG_SNAPSHOT_MAX_BYTES: int = int(
    os.getenv("SCRAPER_DEBUG_SNAPSHOT_MAX_BYTES", "20000")
)

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = 1800      # 30 minutes
CACHE_MAXSIZE = 128

# ── AI model config ───────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 8192

# ── Rate limiting (per-domain polite delay in seconds) ────────────────────────
DOMAIN_DELAYS: dict[str, float] = {
    "flashscore.com": 1.2,
    "sofascore.com": 0.8,
    "transfermarkt.com": 1.5,
    "oddsportal.com": 0.8,
    # New sources (replacing blocked ones)
    "fotmob.com": 0.5,       # replaces flashscore + sofascore
    "betexplorer.com": 0.8,  # replaces oddsportal
    "espn.com": 0.3,
    "api.open-meteo.com": 0.2,
}

# ── Poisson Model Parameters (v4 backtest optimal on 5832 matches) ───────────
POISSON_HOME_ADVANTAGE: float = float(os.getenv("POISSON_HOME_ADVANTAGE", "1.10"))
POISSON_ATTACK_WEIGHT: float = float(os.getenv("POISSON_ATTACK_WEIGHT", "0.60"))
POISSON_ROLLING_WINDOW: int = int(os.getenv("POISSON_ROLLING_WINDOW", "20"))
POISSON_DECAY: float = float(os.getenv("POISSON_DECAY", "0.03"))
DIXON_COLES_RHO: float = float(os.getenv("DIXON_COLES_RHO", "-0.10"))
DRAW_INFLATION: float = float(os.getenv("DRAW_INFLATION", "0.0"))
# SOT-to-xG conversion (shots on target × conversion ≈ xG proxy)
SOT_WEIGHT: float = float(os.getenv("SOT_WEIGHT", "0.40"))
SOT_TO_XG_CONVERSION: float = float(os.getenv("SOT_TO_XG_CONVERSION", "0.30"))
# How much to trust Fotmob real xG vs goal-based estimate (0=goals only, 1=xG only)
XG_BLEND_WEIGHT: float = float(os.getenv("XG_BLEND_WEIGHT", "0.50"))

# ── Dixon-Coles league-specific rho overrides (from v4 backtest) ─────────────
LEAGUE_RHO_OVERRIDES: dict[str, float] = {
    "bundesliga": -0.22,
    "serie a": -0.17,
    "la liga": -0.13,
    "primeira liga": -0.13,
    "eredivisie": -0.10,
    "premier league": -0.05,
    "ligue 1": -0.05,
    "championship": -0.05,
}

# ── O/U Market Focus (strongest edge per backtest: 57.3% accuracy) ───────────
OU_MARKET_PRIORITY: bool = os.getenv("OU_MARKET_PRIORITY", "true").lower() == "true"

# ── CLV Tracking (Closing Line Value) ────────────────────────────────────────
CLV_TRACKING_ENABLED: bool = os.getenv("CLV_TRACKING_ENABLED", "true").lower() == "true"
ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
ODDS_API_ENABLED: bool = bool(os.getenv("ODDS_API_KEY", ""))

OPEN_METEO_FORECAST_URL: str = os.getenv(
    "OPEN_METEO_FORECAST_URL",
    "https://api.open-meteo.com/v1/forecast",
)
WEATHER_HIGH_WIND_KMH: float = float(os.getenv("WEATHER_HIGH_WIND_KMH", "30"))
WEATHER_HIGH_GUST_KMH: float = float(os.getenv("WEATHER_HIGH_GUST_KMH", "45"))
WEATHER_WET_PITCH_MM: float = float(os.getenv("WEATHER_WET_PITCH_MM", "1.5"))
WEATHER_WET_PROBABILITY_PCT: float = float(os.getenv("WEATHER_WET_PROBABILITY_PCT", "60"))
WEATHER_FREEZING_C: float = float(os.getenv("WEATHER_FREEZING_C", "1"))
WEATHER_HEAT_C: float = float(os.getenv("WEATHER_HEAT_C", "30"))

# ── Per-user Telegram rate limit ──────────────────────────────────────────────
USER_RATE_LIMIT_SECONDS = 30

# ── API Keys (loaded from .env) ───────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_URL: str = os.getenv(
    "GEMINI_API_URL",
    "https://generativelanguage.googleapis.com/v1beta",
)

# ── OpenClaw Gateway (alternative to direct Anthropic API) ────────────────────
# If USE_OPENCLAW=true, the bot uses the local OpenClaw gateway instead of the
# direct Anthropic API, enabling google-antigravity/claude-opus-4-5-thinking
# via Google OAuth subscription.
USE_OPENCLAW: bool = os.getenv("USE_OPENCLAW", "false").lower() == "true"
OPENCLAW_GATEWAY_URL: str = os.getenv("OPENCLAW_GATEWAY_URL", "http://localhost:18789/v1")
OPENCLAW_GATEWAY_TOKEN: str = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
OPENCLAW_MODEL: str = "google-antigravity/claude-opus-4-5-thinking"
# ── User-Agent pool ───────────────────────────────────────────────────────────
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


# ── Multi-Agent AI Config ─────────────────────────────────────────────────────
# Default direct-SDK triad:
#   Analyst    -> Claude Opus 4.6
#   Critic     -> GPT-5.4 (high reasoning)
#   Synthesis  -> Gemini 3.1 Pro
SONNET_MODEL: str = os.getenv("SONNET_MODEL", "claude-sonnet-4-6")
OPUS_MODEL: str = os.getenv("OPUS_MODEL", "claude-opus-4-6")
GPT_MODEL: str = os.getenv("GPT_MODEL", "gpt-5.4")
GPT_REASONING_EFFORT: str = os.getenv("GPT_REASONING_EFFORT", "high").strip().lower()
if GPT_REASONING_EFFORT not in {"minimal", "low", "medium", "high"}:
    GPT_REASONING_EFFORT = "high"
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")

# ── OmniRoute gateway (cookie-based multi-AI — zero API cost, adds Grok) ──────
# When USE_OMNIROUTE=true the agent triad routes through OmniRoute workers
# (claude-code / gpt-cursor / gemini-cli / grok) instead of paid Anthropic/OpenAI/Gemini.
USE_OMNIROUTE: bool = os.getenv("USE_OMNIROUTE", "false").lower() == "true"
OMNIROUTE_URL: str = os.getenv("OMNIROUTE_URL", "https://127.0.0.1")
OMNIROUTE_USER: str = os.getenv("OMNIROUTE_USER", "admin")
OMNIROUTE_PASS: str = os.getenv("OMNIROUTE_PASS", "")
OMNIROUTE_ANALYST_WORKER: str = os.getenv("OMNIROUTE_ANALYST_WORKER", "claude-code")
OMNIROUTE_CRITIC_WORKER: str = os.getenv("OMNIROUTE_CRITIC_WORKER", "gpt-cursor")
OMNIROUTE_SYNTHESIS_WORKER: str = os.getenv("OMNIROUTE_SYNTHESIS_WORKER", "gemini-cli")

# Per-role defaults: OmniRoute workers when enabled, else the paid-SDK triad.
if USE_OMNIROUTE:
    _DEF_ANALYST = ("omniroute", OMNIROUTE_ANALYST_WORKER)
    _DEF_ANALYST_FB = ("omniroute", "grok")
    _DEF_CRITIC = ("omniroute", OMNIROUTE_CRITIC_WORKER)
    _DEF_CRITIC_FB = ("omniroute", "claude-code")
    _DEF_SYNTH = ("omniroute", OMNIROUTE_SYNTHESIS_WORKER)
    _DEF_SYNTH_FB = ("omniroute", "claude-code")
else:
    _DEF_ANALYST = ("anthropic", OPUS_MODEL)
    _DEF_ANALYST_FB = ("anthropic", SONNET_MODEL)
    _DEF_CRITIC = ("openai", GPT_MODEL)
    _DEF_CRITIC_FB = ("anthropic", SONNET_MODEL)
    _DEF_SYNTH = ("gemini", GEMINI_MODEL)
    _DEF_SYNTH_FB = ("anthropic", OPUS_MODEL)

AGENT_ANALYST_PROVIDER: str = os.getenv("AGENT_ANALYST_PROVIDER", _DEF_ANALYST[0]).strip().lower()
AGENT_ANALYST_MODEL: str = os.getenv("AGENT_ANALYST_MODEL", _DEF_ANALYST[1])
AGENT_ANALYST_FALLBACK_PROVIDER: str = os.getenv(
    "AGENT_ANALYST_FALLBACK_PROVIDER", _DEF_ANALYST_FB[0]
).strip().lower()
AGENT_ANALYST_FALLBACK_MODEL: str = os.getenv("AGENT_ANALYST_FALLBACK_MODEL", _DEF_ANALYST_FB[1])

AGENT_CRITIC_PROVIDER: str = os.getenv("AGENT_CRITIC_PROVIDER", _DEF_CRITIC[0]).strip().lower()
AGENT_CRITIC_MODEL: str = os.getenv("AGENT_CRITIC_MODEL", _DEF_CRITIC[1])
AGENT_CRITIC_REASONING_EFFORT: str = os.getenv(
    "AGENT_CRITIC_REASONING_EFFORT",
    GPT_REASONING_EFFORT,
).strip().lower()
if AGENT_CRITIC_REASONING_EFFORT not in {"minimal", "low", "medium", "high"}:
    AGENT_CRITIC_REASONING_EFFORT = "high"
AGENT_CRITIC_FALLBACK_PROVIDER: str = os.getenv(
    "AGENT_CRITIC_FALLBACK_PROVIDER", _DEF_CRITIC_FB[0]
).strip().lower()
AGENT_CRITIC_FALLBACK_MODEL: str = os.getenv("AGENT_CRITIC_FALLBACK_MODEL", _DEF_CRITIC_FB[1])

AGENT_SYNTHESIS_PROVIDER: str = os.getenv("AGENT_SYNTHESIS_PROVIDER", _DEF_SYNTH[0]).strip().lower()
AGENT_SYNTHESIS_MODEL: str = os.getenv("AGENT_SYNTHESIS_MODEL", _DEF_SYNTH[1])
AGENT_SYNTHESIS_FALLBACK_PROVIDER: str = os.getenv(
    "AGENT_SYNTHESIS_FALLBACK_PROVIDER", _DEF_SYNTH_FB[0]
).strip().lower()
AGENT_SYNTHESIS_FALLBACK_MODEL: str = os.getenv("AGENT_SYNTHESIS_FALLBACK_MODEL", _DEF_SYNTH_FB[1])
AGENT_CHAIN_ENABLED: bool = os.getenv("AGENT_CHAIN_ENABLED", "true").lower() == "true"
AGENT_MAX_TOKENS: int = 12288
NO_LIVE_AI_IN_TESTS: bool = os.getenv("NO_LIVE_AI_IN_TESTS", "true").lower() == "true"
RECOMMENDATION_MODE: str = os.getenv("RECOMMENDATION_MODE", "balanced").strip().lower()
if RECOMMENDATION_MODE not in {"strict", "balanced", "action"}:
    RECOMMENDATION_MODE = "balanced"
BANKROLL_MODE: str = os.getenv("BANKROLL_MODE", "balanced").strip().lower()
if BANKROLL_MODE not in {"conservative", "balanced", "aggressive"}:
    BANKROLL_MODE = "balanced"
BANKROLL_GUARDRAILS_ENABLED: bool = os.getenv("BANKROLL_GUARDRAILS_ENABLED", "true").lower() == "true"
BANKROLL_MAX_BETS_PER_DAY: int = int(os.getenv("BANKROLL_MAX_BETS_PER_DAY", "4"))
BANKROLL_DAILY_LIMIT_ENABLED: bool = os.getenv("BANKROLL_DAILY_LIMIT_ENABLED", "false").lower() == "true"
BANKROLL_MAX_STAKED_PER_DAY_UNITS: float = float(os.getenv("BANKROLL_MAX_STAKED_PER_DAY_UNITS", "2.5"))
BANKROLL_DAILY_STOP_LOSS_UNITS: float = float(os.getenv("BANKROLL_DAILY_STOP_LOSS_UNITS", "1.5"))
BANKROLL_LOSS_STREAK_COOLDOWN: int = int(os.getenv("BANKROLL_LOSS_STREAK_COOLDOWN", "3"))
BANKROLL_COOLDOWN_STAKE_CAP_UNITS: float = float(os.getenv("BANKROLL_COOLDOWN_STAKE_CAP_UNITS", "0.25"))
PRE_AI_GATE_ENABLED: bool = os.getenv("PRE_AI_GATE_ENABLED", "true").lower() == "true"
PRE_AI_GATE_MAX_APPLICABLE_COMPLETENESS: float = float(os.getenv("PRE_AI_GATE_MAX_APPLICABLE_COMPLETENESS", "0.56"))
PRE_AI_GATE_MAX_EDGE_PCT: float = float(os.getenv("PRE_AI_GATE_MAX_EDGE_PCT", "1.5"))
PRE_AI_GATE_MAX_CANDIDATE_CONFIDENCE: float = float(os.getenv("PRE_AI_GATE_MAX_CANDIDATE_CONFIDENCE", "58.0"))
PRE_AI_GATE_MAX_SUCCESSFUL_SOURCES: int = int(os.getenv("PRE_AI_GATE_MAX_SUCCESSFUL_SOURCES", "4"))
LIVE_1X2_HYBRID_ENABLED: bool = os.getenv("LIVE_1X2_HYBRID_ENABLED", "true").lower() == "true"
LIVE_1X2_HYBRID_ALPHA: float = float(os.getenv("LIVE_1X2_HYBRID_ALPHA", "0.25"))
LIVE_1X2_HYBRID_MIN_ROWS: int = int(os.getenv("LIVE_1X2_HYBRID_MIN_ROWS", "1500"))
LIVE_1X2_HYBRID_MIN_COMPLETENESS: float = float(os.getenv("LIVE_1X2_HYBRID_MIN_COMPLETENESS", "0.55"))
LIVE_1X2_HYBRID_CACHE_TTL_SECONDS: int = int(os.getenv("LIVE_1X2_HYBRID_CACHE_TTL_SECONDS", "3600"))
LIVE_1X2_HYBRID_TRAIN_LIMIT: int = int(os.getenv("LIVE_1X2_HYBRID_TRAIN_LIMIT", "4000"))

# ── LightGBM Ensemble Config ──────────────────────────────────────────────────
BANKROLL_KELLY_PROPORTIONAL: bool = os.getenv("BANKROLL_KELLY_PROPORTIONAL", "false").lower() == "true"
BANKROLL_STARTING_UNITS: float = float(os.getenv("BANKROLL_STARTING_UNITS", "100.0"))

ENSEMBLE_ENABLED: bool = os.getenv("ENSEMBLE_ENABLED", "true").lower() == "true"
ENSEMBLE_OU_WEIGHT: float = float(os.getenv("ENSEMBLE_OU_WEIGHT", "0.40"))
ENSEMBLE_1X2_WEIGHT: float = float(os.getenv("ENSEMBLE_1X2_WEIGHT", "0.30"))
ENSEMBLE_EDGE_THRESHOLD: float = float(os.getenv("ENSEMBLE_EDGE_THRESHOLD", "8.0"))
ENSEMBLE_KELLY_FRACTION: float = float(os.getenv("ENSEMBLE_KELLY_FRACTION", "0.25"))

# ── ChromaDB Config (local vector store) ─────────────────────────────────────
CHROMADB_PATH: str = str(Path(__file__).parent / "chromadb_data")
CHROMADB_COLLECTION_MATCHES: str = "match_history"
CHROMADB_COLLECTION_LESSONS: str = "lessons"
CHROMADB_COLLECTION_TEAM_PATTERNS: str = "team_patterns"
VECTOR_TOP_K: int = 5

# ── Pinecone Config (optional cloud vector store) ────────────────────────────
PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "sportsbot")
PINECONE_ENABLED: bool = bool(os.getenv("PINECONE_API_KEY", ""))


def validate():
    """Call at startup to fail fast if required env vars are missing or invalid."""
    import logging as _logging
    _log = _logging.getLogger(__name__)

    missing = []
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "your_bot_token_here":
        missing.append("TELEGRAM_TOKEN")
    # AI backend: OmniRoute (cookie gateway) > OpenClaw > direct Anthropic API.
    if USE_OMNIROUTE:
        if not OMNIROUTE_PASS:
            missing.append("OMNIROUTE_PASS")
    elif USE_OPENCLAW:
        if not OPENCLAW_GATEWAY_TOKEN or OPENCLAW_GATEWAY_TOKEN == "your_openclaw_token_here":
            missing.append("OPENCLAW_GATEWAY_TOKEN")
    else:
        if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "your_anthropic_key_here":
            missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Please set them in your .env file."
        )

    # ── Numeric range checks ─────────────────────────────────────────────────
    warnings: list[str] = []

    _RANGE_CHECKS: list[tuple[str, float, float, float]] = [
        ("REQUEST_TIMEOUT", REQUEST_TIMEOUT, 1, 120),
        ("MAX_RETRIES", MAX_RETRIES, 0, 10),
        ("SEMAPHORE_LIMIT", SEMAPHORE_LIMIT, 1, 50),
        ("CACHE_TTL_SECONDS", CACHE_TTL_SECONDS, 60, 7200),
        ("USER_RATE_LIMIT_SECONDS", USER_RATE_LIMIT_SECONDS, 0, 300),
        ("POISSON_HOME_ADVANTAGE", POISSON_HOME_ADVANTAGE, 0.8, 1.5),
        ("POISSON_ATTACK_WEIGHT", POISSON_ATTACK_WEIGHT, 0.0, 1.0),
        ("POISSON_DECAY", POISSON_DECAY, 0.0, 0.5),
        ("DIXON_COLES_RHO", DIXON_COLES_RHO, -0.5, 0.0),
        ("ENSEMBLE_KELLY_FRACTION", ENSEMBLE_KELLY_FRACTION, 0.0, 1.0),
        ("LIVE_1X2_HYBRID_ALPHA", LIVE_1X2_HYBRID_ALPHA, 0.0, 1.0),
        ("BANKROLL_MAX_BETS_PER_DAY", BANKROLL_MAX_BETS_PER_DAY, 1, 20),
    ]
    for name, value, lo, hi in _RANGE_CHECKS:
        if not (lo <= value <= hi):
            warnings.append(f"{name}={value} is outside expected range [{lo}, {hi}]")

    # ── Model name validation ────────────────────────────────────────────────
    _KNOWN_ANTHROPIC_MODELS = {
        "claude-sonnet-4-6", "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5-20250514",
        "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
    }
    _KNOWN_OPENAI_MODELS = {"gpt-5.4"}
    _KNOWN_GEMINI_MODELS = {"gemini-3.1-pro-preview"}
    _KNOWN_PROVIDERS = {"anthropic", "openai", "gemini", "omniroute"}
    for model_var, model_val in [
        ("CLAUDE_MODEL", CLAUDE_MODEL),
        ("SONNET_MODEL", SONNET_MODEL),
        ("OPUS_MODEL", OPUS_MODEL),
    ]:
        if model_val not in _KNOWN_ANTHROPIC_MODELS:
            warnings.append(f"{model_var}='{model_val}' is not a recognized Claude model")
    if GPT_MODEL not in _KNOWN_OPENAI_MODELS:
        warnings.append(f"GPT_MODEL='{GPT_MODEL}' is not a recognized OpenAI model")
    if GEMINI_MODEL not in _KNOWN_GEMINI_MODELS:
        warnings.append(f"GEMINI_MODEL='{GEMINI_MODEL}' is not a recognized Gemini model")

    for provider_var, provider_val in [
        ("AGENT_ANALYST_PROVIDER", AGENT_ANALYST_PROVIDER),
        ("AGENT_ANALYST_FALLBACK_PROVIDER", AGENT_ANALYST_FALLBACK_PROVIDER),
        ("AGENT_CRITIC_PROVIDER", AGENT_CRITIC_PROVIDER),
        ("AGENT_CRITIC_FALLBACK_PROVIDER", AGENT_CRITIC_FALLBACK_PROVIDER),
        ("AGENT_SYNTHESIS_PROVIDER", AGENT_SYNTHESIS_PROVIDER),
        ("AGENT_SYNTHESIS_FALLBACK_PROVIDER", AGENT_SYNTHESIS_FALLBACK_PROVIDER),
    ]:
        if provider_val not in _KNOWN_PROVIDERS:
            warnings.append(f"{provider_var}='{provider_val}' is not a supported provider")

    def _has_provider_key(provider: str) -> bool:
        if provider == "anthropic":
            return bool(ANTHROPIC_API_KEY)
        if provider == "openai":
            return bool(OPENAI_API_KEY)
        if provider == "gemini":
            return bool(GEMINI_API_KEY)
        if provider == "omniroute":
            return bool(OMNIROUTE_PASS)
        return False

    def _warn_missing_provider_key(role_name: str, provider: str, model: str) -> None:
        if provider in _KNOWN_PROVIDERS and not _has_provider_key(provider):
            warnings.append(
                f"{role_name} is configured for {provider}:{model}, but its API key is missing; "
                "runtime fallback will be used if configured"
            )

    if AGENT_CHAIN_ENABLED:
        _warn_missing_provider_key("AGENT_ANALYST", AGENT_ANALYST_PROVIDER, AGENT_ANALYST_MODEL)
        _warn_missing_provider_key(
            "AGENT_ANALYST fallback",
            AGENT_ANALYST_FALLBACK_PROVIDER,
            AGENT_ANALYST_FALLBACK_MODEL,
        )
        _warn_missing_provider_key("AGENT_CRITIC", AGENT_CRITIC_PROVIDER, AGENT_CRITIC_MODEL)
        _warn_missing_provider_key(
            "AGENT_CRITIC fallback",
            AGENT_CRITIC_FALLBACK_PROVIDER,
            AGENT_CRITIC_FALLBACK_MODEL,
        )
        _warn_missing_provider_key(
            "AGENT_SYNTHESIS",
            AGENT_SYNTHESIS_PROVIDER,
            AGENT_SYNTHESIS_MODEL,
        )
        _warn_missing_provider_key(
            "AGENT_SYNTHESIS fallback",
            AGENT_SYNTHESIS_FALLBACK_PROVIDER,
            AGENT_SYNTHESIS_FALLBACK_MODEL,
        )

    for w in warnings:
        _log.warning("Config warning: %s", w)
