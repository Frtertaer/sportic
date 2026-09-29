from __future__ import annotations

import json
import unittest

from scrapers.fotmob import (
    FOTMOB_TEAM_HTML,
    build_fotmob_search_candidates,
    build_fotmob_search_url,
    clear_fotmob_caches,
    resolve_fotmob_team,
)


def _make_team_html(team_id: int, team_payload: dict) -> str:
    """Build a minimal HTML page with __NEXT_DATA__ matching real Fotmob."""
    import json as _json
    next_data = {"props": {"pageProps": {"fallback": {f"team-{team_id}": team_payload}}}}
    return (
        '<html><head></head><body>'
        f'<script id="__NEXT_DATA__" type="application/json">{_json.dumps(next_data)}</script>'
        '</body></html>'
    )


class _FakeScraper:
    def __init__(self, responses: dict[object, str]) -> None:
        self._responses = responses
        self.calls: list[object] = []

    async def _fetch_with_retry(self, url, *, params=None, headers=None, json_mode=False):
        if params is not None:
            key = (url, tuple(sorted(params.items())))
        else:
            key = url
        self.calls.append(key)
        if key not in self._responses:
            raise AssertionError(f"Unexpected request: {key}")
        return self._responses[key]


class FotmobHelperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_fotmob_caches()

    def test_build_search_url_uses_percent_encoding(self) -> None:
        url = build_fotmob_search_url("Real Madrid")
        self.assertIn("term=Real%20Madrid", url)
        self.assertNotIn("Real+Madrid", url)

    def test_bayern_alias_added_before_original_name(self) -> None:
        candidates = build_fotmob_search_candidates("Bayern Munich")
        # Must contain both the umlaut and original name; may also include
        # reverse-lookup aliases like "Bayern Muenchen" from team_parser
        self.assertIn("Bayern München", candidates)
        self.assertIn("Bayern Munich", candidates)
        # Aliases before original name
        self.assertLess(candidates.index("Bayern München"), candidates.index("Bayern Munich"))

    def test_club_prefix_alias_is_added_before_original_name(self) -> None:
        self.assertEqual(
            build_fotmob_search_candidates("SS Lazio"),
            ["Lazio", "SS Lazio"],
        )
        self.assertEqual(
            build_fotmob_search_candidates("US Sassuolo"),
            ["Sassuolo", "US Sassuolo"],
        )
        self.assertEqual(
            build_fotmob_search_candidates("RCD Espanyol"),
            ["Espanyol", "RCD Espanyol"],
        )

    async def test_resolver_falls_back_to_prefix_stripped_alias(self) -> None:
        alias_url = build_fotmob_search_url("Lazio")
        main_url = build_fotmob_search_url("SS Lazio")
        search_payload = {
            "hits": {
                "hits": [
                    {
                        "links": [
                            {
                                "href": "https://apigw.fotmob.com/searchapi/search?term=team_8543",
                            }
                        ]
                    }
                ]
            }
        }
        team_payload = {
            "details": {
                "name": "Lazio",
                "shortName": "Lazio",
            }
        }
        scraper = _FakeScraper(
            {
                alias_url: json.dumps(search_payload),
                main_url: json.dumps({"hits": {"hits": []}}),
                FOTMOB_TEAM_HTML.format(team_id=8543): _make_team_html(8543, team_payload),
            }
        )

        resolved, candidates = await resolve_fotmob_team(scraper, "SS Lazio")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 8543)
        self.assertEqual(candidates, ["Lazio", "SS Lazio"])

    async def test_resolver_supports_spanish_rcd_prefix(self) -> None:
        alias_url = build_fotmob_search_url("Espanyol")
        main_url = build_fotmob_search_url("RCD Espanyol")
        search_payload = {
            "hits": {
                "hits": [
                    {
                        "links": [
                            {
                                "href": "https://apigw.fotmob.com/searchapi/search?term=team_8558",
                            }
                        ]
                    }
                ]
            }
        }
        team_payload = {
            "details": {
                "name": "Espanyol",
                "shortName": "Espanyol",
            }
        }
        scraper = _FakeScraper(
            {
                alias_url: json.dumps(search_payload),
                main_url: json.dumps({"hits": {"hits": []}}),
                FOTMOB_TEAM_HTML.format(team_id=8558): _make_team_html(8558, team_payload),
            }
        )

        resolved, candidates = await resolve_fotmob_team(scraper, "RCD Espanyol")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 8558)
        self.assertEqual(candidates, ["Espanyol", "RCD Espanyol"])

    async def test_resolver_falls_back_to_next_search_candidate(self) -> None:
        alias_url = build_fotmob_search_url("Wrong Alias")
        main_url = build_fotmob_search_url("Arsenal")
        search_payload = {
            "hits": {
                "hits": [
                    {
                        "links": [
                            {
                                "href": "https://apigw.fotmob.com/searchapi/search?term=team_9825",
                            }
                        ]
                    }
                ]
            }
        }
        team_payload = {
            "details": {
                "name": "Arsenal",
                "shortName": "Arsenal",
            }
        }
        scraper = _FakeScraper(
            {
                alias_url: json.dumps({"hits": {"hits": []}}),
                main_url: json.dumps(search_payload),
                FOTMOB_TEAM_HTML.format(team_id=9825): _make_team_html(9825, team_payload),
            }
        )

        resolved, candidates = await resolve_fotmob_team(
            scraper,
            "Arsenal",
            search_aliases=["Wrong Alias"],
        )

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.team_id, 9825)
        self.assertEqual(candidates, ["Wrong Alias", "Arsenal"])

    async def test_resolver_uses_cache_for_repeat_lookup(self) -> None:
        main_url = build_fotmob_search_url("Arsenal")
        search_payload = {
            "hits": {
                "hits": [
                    {
                        "links": [
                            {
                                "href": "https://apigw.fotmob.com/searchapi/search?term=team_9825",
                            }
                        ]
                    }
                ]
            }
        }
        team_payload = {
            "details": {
                "name": "Arsenal",
                "shortName": "Arsenal",
            }
        }
        scraper = _FakeScraper(
            {
                main_url: json.dumps(search_payload),
                FOTMOB_TEAM_HTML.format(team_id=9825): _make_team_html(9825, team_payload),
            }
        )

        first_resolved, _ = await resolve_fotmob_team(scraper, "Arsenal")
        second_resolved, _ = await resolve_fotmob_team(scraper, "Arsenal")

        self.assertIsNotNone(first_resolved)
        self.assertIsNotNone(second_resolved)
        self.assertEqual(first_resolved.team_id, second_resolved.team_id)
        self.assertEqual(len(scraper.calls), 2)


