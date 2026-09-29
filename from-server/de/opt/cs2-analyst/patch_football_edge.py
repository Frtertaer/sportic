#!/usr/bin/env python3
"""Correct the football 'best market' to EDGE-over-bookmaker (the bot's own 7578-match Brier study),
not raw win-rate. Best = BTTS + Totals; 1X2/Double-Chance have no edge. Per match, headline the edge
market (Over 2.5 / BTTS) with the CALIBRATED true hit-rate; show the edge ranking + sweet-spots."""
import pathlib

# ================= web.py =================
w = pathlib.Path("/opt/cs2-analyst/web.py")
t = w.read_text(encoding="utf-8")
fn = '''def _football_markets() -> dict:
    """Where the model has a REAL edge over the bookmaker (Brier vs market on the bot's ~7500-match
    history) + the calibrated sweet-spots where reality beats the priced line. Win-rate alone lies
    (1X2 favourites win often but are priced fairly); edge is what actually pays."""
    import sqlite3
    try:
        c = sqlite3.connect("file:/opt/sportsbot/sportsbot.db?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        fam = [dict(r) for r in c.execute(
            "SELECT family,sample_size,trust_score FROM market_family_quality_profiles "
            "WHERE scope='GLOBAL' AND sample_size>=1000 ORDER BY trust_score DESC")]
        spots = [dict(r) for r in c.execute(
            "SELECT market,bucket_label,sample_size,avg_pred_pct,actual_hit_pct,delta_pct "
            "FROM numeric_recalibration_profiles WHERE scope='GLOBAL' AND sample_size>=300 "
            "AND delta_pct>0 ORDER BY delta_pct DESC LIMIT 5")]
        c.close()
    except Exception:  # noqa: BLE001
        return {}
    fn_ = {"btts": "Обе забьют (BTTS)", "totals": "Тоталы (ТБ/ТМ)", "1x2": "Исход 1X2"}
    mn_ = {"OVER_2_5": "ТБ 2.5 голов", "BTTS_YES": "Обе забьют", "BTTS_YES_HIGH_TOTALS": "Обе забьют · много голов",
           "BTTS_YES_MID_TOTALS": "Обе забьют · ср. тотал", "1X2_TOP_PICK": "Исход 1X2"}
    F = [{"name": fn_.get(r["family"], r["family"]), "n": r["sample_size"],
          "trust": round(r["trust_score"], 2), "edge": r["trust_score"] > 0} for r in fam]
    S = [{"market": mn_.get(r["market"], r["market"]), "bucket": r["bucket_label"], "n": r["sample_size"],
          "pred": round(r["avg_pred_pct"]), "real": round(r["actual_hit_pct"]), "delta": round(r["delta_pct"], 1)}
         for r in spots]
    return {"families": F, "spots": S}


def _football() -> dict:'''
assert t.count("def _football() -> dict:") == 1
t = t.replace("def _football() -> dict:", fn, 1)
assert t.count('"football": _football()') == 1
t = t.replace('"football": _football()', '"football": _football(), "football_markets": _football_markets()', 1)
w.write_text(t, encoding="utf-8")
print("web.py: _football_markets (edge) + state")

# ================= web_ui.py =================
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
s = u.read_text(encoding="utf-8")

# helper before render_football
helper = '''def _fedge(r: dict):
    """Model's real-EDGE pick for a match — Over 2.5 / BTTS (where it beats the book on ~7500 matches),
    with the CALIBRATED true hit-rate. 1X2/Double-Chance omitted: high win-rate but no edge."""
    po = r.get("prob_over_2_5") or 0
    pb = r.get("prob_btts_yes") or 0
    po = po * 100 if po <= 1.5 else po
    pb = pb * 100 if pb <= 1.5 else pb
    if po >= 52:
        real = 57 if po < 55 else (66 if po < 60 else 73)
        return (f'ТБ 2.5 голов · модель {po:.0f}% \\u2192 реально ~{real}%', True)
    if pb >= 55:
        real = 61 if pb < 60 else 69
        return (f'Обе забьют · модель {pb:.0f}% \\u2192 реально ~{real}%', True)
    if po and po <= 45:
        return (f'ТМ 2.5 голов · модель {100 - po:.0f}%', False)
    return ('нет явного края в сильных рынках', False)


def render_football(d: dict) -> str:'''
assert s.count("def render_football(d: dict) -> str:") == 1
s = s.replace("def render_football(d: dict) -> str:", helper, 1)

