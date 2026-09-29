"""Registry for pluggable scraper sources."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from models import SourceStatus
from scrapers.advanced_stats import AdvancedStatsScraper
from scrapers.flashscore import FlashscoreScraper
from scrapers.match_context import MatchContextScraper
from scrapers.market_signals import MarketSignalsScraper
from scrapers.oddsportal import OddsportalScraper
from scrapers.schedule_load import ScheduleLoadScraper
from scrapers.sofascore import SofascoreScraper
from scrapers.standings_context import StandingsContextScraper
from scrapers.transfermarkt import TransfermarktScraper
from scrapers.weather_context import WeatherContextScraper
from scrapers.x_social import XSocialScraper


@dataclass(frozen=True, slots=True)
class ScraperSpec:
    key: str
    label: str
    field_name: str
    scraper_cls: type
    provider: str
    icon: str
    required: bool = False

    @property
    def progress_label(self) -> str:
        return f"{self.icon} {self.label}"


SCRAPER_REGISTRY: tuple[ScraperSpec, ...] = (
    ScraperSpec(
        key="fotmob_form",
        label="Form/H2H",
        field_name="flashscore",
        scraper_cls=FlashscoreScraper,
        provider="Fotmob",
        icon="📊",
    ),
    ScraperSpec(
        key="fotmob_squad",
        label="Squads",
        field_name="sofascore",
        scraper_cls=SofascoreScraper,
        provider="Fotmob",
        icon="👥",
    ),
    ScraperSpec(
        key="transfermarkt",
        label="Values",
        field_name="transfermarkt",
        scraper_cls=TransfermarktScraper,
        provider="Transfermarkt",
        icon="💰",
    ),
    ScraperSpec(
        key="betexplorer",
        label="Odds",
        field_name="odds",
        scraper_cls=OddsportalScraper,
        provider="Betexplorer",
        icon="📉",
    ),
    ScraperSpec(
        key="market_signals",
        label="Market signals",
        field_name="market_signals",
        scraper_cls=MarketSignalsScraper,
        provider="Betexplorer",
        icon="📡",
    ),
    ScraperSpec(
        key="advanced_stats",
        label="Advanced stats",
        field_name="advanced_stats",
        scraper_cls=AdvancedStatsScraper,
        provider="Fotmob",
        icon="📐",
    ),
    ScraperSpec(
        key="standings_context",
        label="Standings",
        field_name="standings_context",
        scraper_cls=StandingsContextScraper,
        provider="Fotmob",
        icon="🏁",
    ),
    ScraperSpec(
        key="match_context",
        label="Match context",
        field_name="match_context",
        scraper_cls=MatchContextScraper,
        provider="Fotmob",
        icon="🗺️",
    ),
    ScraperSpec(
        key="weather_context",
        label="Weather",
        field_name="weather_context",
        scraper_cls=WeatherContextScraper,
        provider="Open-Meteo",
        icon="Wx",
    ),
    ScraperSpec(
        key="schedule_load",
        label="Schedule/load",
        field_name="schedule_load",
        scraper_cls=ScheduleLoadScraper,
        provider="Fotmob",
        icon="⏱️",
    ),
    ScraperSpec(
        key="x_social",
        label="Live Search",
        field_name="x_social",
        scraper_cls=XSocialScraper,
        provider="LiveSearch",
        icon="🔎",
    ),
)


def build_source_status(
    spec: ScraperSpec,
    result: Any,
    *,
    error_message: str | None = None,
    duration_ms: int | None = None,
    exception_type: str | None = None,
) -> SourceStatus:
    resolved_error = error_message if error_message is not None else getattr(result, "error_message", None)
    coverage_gap = _is_provider_coverage_gap(spec, resolved_error)
    return SourceStatus(
        key=spec.key,
        label=spec.label,
        field_name=spec.field_name,
        scrape_success=bool(result is not None and getattr(result, "scrape_success", False)),
        error_message=resolved_error,
        provider=spec.provider,
        required=spec.required,
        applicable=not coverage_gap,
        coverage_gap=coverage_gap,
        duration_ms=duration_ms,
        exception_type=exception_type,
    )


def _is_provider_coverage_gap(spec: ScraperSpec, error_message: str | None) -> bool:
    if not error_message:
        return False
    lowered = error_message.lower()
    if spec.provider == "Fotmob" and "teams not found on fotmob" in lowered:
        return True
    return False


def get_scraper_progress_labels() -> tuple[tuple[str, str], ...]:
    return tuple((spec.key, spec.progress_label) for spec in SCRAPER_REGISTRY)


def get_scraper_progress_label(source_key: str) -> str:
    for spec in SCRAPER_REGISTRY:
        if spec.key == source_key:
            return spec.progress_label
    return source_key


def get_loading_source_lines() -> tuple[str, ...]:
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for spec in SCRAPER_REGISTRY:
        grouped.setdefault(spec.provider, []).append(spec.label)
    return tuple(
        f"  • {provider} — {', '.join(labels)}"
        for provider, labels in grouped.items()
    )
