from __future__ import annotations

import unittest

from team_parser import normalize_team_name, parse_teams_from_input


class TeamParserTests(unittest.TestCase):
    def test_normalize_strips_supported_club_prefixes_via_aliases(self) -> None:
        self.assertEqual(normalize_team_name("SS Lazio"), "SS Lazio")
        self.assertEqual(normalize_team_name("US Sassuolo"), "US Sassuolo")
        self.assertEqual(normalize_team_name("RCD Espanyol"), "RCD Espanyol")
        self.assertEqual(normalize_team_name("FC Porto"), "FC Porto")

    def test_english_vs_input(self) -> None:
        self.assertEqual(
            parse_teams_from_input("Arsenal vs Chelsea"),
            ("Arsenal", "Chelsea"),
        )

    def test_russian_dash_input(self) -> None:
        self.assertEqual(
            parse_teams_from_input("Реал Мадрид - Барселона"),
            ("Real Madrid", "Barcelona"),
        )

    def test_short_english_aliases(self) -> None:
        self.assertEqual(
            parse_teams_from_input("Man City v Liverpool"),
            ("Manchester City", "Liverpool"),
        )


if __name__ == "__main__":
    unittest.main()
