#!/usr/bin/env python3
"""Populate the pre-match section with a FULL BOARD of every upcoming match the model evaluated
(not just the rare 7-15% 'value' hits). prematch_scan publishes prematch_board.json; the dashboard
shows every upcoming match with the model's read (fav%, model%, verdict, anti-sweep near-lock)."""
import pathlib

# ============ 1) prematch_scan.py: publish the board ============
ps = pathlib.Path("/opt/cs2-analyst/prematch_scan.py")
s = ps.read_text(encoding="utf-8")
o = s

s = s.replace("    reports = []\n", "    reports = []\n    board = []\n", 1)

as_anchor = ('        as_note = (f"  \U0001f6e1 анти-свип: {aslock[0]} берёт ≥1 карту {aslock[1]*100:.0f}% "\n'
             '                   f"(фора +1.5 карты — near-lock, проверь линию вручную)") if aslock[1] >= ANTISWEEP_P else ""\n')
board_append = as_anchor + (
    '        board.append({"t1": n1, "t2": n2, "bo": bo, "ml1": ml1, "ml2": ml2,\n'
    '                      "fav": (n1 if pf1 >= pf2 else n2), "pf_fav": round(pf_fav, 3),\n'
    '                      "pm_fav": round(pm_fav, 3), "edge": round(edge1, 3), "verdict": verdict,\n'
    '                      "bet_side": bet_side, "bet_odd": bet_odd,\n'
    '                      "antisweep": [aslock[0], round(aslock[1], 3)], "start_date": m.get("start_date")})\n')
assert s.count(as_anchor) == 1, "as_note anchor not found"
s = s.replace(as_anchor, board_append, 1)

scan_print = '    print(f"scanned {len(ups)} upcoming, flagged {len(reports)}")\n'
board_write = scan_print + (
    '    try:\n'
    '        (ROOT / "data" / "prematch_board.json").write_text(\n'
    '            json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "matches": board},\n'
    '                       ensure_ascii=False), encoding="utf-8")\n'
    '    except Exception:  # noqa: BLE001\n'
    '        pass\n')
assert s.count(scan_print) == 1, "scan print anchor not found"
s = s.replace(scan_print, board_write, 1)
assert s != o
ps.write_text(s, encoding="utf-8")
print("patched prematch_scan.py (publishes prematch_board.json)")

# ============ 2) web.py: _prematch_board() + wire into state ============
w = pathlib.Path("/opt/cs2-analyst/web.py")
t = w.read_text(encoding="utf-8")
board_fn = '''def _prematch_board() -> list:
    """Full board of upcoming matches the model evaluated (from prematch_scan) so the section is
    populated even when there is no strong value pick. Upcoming = start_date in the future."""
    import json as _json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    try:
        rows = _json.loads((ROOT / "data" / "prematch_board.json").read_text(encoding="utf-8")).get("matches", [])
    except Exception:
        return []
    out = []
    for r in rows:
        sd = r.get("start_date")
        if sd:
            try:
                if datetime.fromisoformat(str(sd).replace("Z", "+00:00")) <= now:
                    continue
            except Exception:
                pass
        out.append(r)
    order = {"VALUE": 0, "SUSPICIOUS": 1, "PASS": 2}
    out.sort(key=lambda r: (order.get(r.get("verdict"), 3), -((r.get("antisweep") or ["", 0])[1] or 0)))
    return out[:20]


def _track() -> dict:'''
assert t.count("def _track() -> dict:") == 1, "_track anchor missing"
t = t.replace("def _track() -> dict:", board_fn, 1)
t = t.replace('"prematch_picks": _prematch_picks(),',
              '"prematch_picks": _prematch_picks(), "prematch_board": _prematch_board(),', 1)
w.write_text(t, encoding="utf-8")
print("patched web.py (_prematch_board + state)")

# ============ 3) web_ui.py: render the board before 'Точность модели' ============
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
ui = u.read_text(encoding="utf-8")
anchor = "    P.append('<div class=sect>\\U0001f4ca Точность модели"
board_render = (
    "    board = d.get('prematch_board') or []\n"
    "    if board:\n"
    "        P.append('<div class=sect>\\U0001f4cb Ближайшие матчи · оценка модели</div>')\n"
    "        for r in board:\n"
    "            pf = round((r.get('pf_fav') or 0) * 100); pm = round((r.get('pm_fav') or 0) * 100)\n"
    "            asw = r.get('antisweep') or ['', 0]; aswp = round((asw[1] or 0) * 100)\n"
    "            vd = r.get('verdict')\n"
    "            badge = ('<span class=\"badge b-val\">\\u2705 value</span>' if vd == 'VALUE'\n"
    "                     else '<span class=\"badge b-warn\">\\u26a0\\ufe0f странный</span>' if vd == 'SUSPICIOUS' else '')\n"
    "            betline = (f'<div class=pick><b class=pos>ставка: {e(str(r.get(\"bet_side\")))} @ {r.get(\"bet_odd\")}</b></div>'\n"
    "                       if r.get('bet_side') else '')\n"
    "            aline = (f'<div class=mrow><span class=mut>\\U0001f6e1 {e(str(asw[0]))} берёт \\u22651 карту</span><b class=pos>{aswp}%</b></div>'\n"
    "                     if aswp >= 78 else '')\n"
    "            P.append(f'''<div class=\"card{' val' if vd == 'VALUE' else ''}\">\n"
    "<div class=mrow><span class=team>{e(str(r.get('t1')))} vs {e(str(r.get('t2')))}</span>{badge}</div>\n"
    "<div class=mrow><span class=mut>фаворит: {e(str(r.get('fav')))}</span><b>бук {pf}% · модель {pm}%</b></div>\n"
    "{betline}{aline}</div>''')\n"
)
assert ui.count(anchor) == 1, "track sect anchor not found in web_ui"
ui = ui.replace(anchor, board_render + anchor, 1)
u.write_text(ui, encoding="utf-8")
print("patched web_ui.py (board render)")
