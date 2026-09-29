from __future__ import annotations

import copy
import json
import time
import unittest
from pathlib import Path

import web_ui_v2
import web_ui_v3


ROOT = Path(__file__).resolve().parent


class V3RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / "sample_state.json").read_text(encoding="utf-8"))

    def state(self):
        value = copy.deepcopy(self.fixture)
        value["ts"] = int(time.time())
        return value

    def test_contract_and_counts(self):
        output = web_ui_v3.render(self.state())
        for anchor in ("id=main", "id=signals", "id=matches", "id=upcoming", "id=accuracy"):
            self.assertIn(anchor, output)
        self.assertEqual(output.count("signal-row tone-"), 4)
        self.assertEqual(output.count("class=match-row"), 2)
        self.assertEqual(output.count("class=board-row"), 5)

    def test_visual_language_has_no_dashboard_effects(self):
        head = web_ui_v3.CS2_HEAD_V3.lower()
        for token in ("radial-gradient", "backdrop-filter", "box-shadow"):
            self.assertNotIn(token, head)
        self.assertNotIn("border-radius:999", head)

    def test_provider_markup_and_xss_are_clean(self):
        state = self.state()
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        state["value"][0]["t1"] = payload
        state["prematch_board"][0]["news"] = "GREEN: " + payload
        output = web_ui_v3.render(state)
        self.assertNotIn("<script>", output)
        self.assertNotIn("<img src=x", output)
        self.assertNotIn("grok:", output.lower())
        self.assertIn("&lt;script&gt;", output)

    def test_football_is_unchanged(self):
        state = self.state()
        self.assertEqual(web_ui_v3.render_football(state), web_ui_v2.render_football(state))

    def test_input_is_not_mutated(self):
        state = self.state()
        before = copy.deepcopy(state)
        web_ui_v3.render(state)
        self.assertEqual(state, before)

    def test_invalid_state_degrades(self):
        output = web_ui_v3.render({"ts": float("nan"), "live": ["bad"], "steam": ["bad"]})
        self.assertIn("нужно обновление", output)
        self.assertIn("</html>", output)

    def test_refresh_and_single_landmarks(self):
        output = web_ui_v3.render(self.state())
        self.assertIn('<meta http-equiv="refresh" content="30">', output)
        self.assertEqual(output.count("<main "), 1)
        self.assertEqual(output.count("<h1>"), 1)

    def test_editorial_labels_and_football_link(self):
        output = web_ui_v3.render(self.state())
        self.assertIn('href="/football"', output)
        self.assertNotIn(">ANTI<", output)
        self.assertNotIn("· PASS", output)
        self.assertIn("<span class=mobile-prefix>Δ </span>", output)
        self.assertIn("<span class=mobile-prefix>@</span>", output)

    def test_ml_probabilities_are_joined_from_match_board(self):
        output = web_ui_v3.render(self.state())
        self.assertIn("Sharks победа", output)
        self.assertIn("48.4%", output)
        self.assertIn("41%", output)
        self.assertIn("+7.4", output)

    def test_board_is_sorted_and_grouped_by_date(self):
        output = web_ui_v3.render(self.state())
        self.assertGreaterEqual(output.count("class=date-band"), 2)
        self.assertLess(output.index("11 июля"), output.index("12 июля"))


if __name__ == "__main__":
    unittest.main()
