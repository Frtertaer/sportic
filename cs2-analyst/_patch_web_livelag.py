import shutil, os

W = "/opt/cs2-analyst/web.py"
s = open(W, encoding="utf-8").read()
if not os.path.exists(W + ".bak_livelag"):
    shutil.copy(W, W + ".bak_livelag")

# 1) add _livelag() before _roi()
if "def _livelag(" not in s:
    func = (
        'def _livelag() -> dict:\n'
        '    res = _read_jsonl(ROOT / "data" / "livelag_ft_results.jsonl")\n'
        '    n = len(res)\n'
        '    wins = sum(1 for r in res if r.get("outcome") == "win")\n'
        '    pl = sum(float(r.get("pl_units", 0)) for r in res)\n'
        '    pend = len(_read_jsonl(ROOT / "data" / "livelag_ft_log.jsonl")) - n\n'
        '    return {"n": n, "wins": wins, "winrate": round(wins / n * 100) if n else 0,\n'
        '            "pl": round(pl, 2), "roi": round(pl / n * 100, 1) if n else 0, "pending": max(0, pend)}\n\n\n'
    )
    s = s.replace("def _roi() -> dict:", func + "def _roi() -> dict:", 1)

# 2) add to the data dict
if '"livelag": _livelag()' not in s:
    s = s.replace('"roi": _roi(),', '"roi": _roi(), "livelag": _livelag(),', 1)

# 3) render card after the totals-value ROI card
if "LIVE lag-value" not in s:
    anchor = ('<div class="roi {cls}">ROI {r[\'roi\']:+}%</div><div class=muted>{r[\'pl\']:+} ед.</div></div>""")')
    card = (anchor + '\n\n    lg = d["livelag"]\n'
            '    lcls = "pos" if lg["roi"] >= 0 else "neg"\n'
            '    parts.append(f"""<h2>⚡ LIVE lag-value (лаг live-линии)</h2><div class=card>\n'
            '<div class=row><span>ставок: {lg[\'n\']} · винрейт <b>{lg[\'winrate\']}%</b> · ждут: {lg[\'pending\']}</span></div>\n'
            '<div class="roi {lcls}">ROI {lg[\'roi\']:+}%</div>'
            '<div class=muted>{lg[\'pl\']:+} ед. · near-certain live (P серии ≥85%, край ≥8%)</div></div>""")')
    assert anchor in s, "ROI render anchor not found"
    s = s.replace(anchor, card, 1)

open(W, "w", encoding="utf-8").write(s)
print("web.py: live-lag forward-test section added")
