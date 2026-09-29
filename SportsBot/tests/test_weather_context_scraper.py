from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from scrapers.fotmob import ResolvedFotmobTeam
from scrapers.weather_context import WeatherContextScraper


def _resolved_team(name: str, team_id: int, data: dict) -> ResolvedFotmobTeam:
    return ResolvedFotmobTeam(
        requested_name=name,
        search_term=name,
        team_id=team_id,
        data=data,
    )


class WeatherContextScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_extracts_weather_context_and_warnings(self):
        fixture = {
            "status": {"finished": False, "utcTime": "2026-03-10T20:00:00Z"},
            "home": {"id": 1, "name": "Arsenal"},
            "away": {"id": 2, "name": "Chelsea"},
            "opponent": {"id": 2, "name": "Chelsea"},
            "venue": {"name": "Emirates Stadium"},
            "isNeutralGround": False,
        }
        data1 = {
            "details": {
                "name": "Arsenal",
                "sportsTeamJSONLD": {
                    "location": {
                        "name": "Emirates Stadium",
                        "address": {
                            "addressLocality": "London",
                            "addressCountry": "England",
                        },
                        "geo": {"latitude": "51.5549", "longitude": "-0.1084"},
                    }
                },
            },
            "fixtures": {"allFixtures": {"fixtures": [fixture]}},
        }
        data2 = {
            "details": {
                "name": "Chelsea",
                "sportsTeamJSONLD": {
                    "location": {
                        "name": "Stamford Bridge",
                        "address": {
                            "addressLocality": "London",
                            "addressCountry": "England",
                        },
                        "geo": {"latitude": "51.4817", "longitude": "-0.1910"},
                    }
                },
            },
            "fixtures": {"allFixtures": {"fixtures": []}},
        }
        forecast_payload = {
            "hourly": {
                "time": [
                    "2026-03-10T19:00:00",
                    "2026-03-10T20:00:00",
                    "2026-03-10T21:00:00",
                ],
                "temperature_2m": [5.0, 0.0, 4.0],
                "apparent_temperature": [3.5, -2.0, 2.5],
                "precipitation_probability": [30, 74, 25],
                "precipitation": [0.0, 2.2, 0.1],
                "weather_code": [3, 95, 3],
                "wind_speed_10m": [12.0, 34.0, 15.0],
                "wind_gusts_10m": [22.0, 51.0, 24.0],
            }
        }

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with (
            patch("scrapers.weather_context.resolve_fotmob_team", side_effect=fake_resolve),
            patch.object(
                WeatherContextScraper,
                "_fetch_meteo",
                new=AsyncMock(return_value=json.dumps(forecast_payload)),
            ),
        ):
            result = await WeatherContextScraper("Arsenal", "Chelsea").scrape()

        self.assertTrue(result.scrape_success)
        self.assertEqual(result.venue_name, "Emirates Stadium")
        self.assertEqual(result.city_name, "London")
        self.assertEqual(result.country_name, "England")
        self.assertAlmostEqual(result.latitude or 0.0, 51.5549, places=4)
        self.assertAlmostEqual(result.longitude or 0.0, -0.1084, places=4)
        self.assertEqual(result.forecast_time_utc, "2026-03-10T20:00:00")
        self.assertEqual(result.weather_label, "Thunderstorm")
        self.assertAlmostEqual(result.temperature_c or 0.0, 0.0, places=2)
        self.assertAlmostEqual(result.wind_speed_kmh or 0.0, 34.0, places=2)
        self.assertIn(
            "Windy conditions may distort crossing and long-ball accuracy",
            result.weather_warnings,
        )
        self.assertIn(
            "Strong gusts raise variance on set pieces and shots",
            result.weather_warnings,
        )
        self.assertIn(
            "Wet pitch risk can slow tempo and increase errors",
            result.weather_warnings,
        )
        self.assertIn(
            "Thunderstorm conditions can make pre-match pricing less reliable",
            result.weather_warnings,
        )

    async def test_scrape_returns_failed_model_when_neutral_venue_has_no_coordinates(self):
        fixture = {
            "status": {"finished": False, "utcTime": "2026-03-10T20:00:00Z"},
            "home": {"id": 1, "name": "Arsenal"},
            "away": {"id": 2, "name": "Chelsea"},
            "opponent": {"id": 2, "name": "Chelsea"},
            "venue": {"name": "Olympiastadion"},
            "isNeutralGround": True,
        }
        data1 = {
            "details": {
                "name": "Arsenal",
                "sportsTeamJSONLD": {
                    "location": {
                        "name": "Emirates Stadium",
                        "address": {"addressLocality": "London", "addressCountry": "England"},
                        "geo": {"latitude": "51.5549", "longitude": "-0.1084"},
                    }
                },
            },
            "fixtures": {"allFixtures": {"fixtures": [fixture]}},
        }
        data2 = {
            "details": {
                "name": "Chelsea",
                "sportsTeamJSONLD": {
                    "location": {
                        "name": "Stamford Bridge",
                        "address": {"addressLocality": "London", "addressCountry": "England"},
                        "geo": {"latitude": "51.4817", "longitude": "-0.1910"},
                    }
                },
            },
            "fixtures": {"allFixtures": {"fixtures": []}},
        }

        async def fake_resolve(_scraper, team_name, **kwargs):
            if team_name == "Arsenal":
                return _resolved_team("Arsenal", 1, data1), ["Arsenal"]
            return _resolved_team("Chelsea", 2, data2), ["Chelsea"]

        with patch("scrapers.weather_context.resolve_fotmob_team", side_effect=fake_resolve):
            result = await WeatherContextScraper("Arsenal", "Chelsea").scrape()

        self.assertFalse(result.scrape_success)
        self.assertEqual(result.venue_name, "Olympiastadion")
        self.assertIn("Venue coordinates unavailable for weather lookup", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
