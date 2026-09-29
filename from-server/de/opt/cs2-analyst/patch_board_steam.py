#!/usr/bin/env python3
"""Прокачка борда: cross-confirm each model value pick with the LINE-MOVEMENT (steam) signal.
Model value + money steaming to the SAME side = power pick; steaming AGAINST = caution."""
import pathlib

# A) line_anomaly.py: movement_map()
la = pathlib.Path("/opt/cs2-analyst/line_anomaly.py")
s = la.read_text(encoding="utf-8")
mm = '''def movement_map():
    """{frozenset(norm t1, norm t2): {side,type,strength}} for directional moves (STEAM/EARLY/REVERSE),
    so other tools can cross-check a model pick against where the market money actually went."""
    out = {}
    for r in scan():
        if r["type"] in ("STEAM", "EARLY", "REVERSE") and r["side"]:
            out[frozenset((_norm(r["t1"]), _norm(r["t2"])))] = {
                "side": r["side"], "type": r["type"], "strength": r["strength"]}
    return out


if __name__ == "__main__":'''
assert s.count('if __name__ == "__main__":') == 1
la.write_text(s.replace('if __name__ == "__main__":', mm, 1), encoding="utf-8")
print("line_anomaly.py: movement_map()")

# B) prematch_board.py: overlay line movement onto each rec
pb = pathlib.Path("/opt/cs2-analyst/prematch_board.py")
t = pb.read_text(encoding="utf-8")
t = t.replace("import winline_odds              # noqa: E402\n",
              "import winline_odds              # noqa: E402\nimport line_anomaly as la       # noqa: E402\n", 1)
t = t.replace("    board = []\n    for m in ups:\n",
              "    board = []\n    try:\n        mm = la.movement_map()\n    except Exception:\n        mm = {}\n    for m in ups:\n", 1)
t = t.replace(
    "        board.append(rec)\n",
    "        mv = mm.get(frozenset((la._norm(n1), la._norm(n2))))\n"
    "        if mv:\n"
    "            model_side = rec.get(\"bet_side\") or rec[\"fav\"]\n"
    "            rec[\"line\"] = {\"dir\": \"confirm\" if la._norm(mv[\"side\"]) == la._norm(model_side) else \"against\",\n"
    "                           \"side\": mv[\"side\"], \"type\": mv[\"type\"]}\n"
    "        board.append(rec)\n", 1)
assert "import line_anomaly as la" in t and "mm = la.movement_map()" in t and 'rec["line"]' in t
pb.write_text(t, encoding="utf-8")
print("prematch_board.py: line overlay")

# C) web_ui.py: confirmation badge in the board card
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
w = u.read_text(encoding="utf-8")
anchor = "                     if aswp >= 78 else '')\n"
insert = (anchor +
          "            ln = r.get('line'); lineb = ''\n"
          "            if ln and ln.get('dir') == 'confirm':\n"
          "                lineb = f'<div class=mrow><span class=mut>\\U0001f525 рынок подтверждает</span><b class=pos>деньги тоже на {e(str(ln[\"side\"]))}</b></div>'\n"
          "            elif ln and ln.get('dir') == 'against':\n"
          "                lineb = f'<div class=mrow><span class=mut>\\u26a0\\ufe0f рынок против</span><b style=\"color:#ffc857\">деньги на {e(str(ln[\"side\"]))} — осторожно</b></div>'\n")
assert w.count(anchor) == 1, "aline anchor not found"
w = w.replace(anchor, insert, 1)
assert w.count("{betline}{aline}</div>'''") == 1
w = w.replace("{betline}{aline}</div>'''", "{betline}{aline}{lineb}</div>'''", 1)
u.write_text(w, encoding="utf-8")
print("web_ui.py: confirmation badge")