# _fm var after nval
s = s.replace(
    '    nval = sum(1 for r in pend if _parse_vb(r.get("value_bets")))\n',
    '    nval = sum(1 for r in pend if _parse_vb(r.get("value_bets")))\n'
    '    _fm = d.get("football_markets") or {}\n'
    '    _topfam = (_fm.get("families") or [{"name": "—"}])[0]["name"].split(" (")[0]\n', 1)

# hero: replace "Тотал 2.5" stat with "Топ-рынок (край)"
old_hero = "<div class=stat><div class=k>Тотал 2.5</div><div class=\"v {'pos' if po >= 55 else ''}\">{po}%</div><div class=s>по {no} матчам</div></div>"
new_hero = "<div class=stat><div class=k>Топ-рынок</div><div class=\"v pos\">{e(_topfam)}</div><div class=s>реальный край vs бук</div></div>"
assert s.count(old_hero) == 1
s = s.replace(old_hero, new_hero, 1)

# markets panels before "Предстоящие прогнозы"
anchor = "    P.append('<div class=sect>\\U0001f3af Предстоящие прогнозы</div>')\n"
panel = (
    "    if _fm.get(\"families\"):\n"
    "        P.append('<div class=sect>\\U0001f4ca Где у модели РЕАЛЬНЫЙ край над букмекером <span class=mut style=\"text-transform:none;letter-spacing:0;font-weight:400\">— по ~7500 матчам (винрейт врёт, край — нет)</span></div>')\n"
    "        P.append('<div class=card>')\n"
    "        for fam in _fm[\"families\"]:\n"
    "            cls = 'pos' if fam['edge'] else 'mut'\n"
    "            tag = ('\\u2713 есть край' if fam['edge'] else '\\u2717 нет края')\n"
    "            P.append(f'<div class=mrow><span class=team>{e(fam[\"name\"])}</span><b class={cls}>{tag} · {fam[\"trust\"]:+} · {fam[\"n\"]} матч.</b></div>')\n"
    "        P.append('</div>')\n"
    "    if _fm.get(\"spots\"):\n"
    "        P.append('<div class=sect>\\U0001f3af Где край реально заходит <span class=mut style=\"text-transform:none;letter-spacing:0;font-weight:400\">— маркет + зона уверенности модели</span></div>')\n"
    "        P.append('<div class=card>')\n"
    "        for sp in _fm[\"spots\"]:\n"
    "            P.append(f'''<div class=calib>\n"
    "<div class=lab><span><b>{e(sp[\"market\"])}</b> · модель {sp[\"bucket\"]}% · {sp[\"n\"]} матч.</span><span class=\"badge b-ok\">+{sp[\"delta\"]}pp края</span></div>\n"
    "<div class=bar><div class=fill style=\"width:{sp[\"real\"]}%\"></div><div class=mark style=\"left:{sp[\"pred\"]}%\"></div></div>\n"
    "<div class=lab><span class=mut>реально {sp[\"real\"]}% заходит</span><span class=mut>\\u25be бук ждёт ~{sp[\"pred\"]}%</span></div>\n"
    "</div><div style=\"height:10px\"></div>''')\n"
    "        P.append('</div>')\n"
)
assert s.count(anchor) == 1
s = s.replace(anchor, panel + anchor, 1)

# pending card: edge headline (compute eg before append, replace the 2 old lines)
s = s.replace(
    "        w1, dr, w2 = r.get(\"prob_w1\") or 0, r.get(\"prob_draw\") or 0, r.get(\"prob_w2\") or 0\n",
    "        w1, dr, w2 = r.get(\"prob_w1\") or 0, r.get(\"prob_draw\") or 0, r.get(\"prob_w2\") or 0\n"
    "        eg, eg_strong = _fedge(r)\n", 1)
old_pick = ('<div class=mrow><span class=mut>лучший исход</span><b>{_flean(r)}</b></div>\n'
            '<div class=mrow><span class=mut>тотал 2.5</span><span class=mut>{e(str(r.get("top_rec") or "—"))} · {conf:.0f}%</span></div>')
new_pick = '<div class=mrow><span class=mut>\\U0001f3af лучший рынок (край)</span><b class="{\'pos\' if eg_strong else \'mut\'}">{e(eg)}</b></div>'
assert s.count(old_pick) == 1, "pending pick lines not found (need clean .bak_fbmk base)"
s = s.replace(old_pick, new_pick, 1)

u.write_text(s, encoding="utf-8")
print("web_ui.py: edge markets panel + edge headline + hero")
