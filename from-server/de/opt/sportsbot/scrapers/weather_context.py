"""Weather-context scraper for upcoming fixtures using Fotmob venue metadata and Open-Meteo."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import config
from models import WeatherContextData
from scrapers.network import build_client_session
from scrapers.base import BaseScraper, ScraperError
from scrapers.flashscore import get_search_aliases
from scrapers.fotmob import ResolvedFotmobTeam, resolve_fotmob_team
from scrapers.match_context import MatchContextScraper

logger = logging.getLogger(__name__)

_WEATHER_LABELS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Heavy rain showers",
    82: "Violent rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Severe thunderstorm with hail",
}
_SNOW_CODES = {71, 73, 75, 77, 85, 86}
_THUNDER_CODES = {95, 96, 99}


@dataclass(slots=True)
class _VenueProfile:
    venue_name: str | None
    city_name: str | None
    country_name: str | None
    latitude: float | None
    longitude: float | None


def _parse_iso_utc(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class WeatherContextScraper(BaseScraper):
    """Resolve home-stadium weather for the upcoming fixture kickoff window."""

    DOMAIN = "api.open-meteo.com"

    @staticmethod
    def _extract_venue_profile(team_data: dict) -> _VenueProfile:
        details = team_data.get("details") or {}
        sports_json = details.get("sportsTeamJSONLD") or {}
        location = sports_json.get("location") or {}
        address = location.get("address") or {}
        geo = location.get("geo") or {}
        return _VenueProfile(
            venue_name=location.get("name"),
            city_name=address.get("addressLocality"),
            country_name=address.get("addressCountry"),
            latitude=_float_or_none(geo.get("latitude")),
            longitude=_float_or_none(geo.get("longitude")),
        )

    @classmethod
    def _resolve_fixture_venue_profile(
        cls,
        fixture: dict,
        *,
        resolved_t1: ResolvedFotmobTeam,
        resolved_t2: ResolvedFotmobTeam,
    ) -> Optional[_VenueProfile]:
        venue_name = ((fixture.get("venue") or {}).get("name")) or ((fixture.get("venue") or {}).get("longName"))
        neutral = MatchContextScraper._extract_neutral_flag(fixture)
        profiles = {
            resolved_t1.team_id: cls._extract_venue_profile(resolved_t1.data),
            resolved_t2.team_id: cls._extract_venue_profile(resolved_t2.data),
        }

        if venue_name:
            normalized_venue_name = str(venue_name).strip().lower()
            for profile in profiles.values():
                if profile.venue_name and profile.venue_name.strip().lower() == normalized_venue_name:
                    return _VenueProfile(
                        venue_name=venue_name,
                        city_name=profile.city_name,
                        country_name=profile.country_name,
                        latitude=profile.latitude,
                        longitude=profile.longitude,
                    )

        if neutral:
            return None

        home_id = (fixture.get("home") or {}).get("id")
        profile = profiles.get(home_id)
        if profile is None:
            return None
        return _VenueProfile(
            venue_name=venue_name or profile.venue_name,
            city_name=profile.city_name,
            country_name=profile.country_name,
            latitude=profile.latitude,
            longitude=profile.longitude,
        )

    @staticmethod
    def _select_hour_index(payload: dict, kickoff_dt: datetime) -> Optional[int]:
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not isinstance(times, list) or not times:
            return None

        best_index = None
        best_delta_seconds = None
        for idx, raw_time in enumerate(times):
            hour_dt = _parse_iso_utc(raw_time)
            if hour_dt is None:
                continue
            delta_seconds = abs((hour_dt - kickoff_dt).total_seconds())
            if best_delta_seconds is None or delta_seconds < best_delta_seconds:
                best_index = idx
                best_delta_seconds = delta_seconds

        if best_index is None or best_delta_seconds is None or best_delta_seconds > 3 * 3600:
            return None
        return best_index

    @staticmethod
    def _hourly_value(payload: dict, field_name: str, index: int) -> Optional[float]:
        values = ((payload.get("hourly") or {}).get(field_name)) or []
        if not isinstance(values, list) or index >= len(values):
            return None
        return _float_or_none(values[index])

    @staticmethod
    def _weather_warnings(
        *,
        temperature_c: Optional[float],
        wind_speed_kmh: Optional[float],
        wind_gusts_kmh: Optional[float],
        precipitation_mm: Optional[float],
        precipitation_probability_pct: Optional[float],
        weather_code: Optional[int],
    ) -> list[str]:
        warnings: list[str] = []
        if wind_speed_kmh is not None and wind_speed_kmh >= config.WEATHER_HIGH_WIND_KMH:
            warnings.append("Windy conditions may distort crossing and long-ball accuracy")
        if wind_gusts_kmh is not None and wind_gusts_kmh >= config.WEATHER_HIGH_GUST_KMH:
            warnings.append("Strong gusts raise variance on set pieces and shots")
        if precipitation_mm is not None and precipitation_mm >= config.WEATHER_WET_PITCH_MM:
            warnings.append("Wet pitch risk can slow tempo and increase errors")
        if (
            precipitation_probability_pct is not None
            and precipitation_probability_pct >= config.WEATHER_WET_PROBABILITY_PCT
        ):
            warnings.append("High rain probability can reduce ball speed control")
        if temperature_c is not None and temperature_c <= config.WEATHER_FREEZING_C:
            warnings.append("Cold conditions can suppress tempo and first-touch quality")
        if temperature_c is not None and temperature_c >= config.WEATHER_HEAT_C:
            warnings.append("Heat can reduce pressing intensity late in the match")
        if weather_code in _SNOW_CODES:
            warnings.append("Snow risk adds randomness and can suppress chance quality")
        if weather_code in _THUNDER_CODES:
            warnings.append("Thunderstorm conditions can make pre-match pricing less reliable")
        return warnings

    async def _fetch_meteo(self, latitude: float, longitude: float, forecast_days: int) -> str:
        """Fetch Open-Meteo forecast using a dedicated session.

        A separate lightweight aiohttp session avoids contention with the
        shared scraper pool that downloads heavy Fotmob HTML pages in parallel.
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join([
                "temperature_2m", "apparent_temperature",
                "precipitation_probability", "precipitation",
                "weather_code", "wind_speed_10m", "wind_gusts_10m",
            ]),
            "forecast_days": forecast_days,
            "timezone": "GMT",
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }
        last_exc: Exception | None = None
        session = build_client_session(timeout_total=15, ssl=False)
        try:
            for _attempt in range(3):
                try:
                    async with session.get(
                        config.OPEN_METEO_FORECAST_URL, params=params,
                    ) as resp:
                        return await resp.text()
                except Exception as exc:
                    last_exc = exc
                    await asyncio.sleep(1)
            raise last_exc or ScraperError("Open-Meteo request failed")
        finally:
            await session.close()

    async def scrape(self) -> WeatherContextData:
        try:
            (resolved_t1, searched_t1), (resolved_t2, searched_t2) = await asyncio.gather(
                resolve_fotmob_team(
                    self,
                    self.team1,
                    search_aliases=get_search_aliases(self.team1),
                    log=logger,
                ),
                resolve_fotmob_team(
                    self,
                    self.team2,
                    search_aliases=get_search_aliases(self.team2),
                    log=logger,
                ),
            )

            if not resolved_t1 or not resolved_t2:
                missing = []
                if not resolved_t1:
                    missing.append(f"{self.team1} (searched: {', '.join(searched_t1)})")
                if not resolved_t2:
                    missing.append(f"{self.team2} (searched: {', '.join(searched_t2)})")
                return WeatherContextData(
                    team1_name=self.team1,
                    team2_name=self.team2,
                    scrape_success=False,
                    error_message=f"Teams not found on Fotmob: {', '.join(missing)}",
                )

            team1_name = (resolved_t1.data.get("details") or {}).get("name", self.team1)
            team2_name = (resolved_t2.data.get("details") or {}).get("name", self.team2)
            fixture = MatchContextScraper._select_fixture(
                resolved_t1.data,
                team1_id=resolved_t1.team_id,
                team2_id=resolved_t2.team_id,
                team1_names=[self.team1, team1_name],
                team2_names=[self.team2, team2_name],
            )
            if not fixture:
                return WeatherContextData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    scrape_success=False,
                    error_message="Upcoming fixture not found on Fotmob",
                )

            kickoff_utc = ((fixture.get("status") or {}).get("utcTime")) or ""
            kickoff_dt = _parse_iso_utc(kickoff_utc)
            if kickoff_dt is None:
                return WeatherContextData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    scrape_success=False,
                    error_message="Kickoff time missing for weather lookup",
                )

            venue_profile = self._resolve_fixture_venue_profile(
                fixture,
                resolved_t1=resolved_t1,
                resolved_t2=resolved_t2,
            )
            if (
                venue_profile is None
                or venue_profile.latitude is None
                or venue_profile.longitude is None
            ):
                return WeatherContextData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    venue_name=((fixture.get("venue") or {}).get("name")) or ((fixture.get("venue") or {}).get("longName")),
                    kickoff_utc=kickoff_utc or None,
                    scrape_success=False,
                    error_message="Venue coordinates unavailable for weather lookup",
                )

            # Calculate minimal forecast window: days until kickoff + 1 buffer,
            # clamped to [1, 16].  forecast_days=16 causes Open-Meteo to time out
            # on the free tier; requesting only the days we need is 50x faster.
            days_until = max(1, int((kickoff_dt - datetime.now(timezone.utc)).total_seconds() / 86400) + 2)
            forecast_days = min(days_until, 16)

            payload = json.loads(
                await self._fetch_meteo(
                    venue_profile.latitude,
                    venue_profile.longitude,
                    forecast_days,
                )
            )
            hour_index = self._select_hour_index(payload, kickoff_dt)
            if hour_index is None:
                return WeatherContextData(
                    team1_name=team1_name,
                    team2_name=team2_name,
                    venue_name=venue_profile.venue_name,
                    city_name=venue_profile.city_name,
                    country_name=venue_profile.country_name,
                    latitude=venue_profile.latitude,
                    longitude=venue_profile.longitude,
                    kickoff_utc=kickoff_utc,
                    scrape_success=False,
                    error_message="Forecast hour missing for kickoff window",
                )

            hourly_times = ((payload.get("hourly") or {}).get("time")) or []
            weather_code = self._hourly_value(payload, "weather_code", hour_index)
            weather_code_int = int(weather_code) if weather_code is not None else None
            temperature_c = self._hourly_value(payload, "temperature_2m", hour_index)
            apparent_temperature_c = self._hourly_value(payload, "apparent_temperature", hour_index)
            precipitation_mm = self._hourly_value(payload, "precipitation", hour_index)
            precipitation_probability_pct = self._hourly_value(payload, "precipitation_probability", hour_index)
            wind_speed_kmh = self._hourly_value(payload, "wind_speed_10m", hour_index)
            wind_gusts_kmh = self._hourly_value(payload, "wind_gusts_10m", hour_index)

            return WeatherContextData(
                team1_name=team1_name,
                team2_name=team2_name,
                venue_name=venue_profile.venue_name,
                city_name=venue_profile.city_name,
                country_name=venue_profile.country_name,
                latitude=venue_profile.latitude,
                longitude=venue_profile.longitude,
                kickoff_utc=kickoff_utc,
                forecast_time_utc=hourly_times[hour_index] if hour_index < len(hourly_times) else None,
                temperature_c=temperature_c,
                apparent_temperature_c=apparent_temperature_c,
                wind_speed_kmh=wind_speed_kmh,
                wind_gusts_kmh=wind_gusts_kmh,
                precipitation_mm=precipitation_mm,
                precipitation_probability_pct=precipitation_probability_pct,
                weather_code=weather_code_int,
                weather_label=_WEATHER_LABELS.get(weather_code_int, "Unknown weather"),
                weather_warnings=self._weather_warnings(
                    temperature_c=temperature_c,
                    wind_speed_kmh=wind_speed_kmh,
                    wind_gusts_kmh=wind_gusts_kmh,
                    precipitation_mm=precipitation_mm,
                    precipitation_probability_pct=precipitation_probability_pct,
                    weather_code=weather_code_int,
                ),
                scrape_success=True,
            )
        except Exception as exc:
            logger.error("Weather context scraper critical failure: %s", exc, exc_info=True)
            return WeatherContextData(
                team1_name=self.team1,
                team2_name=self.team2,
                scrape_success=False,
                error_message=str(exc),
            )
        finally:
            await self._close()
