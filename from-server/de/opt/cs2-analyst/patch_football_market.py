#!/usr/bin/env python3
"""Football: switch to the best-winrate market. Headline DOUBLE CHANCE (83% on data) with DNB as the
higher-odds secondary; drop the Over-2.5 headline (43% = broken per the council). Add a live
per-market winrate tracker panel so the ranking sharpens as more matches resolve (need ~150-300)."""
import pathlib, re

# ================= web.py: _football_markets() + state wire =================
w = pathlib.Path("/opt/cs2-analyst/web.py")
t = w.read_text(encoding="utf-8")
fn = '''def _football_markets() -> list:
    """Live per-market hit-rate on RESOLVED predictions — 'which market actually wins'. Feeds the
    dashboard ranking so we gather data on the best markets over time (target ~150-300 resolved)."""
    import sqlite3
    try:
        c = sqlite3.connect("file:/opt/sportsbot/sportsbot.db?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(
            "SELECT prob_w1,prob_draw,prob_w2,prob_over_2_5,prob_btts_yes,actual_result,"
            "actual_goals_home,actual_goals_away FROM predictions")]
        c.close()
    except Exception:  # noqa: BLE001
        return []
    def sc(v):
        v = v or 0
        return v * 100 if v <= 1.5 else v
    res = [r for r in rows if r.get("actual_result") in ("W1", "DRAW", "W2")
           and r.get("actual_goals_home") is not None and r.get("actual_goals_away") is not None]
    M = {}
    for r in res:
        w1, dr, w2 = sc(r["prob_w1"]), sc(r["prob_draw"]), sc(r["prob_w2"])
        gh, ga = r["actual_goals_home"] or 0, r["actual_goals_away"] or 0
        ar = r["actual_result"]
        order = sorted([("W1", w1), ("DRAW", dr), ("W2", w2)], key=lambda x: -x[1])
        dc = {order[0][0], order[1][0]}
        M.setdefault("Двойной шанс", []).append(ar in dc)
        if ar != "DRAW":
            M.setdefault("DNB (фаворит)", []).append(("W1" if w1 >= w2 else "W2") == ar)
        M.setdefault("Обе забьют", []).append((sc(r["prob_btts_yes"]) >= 50) == (gh >= 1 and ga >= 1))
        pk = "W1" if w1 >= dr and w1 >= w2 else ("DRAW" if dr >= w2 else "W2")
        M.setdefault("Исход 1X2", []).append(pk == ar)
        M.setdefault("Тотал 2.5", []).append((sc(r["prob_over_2_5"]) >= 50) == (gh + ga >= 3))
    out = []
    for name, h in M.items():
        n = len(h); wn = sum(1 for x in h if x)
        out.append({"name": name, "n": n, "win": round(100 * wn / n) if n else 0})
    out.sort(key=lambda x: -x["win"])
    return out


def _football() -> dict:'''
assert t.count("def _football() -> dict:") == 1, "_football anchor missing"
t = t.replace("def _football() -> dict:", fn, 1)
assert t.count('"football": _football()') == 1, "football state wire missing"
t = t.replace('"football": _football()', '"football": _football(), "football_markets": _football_markets()', 1)
w.write_text(t, encoding="utf-8")
print("patched web.py (_football_markets + state)")

# ================= web_ui.py =================
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
s = u.read_text(encoding="utf-8")

