#!/usr/bin/env python3
"""Phase 3: dashboard card for winline line-movement steams (live actionable + follow-the-steam track)."""
import pathlib

# ---------- web.py: _steam() + state ----------
w = pathlib.Path("/opt/cs2-analyst/web.py")
t = w.read_text(encoding="utf-8")
fn = '''def _steam() -> dict:
    """Live winline line-movement steams still on the board (bettable now) + the follow-the-steam
    win-rate track. Reads line_steam_log.jsonl / line_steam_results.jsonl / winline_odds.json."""
    import re, json as _json
    log = _read_jsonl(ROOT / "data" / "line_steam_log.jsonl")
    res = _read_jsonl(ROOT / "data" / "line_steam_results.jsonl")
    def nrm(s):
        return re.sub(r"[^a-z0-9]", "", str(s or "").lower())
    try:
        board = {frozenset((nrm(r.get("t1")), nrm(r.get("t2"))))
                 for r in _json.loads((ROOT / "data" / "winline_odds.json").read_text(encoding="utf-8"))}
    except Exception:
        board = set()
    resolved = {f"{r.get('t1')}|{r.get('t2')}|{r.get('logged_at')}" for r in res}
    live, seen = [], set()
    for b in reversed(log):
        if f"{b.get('t1')}|{b.get('t2')}|{b.get('logged_at')}" in resolved:
            continue
        pair = frozenset((nrm(b.get("t1")), nrm(b.get("t2"))))
        if pair not in board or pair in seen:
            continue
        seen.add(pair)
        live.append({"t1": b.get("t1"), "t2": b.get("t2"), "side": b.get("side"), "entry": b.get("entry"),
                     "open": b.get("open"), "strength": round((b.get("strength") or 0) * 100),
                     "mode": b.get("mode", "STEAM"), "news": b.get("news", "")})
    n = len(res); wn = sum(1 for r in res if r.get("outcome") == "win")
    pl = sum(r.get("pl_units", 0) for r in res)
    track = {"n": n, "wins": wn, "winrate": round(100 * wn / n) if n else 0,
             "roi": round(100 * pl / n, 1) if n else 0, "pending": max(0, len(log) - n)}
    return {"live": live[:8], "track": track}


def _track() -> dict:'''
assert t.count("def _track() -> dict:") == 1
t = t.replace("def _track() -> dict:", fn, 1)
assert t.count('"livelag": _livelag(),') == 1
t = t.replace('"livelag": _livelag(),', '"livelag": _livelag(), "steam": _steam(),', 1)
w.write_text(t, encoding="utf-8")
print("web.py: _steam + state")

# ---------- web_ui.py: steam card in render() before 'Ставить сейчас' ----------
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
s = u.read_text(encoding="utf-8")
anchor = "    P.append('<div class=sect>\\U0001f3af Ставить сейчас</div>')\n"
card = (
    "    st = d.get('steam') or {}\n"
    "    stl = st.get('live') or []\n"
    "    tr_ = st.get('track') or {}\n"
    "    P.append('<div class=sect>\\U0001f525 Движение линии winline <span class=mut style=\"text-transform:none;letter-spacing:0;font-weight:400\">— стим/договорняки по всему борду</span></div>')\n"
    "    if tr_.get('n'):\n"
    "        P.append(f'<div class=card><div class=mrow><span class=mut>трек «следуй за стимом»</span><b class=\"{\"pos\" if tr_[\"roi\"] > 0 else \"\"}\">{tr_[\"wins\"]}/{tr_[\"n\"]} = {tr_[\"winrate\"]}% · ROI {tr_[\"roi\"]:+}% · {tr_[\"pending\"]} ждут</b></div></div>')\n"
    "    else:\n"
    "        P.append(f'<div class=card><span class=mut>трек копится — стимов сыграно 0, ждут {tr_.get(\"pending\", 0)}</span></div>')\n"
    "    if not stl:\n"
    "        P.append('<div class=empty>Активных стимов сейчас нет — появятся здесь и в Telegram</div>')\n"
    "    for sm in stl:\n"
    "        tagl = '\\U0001f680 ранний' if sm['mode'] == 'EARLY' else '\\U0001f525 стим'\n"
    "        newsl = (f'<div class=mrow><span class=mut>\\U0001f4f0</span><span class=mut>{e(str(sm[\"news\"])[:90])}</span></div>' if sm.get('news') else '')\n"
    "        P.append(f'''<div class=\"card val\">\n"
    "<div class=mrow><span class=team>{e(str(sm[\"t1\"]))} vs {e(str(sm[\"t2\"]))}</span><span class=\"badge b-val\">{tagl} · {sm[\"strength\"]}%</span></div>\n"
    "<div class=pick><b class=pos>ставь {e(str(sm[\"side\"]))} @ {sm[\"entry\"]}</b><span class=mut>двинули {sm[\"open\"]} \\u2192 {sm[\"entry\"]}</span></div>\n"
    "{newsl}</div>''')\n"
)
assert s.count(anchor) == 1, "Ставить сейчас anchor not found"
s = s.replace(anchor, card + anchor, 1)
u.write_text(s, encoding="utf-8")
print("web_ui.py: steam card in render()")
