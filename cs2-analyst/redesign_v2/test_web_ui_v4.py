from __future__ import annotations

import copy
import json
import time
import unittest
from pathlib import Path

import web_ui_v3
import web_ui_v4


ROOT = Path(__file__).resolve().parent


class V4RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / "sample_state.json").read_text(encoding="utf-8"))

    def state(self):
        value = copy.deepcopy(self.fixture)
        value["ts"] = int(time.time())
        return value

    def test_structure_and_data_match_v3(self):
        v3_output = web_ui_v3.render(self.state())
        v4_output = web_ui_v4.render(self.state())
        for marker in ("signal-row tone-", "class=match-row", "class=board-row", "class=date-band"):
            self.assertEqual(v4_output.count(marker), v3_output.count(marker))
        for anchor in ("id=main", "id=signals", "id=matches", "id=upcoming", "id=accuracy"):
            self.assertIn(anchor, v4_output)

    def test_atlantic_palette_and_semantic_roles_exist(self):
        head = web_ui_v4.CS2_HEAD_V4.lower()
        for token in ("--bg:#0b1724", "--brand:#e8864a", "--actual-color:#69a9cf", "--model:#63cfdb", "--market:#b5aacb",
                      "--green:#67c18f", "--red:#f07a76"):
            self.assertIn(token, head)
        self.assertIn(".c-model strong", head)
        self.assertIn(".c-market strong", head)
        self.assertIn(".match-row .prob span{display:none}", head)
        self.assertIn(".match-row .prob b{font-size:11px}", head)

    def test_no_ai_surface_effects_reintroduced(self):
        head = web_ui_v4.CS2_HEAD_V4.lower()
        for token in ("radial-gradient", "backdrop-filter", "box-shadow"):
            self.assertNotIn(token, head)
        self.assertNotIn("border-radius:999", head)

    def test_provider_markup_and_xss_remain_clean(self):
        state = self.state()
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        state["value"][0]["t1"] = payload
        state["prematch_board"][0]["news"] = "GREEN: " + payload
        output = web_ui_v4.render(state)
        self.assertNotIn("<script>", output)
        self.assertNotIn("<img src=x", output)
        self.assertNotIn("grok:", output.lower())
        self.assertIn("&lt;script&gt;", output)

    def test_football_content_is_unchanged_but_palette_is_unified(self):
        state = self.state()
        v3_output = web_ui_v3.render_football(state)
        v4_output = web_ui_v4.render_football(state)
        self.assertEqual(
            v4_output.replace(web_ui_v4.FOOTBALL_HEAD_V4, "", 1),
            v3_output.replace(web_ui_v3.base.HEAD, "", 1),
        )
        self.assertIn("--bg:#0b1724", v4_output.lower())
        self.assertIn("<title>Analyst — спортивная аналитика</title>", v4_output)
        self.assertIn('<meta name="theme-color" content="#0b1724">', v4_output)

    def test_input_is_not_mutated(self):
        state = self.state(); before = copy.deepcopy(state)
        web_ui_v4.render(state)
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