# (a) helpers before render_football
helpers = '''def _fdc(r: dict) -> str:
    """Double Chance — the two most-likely of 1X2 (the model's BEST market, ~83%). It covers the
    model's real weakness: it often picks the wrong one of its top two. Returns '1X (Team или ничья) · 79%'."""
    w1, dr, w2 = r.get("prob_w1") or 0, r.get("prob_draw") or 0, r.get("prob_w2") or 0
    t1, t2 = html.escape(str(r.get("team1", "?"))), html.escape(str(r.get("team2", "?")))
    order = sorted([("W1", w1), ("X", dr), ("W2", w2)], key=lambda x: -x[1])
    if order[0][1] <= 0:
        return "\\u2014"
    codes = {order[0][0], order[1][0]}
    comb = order[0][1] + order[1][1]
    if codes == {"W1", "X"}:
        lab = "1X (" + t1 + " или ничья)"
    elif codes == {"W2", "X"}:
        lab = "X2 (ничья или " + t2 + ")"
    else:
        lab = "12 (" + t1 + " или " + t2 + ")"
    return f'{lab} · {comb:.0f}%'


def _fdnb(r: dict) -> str:
    """Draw-No-Bet on the stronger team (higher odds ~1.55+, ~64%). Push on draw."""
    w1, w2 = r.get("prob_w1") or 0, r.get("prob_w2") or 0
    if max(w1, w2) <= 0:
        return "\\u2014"
    if w1 >= w2:
        return f'П1 · {html.escape(str(r.get("team1", "?")))} · {w1:.0f}% (ничья=возврат)'
    return f'П2 · {html.escape(str(r.get("team2", "?")))} · {w2:.0f}% (ничья=возврат)'


def render_football(d: dict) -> str:'''
assert s.count("def render_football(d: dict) -> str:") == 1
s = s.replace("def render_football(d: dict) -> str:", helpers, 1)

# (b) dcwin var after nval
s = s.replace(
    '    nval = sum(1 for r in pend if _parse_vb(r.get("value_bets")))\n',
    '    nval = sum(1 for r in pend if _parse_vb(r.get("value_bets")))\n'
    '    _fm = d.get("football_markets") or []\n'
    '    dcwin = next((m["win"] for m in _fm if m["name"] == "Двойной шанс"), 0)\n', 1)

# (c) hero: Тотал 2.5 stat -> Двойной шанс
old_hero = "<div class=stat><div class=k>Тотал 2.5</div><div class=\"v {'pos' if po >= 55 else ''}\">{po}%</div><div class=s>по {no} матчам</div></div>"
new_hero = "<div class=stat><div class=k>Двойной шанс</div><div class=\"v {'pos' if dcwin >= 60 else ''}\">{dcwin}%</div><div class=s>лучший рынок</div></div>"
assert s.count(old_hero) == 1, "hero total stat not found"
s = s.replace(old_hero, new_hero, 1)

# (d) markets panel before "Предстоящие прогнозы"
anchor = "    P.append('<div class=sect>\\U0001f3af Предстоящие прогнозы</div>')\n"
panel = (
    "    if _fm:\n"
    "        P.append('<div class=sect>\\U0001f4ca Маркеты по винрейту <span class=mut style=\"text-transform:none;letter-spacing:0;font-weight:400\">— какой рынок реально заходит (по сыгранным)</span></div>')\n"
    "        P.append('<div class=card>')\n"
    "        for i, m in enumerate(_fm):\n"
    "            bdg = '<span class=\"badge b-ok\">\\U0001f947 лучший</span>' if i == 0 else ''\n"
    "            P.append(f'''<div class=calib>\n"
    "<div class=lab><span><b>{e(m[\"name\"])}</b> · {m[\"n\"]} матчей</span>{bdg}</div>\n"
    "<div class=bar><div class=fill style=\"width:{m[\"win\"]}%\"></div></div>\n"
    "<div class=lab><span class=mut>{m[\"win\"]}% заходит</span></div>\n"
    "</div><div style=\"height:10px\"></div>''')\n"
    "        P.append('</div>')\n"
)
assert s.count(anchor) == 1, "predstoyashchie anchor not found"
s = s.replace(anchor, panel + anchor, 1)

# (e) pending card: DC primary + DNB secondary (drop O/U headline)
old_pick = ('<div class=mrow><span class=mut>лучший исход</span><b>{_flean(r)}</b></div>\n'
            '<div class=mrow><span class=mut>тотал 2.5</span><span class=mut>{e(str(r.get("top_rec") or "—"))} · {conf:.0f}%</span></div>')
new_pick = ('<div class=mrow><span class=mut>\\U0001f947 двойной шанс</span><b class=pos>{_fdc(r)}</b></div>\n'
            '<div class=mrow><span class=mut>DNB · выше кэф</span><b>{_fdnb(r)}</b></div>')
assert s.count(old_pick) == 1, "pending pick lines not found"
s = s.replace(old_pick, new_pick, 1)

u.write_text(s, encoding="utf-8")
print("patched web_ui.py (DC headline + DNB + markets panel)")
