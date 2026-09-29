from __future__ import annotations

import copy
import importlib.util
import json
import re
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_module():
    spec = importlib.util.spec_from_file_location("web_ui_v2", ROOT / "web_ui_v2.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("renderer import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CountingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.starts = 0
        self.ends = 0

    def handle_starttag(self, _tag, _attrs):
        self.starts += 1

    def handle_endtag(self, _tag):
        self.ends += 1


class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = load_module()
        cls.fixture = json.loads((ROOT / "sample_state.json").read_text(encoding="utf-8"))

    def state(self):
        value = copy.deepcopy(self.fixture)
        value["ts"] = int(time.time())
        return value

    def test_full_fixture_keeps_all_sections(self):
        output = self.ui.render(self.state())
        for contract in (
            'id=signals',
            'id=matches',
            'id=upcoming',
            'id=market',
            'id=accuracy',
        ):
            self.assertIn(contract, output)
        self.assertEqual(output.count("class=board-card"), 5)
        self.assertEqual(output.count('class="card live-card"'), 2)

    def test_probability_bar_uses_real_percentage_width(self):
        output = self.ui.render(self.state())
        self.assertIn('style="--pa:50%"', output)
        self.assertNotIn('style="flex:50', output)

    def test_provider_markup_is_removed_closed_or_truncated(self):
        cases = (
            'grok: **GREEN: clean.**<grok:render card_id="1">hidden</grok:render>',
            'grok: **RED: roster issue**<grok:render type="render_inline_citation">truncated',
        )
        cleaned = [self.ui._clean_display(value) for value in cases]
        self.assertEqual(cleaned[0], "GREEN: clean.")
        self.assertEqual(cleaned[1], "RED: roster issue")

    def test_xss_payloads_are_escaped(self):
        state = self.state()
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        state["value"][0]["t1"] = payload
        state["prematch_board"][0]["news"] = "GREEN: " + payload
        output = self.ui.render(state)
        self.assertNotIn("<script>", output)
        self.assertNotIn("<img src=x", output)
        self.assertIn("&lt;script&gt;", output)

    def test_empty_state_renders(self):
        output = self.ui.render({"ts": int(time.time())})
        self.assertIn('id=signals', output)
        self.assertIn("class=empty", output)

    def test_zero_formats_as_zero(self):
        self.assertEqual(self.ui._fmt(0, 0), "0")
        self.assertEqual(self.ui._fmt(0, 1), "0")

    def test_board_value_is_measured_for_recommended_side(self):
        row = {
            "t1": "Favorite",
            "t2": "Underdog",
            "fav": "Favorite",
            "pf_fav": 0.65,
            "pm_fav": 0.55,
            "verdict": "VALUE",
            "bet_side": "Underdog",
            "bet_odd": 2.3,
        }
        output = self.ui._board_card(row)
        self.assertIn("Модель</span><b>45%", output)
        self.assertIn("Букмекер</span><b>35%", output)
        self.assertIn("+10.0 п.п.", output)

    def test_probability_is_clamped(self):
        state = self.state()
        first = state["live"][0]
        first["p_series"] = {first["team1"]: 1.8, first["team2"]: -0.8}
        output = self.ui.render(state)
        self.assertIn('style="--pa:100%"', output)

    def test_football_renderer_contract_survives(self):
        output = self.ui.render_football(self.state())
        self.assertIn('href="/football"', output)
        self.assertIn('<main id=main>', output)
        self.assertIn("</html>", output)

    def test_auto_refresh_contract_survives(self):
        self.assertIn('<meta http-equiv="refresh" content="30">', self.ui.render(self.state()))

    def test_full_output_has_no_provider_markup(self):
        output = self.ui.render(self.state())
        self.assertIsNone(re.search(r"grok:|<grok|render_inline_citation", output, re.IGNORECASE))

    def test_structured_news_and_unknown_status_are_fail_closed(self):
        red = self.ui._board_card({
            "t1": "A", "t2": "B", "news_flag": "RED", "news_reason": "stand-in confirmed",
        })
        unknown = self.ui._board_card({"t1": "A", "t2": "B", "news_flag": "UNKNOWN"})
        self.assertIn("Обнаружен риск", red)
        self.assertIn("stand-in confirmed", red)
        self.assertIn("Статус новостей неизвестен", unknown)
        self.assertNotIn("Новости проверены", unknown)

    def test_ml_signal_does_not_invent_probability_from_mixed_bases(self):
        output = self.ui._prematch_signal({
            "t1": "paiN", "t2": "Sharks", "side": "Sharks", "odd": 2.23,
            "edge": 7, "kind": "ml",
        })
        self.assertIn("Преимущество</span><b class=pos>+7.0 п.п.", output)
        self.assertIn("Вероятности</span><b>в карточке матча", output)
        self.assertNotIn("51.8%", output)

    def test_football_escapes_untrusted_fields_and_coerces_numbers(self):
        state = self.state()
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        state["football"]["n"] = payload
        state["football"]["pending"][0]["team1"] = payload
        state["football"]["pending"][0]["prob_w1"] = payload
        state["football_markets"]["families"][0].update({"name": payload, "trust": payload, "n": payload})
        state["football_markets"]["spots"][0].update({
            "market": payload, "bucket": payload, "n": payload, "delta": payload,
            "real": payload, "pred": payload,
        })
        output = self.ui.render_football(state)
        self.assertNotIn("<script>", output)
        self.assertNotIn("<img src=x", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn("</html>", output)

    def test_invalid_timestamp_is_not_reported_as_fresh(self):
        output = self.ui.render({"ts": float("nan")})
        self.assertIn("время обновления неизвестно", output)
        self.assertIn("Нужно обновление", output)

    def test_malformed_nested_state_degrades_without_crashing(self):
        state = {
            "ts": 10**1000,
            "live": ["bad", {"team1": "A", "team2": "B", "p_series": [], "round_total": []}],
            "value": [None, {"t1": "A", "t2": "B", "p": float("inf")}],
            "prematch_picks": [1, {"kind": "ml", "odd": "bad"}],
            "prematch_board": [{"antisweep": "bad", "news_flag": "UNKNOWN"}],
            "steam": ["bad"], "track": ["bad"], "roi": ["bad"],
            "football": {"acc": {"1x2": [1]}, "pending": [{"prob_w1": "50"}]},
            "football_markets": {"families": [{}], "spots": [{}]},
        }
        self.assertIn("</html>", self.ui.render(state))
        self.assertIn("</html>", self.ui.render_football(state))

    def test_html_is_parseable(self):
        parser = CountingParser()
        parser.feed(self.ui.render(self.state()))
        parser.close()
        self.assertGreater(parser.starts, 100)
        self.assertGreater(parser.ends, 40)


if __name__ == "__main__":
    unittest.main()