class LeagueExtractionTest(unittest.TestCase):
    def test_league_name_extracted_from_team_form(self):
        """Simulate the teamForm structure Fotmob returns."""
        mock_team_data = {
            "overview": {
                "teamForm": [
                    {
                        "tournament": {"name": "Premier League"},
                        "resultString": "W",
                        "tooltipText": {
                            "homeTeam": "Arsenal", "awayTeam": "Chelsea",
                            "homeScore": 2, "awayScore": 1,
                        },
                        "home": {"id": 9825},
                        "date": {"utcTime": "2026-02-28T15:00:00Z"},
                    }
                ]
            }
        }
        from scrapers.flashscore import FlashscoreScraper
        league = FlashscoreScraper._extract_league_name(mock_team_data)
        self.assertEqual(league, "Premier League")

    def test_league_name_none_when_missing(self):
        from scrapers.flashscore import FlashscoreScraper
        self.assertIsNone(FlashscoreScraper._extract_league_name({}))

    def test_league_name_fallback_to_leagueName_key(self):
        """Test fallback when tournament dict is absent."""
        mock_data = {
            "overview": {
                "teamForm": [{"leagueName": "Bundesliga"}]
            }
        }
        from scrapers.flashscore import FlashscoreScraper
        self.assertEqual(FlashscoreScraper._extract_league_name(mock_data), "Bundesliga")

    def test_league_name_fallback_to_competition_key(self):
        """Test third fallback when tournament and leagueName are both absent."""
        mock_data = {
            "overview": {
                "teamForm": [{"competition": "Serie A"}]
            }
        }
        from scrapers.flashscore import FlashscoreScraper
        self.assertEqual(
            FlashscoreScraper._extract_league_name(mock_data), "Serie A"
        )

    def test_league_name_skips_empty_tournament_name(self):
        """Empty tournament.name string is falsy → should fall through to leagueName."""
        mock_data = {
            "overview": {
                "teamForm": [{"tournament": {"name": ""}, "leagueName": "Ligue 1"}]
            }
        }
        from scrapers.flashscore import FlashscoreScraper
        self.assertEqual(
            FlashscoreScraper._extract_league_name(mock_data), "Ligue 1"
        )


if __name__ == "__main__":
    unittest.main()
